-- manga-cli 0.8.3 Android/Termux reader profile.
-- Target stack: Termux + Termux:X11 + mpv-x.
-- No external Lua modules. Desktop reader.lua remains untouched.

local mp = require 'mp'
local utils = require 'mp.utils'

local control_path = os.getenv('ACMANGA_READER_STATE')
local current = {page=0, total=0, chapter_key='', label='', loaded=false}
local busy, quitting, seq = false, false, 0
local action, load_error = '', ''
local cooldown = 0
local show_indicator = true
local deferred = nil
local pending_tap = nil
local ow_seen, oh_seen = 0, 0
local tap_guard_until = 0
local persist_fit = 'page'
local double_tap_scale = 2.0

-- Mobile UX constants, deliberately small and boring: predictable beats clever.
local DOUBLE_TAP_DELAY = 0.30
local DRAG_THRESHOLD = 9
local LEFT_ZONE = 0.35
local RIGHT_ZONE = 0.65

local drag = {
    down=false,
    moved=false,
    start_x=0,
    start_y=0,
    old_align_x=0,
    old_align_y=0,
    dims=nil,
}

local function clamp(n, low, high)
    return math.max(low, math.min(high, tonumber(n) or low))
end

local function json_escape(value)
    local map = {['"']='\\"', ['\\']='\\\\', ['\b']='\\b', ['\f']='\\f',
                 ['\n']='\\n', ['\r']='\\r', ['\t']='\\t'}
    return '"' .. tostring(value):gsub('[%z\1-\31\\"]', function(c)
        return map[c] or string.format('\\u%04x', string.byte(c))
    end) .. '"'
end

local function json_encode(value, seen)
    local kind = type(value)
    if kind == 'nil' then return 'null' end
    if kind == 'boolean' then return value and 'true' or 'false' end
    if kind == 'number' then
        if value ~= value or value == math.huge or value == -math.huge then return 'null' end
        return tostring(value)
    end
    if kind == 'string' then return json_escape(value) end
    if kind ~= 'table' then return 'null' end
    seen = seen or {}
    if seen[value] then return 'null' end
    seen[value] = true
    local is_array, highest, count = true, 0, 0
    for key in pairs(value) do
        if type(key) ~= 'number' or key < 1 or key % 1 ~= 0 then
            is_array = false; break
        end
        highest = math.max(highest, key); count = count + 1
    end
    local out = {}
    if is_array and highest == count then
        for i=1,highest do out[#out+1] = json_encode(value[i], seen) end
        seen[value] = nil
        return '[' .. table.concat(out, ',') .. ']'
    end
    local keys = {}
    for key in pairs(value) do keys[#keys+1] = tostring(key) end
    table.sort(keys)
    for _,key in ipairs(keys) do
        out[#out+1] = json_escape(key) .. ':' .. json_encode(value[key], seen)
    end
    seen[value] = nil
    return '{' .. table.concat(out, ',') .. '}'
end

local function current_view()
    -- Mobile zoom/pan is deliberately transient. Persist only chapter/page and
    -- preserve the reader mode that desktop had stored for the same manga.
    -- This prevents a phone session from making Debian reopen a title zoomed.
    return {position=0, horizontal=0, zoom=0, fit=persist_fit}
end

local function publish(now)
    if not control_path then return end
    if not now then
        if deferred then return end
        deferred = mp.add_timeout(0.08, function() deferred=nil; publish(true) end)
        return
    end
    if deferred then deferred:kill(); deferred=nil end
    local data = {
        seq=seq, action=action, page=current.page, total=current.total,
        chapter_key=current.chapter_key, loaded=current.loaded, quit=quitting,
        error=load_error, busy=busy, view=current_view(),
    }
    local encoded = nil
    if type(utils.format_json) == 'function' then
        local ok, result = pcall(utils.format_json, data)
        if ok and type(result) == 'string' then encoded = result end
    end
    if not encoded then encoded = json_encode(data) end
    local f = io.open(control_path .. '.tmp', 'w')
    if f then
        local ok = pcall(function() f:write(encoded) end)
        f:close()
        if ok then os.rename(control_path .. '.tmp', control_path) end
    end
end

local function dimensions_ready()
    local d = mp.get_property_native('osd-dimensions') or {}
    local v = mp.get_property_native('video-params') or {}
    local ow, oh = tonumber(d.w) or 0, tonumber(d.h) or 0
    local vw, vh = tonumber(v.dw or v.w) or 0, tonumber(v.dh or v.h) or 0
    return ow > 0 and oh > 0 and vw > 0 and vh > 0, d
end

local function cancel_pending_tap()
    if pending_tap then
        pending_tap:kill()
        pending_tap = nil
    end
end

local function reset_view(show)
    mp.set_property_number('video-zoom', 0)
    mp.set_property_number('video-pan-x', 0)
    mp.set_property_number('video-pan-y', 0)
    mp.set_property_number('video-align-x', 0)
    mp.set_property_number('video-align-y', 0)
    publish(false)
    if show then mp.osd_message('Fit to screen', 0.8) end
end

local function is_zoomed()
    return (tonumber(mp.get_property_native('video-zoom')) or 0) > 0.05
end

-- Cursor-centric zoom using mpv's public video-zoom/video-align coordinate model.
-- It keeps the part of the page under the finger in the same screen position.
local function zoom_at(x, y)
    if not current.loaded or busy then return end
    if is_zoomed() then
        reset_view(false)
        mp.osd_message('1x', 0.55)
        return
    end

    local dims = mp.get_property_native('osd-dimensions') or {}
    local w, h = tonumber(dims.w) or 0, tonumber(dims.h) or 0
    if w <= 0 or h <= 0 then return end
    x = clamp(x or w/2, 0, w)
    y = clamp(y or h/2, 0, h)

    local amount = math.log(double_tap_scale) / math.log(2)
    local scale = 2 ^ amount
    local visible_w = (w - (tonumber(dims.ml) or 0) - (tonumber(dims.mr) or 0)) * scale
    local visible_h = (h - (tonumber(dims.mt) or 0) - (tonumber(dims.mb) or 0)) * scale
    local pan_x = tonumber(mp.get_property_native('video-pan-x')) or 0
    local pan_y = tonumber(mp.get_property_native('video-pan-y')) or 0

    local old_cursor_ml = (tonumber(dims.ml) or 0) - x
    local ml = old_cursor_ml * scale + x
    local denom_x = w - visible_w
    local align_x = 0
    if math.abs(denom_x) > 0.001 then
        align_x = 2 * (ml - pan_x * visible_w) / denom_x - 1
    end

    local old_cursor_mt = (tonumber(dims.mt) or 0) - y
    local mt = old_cursor_mt * scale + y
    local denom_y = h - visible_h
    local align_y = 0
    if math.abs(denom_y) > 0.001 then
        align_y = 2 * (mt - pan_y * visible_h) / denom_y - 1
    end

    mp.set_property_number('video-zoom', amount)
    mp.set_property_number('video-align-x', clamp(align_x, -1, 1))
    mp.set_property_number('video-align-y', clamp(align_y, -1, 1))
    publish(false)
    mp.osd_message(string.format('%.1fx', double_tap_scale), 0.55)
end

local function status(force)
    if not force and not show_indicator then return end
    if not current.loaded and not force then return end
    local zoom = is_zoomed() and string.format('%.1fx', double_tap_scale) or 'fit'
    mp.osd_message(string.format('%s  |  %d/%d  |  %s', current.label or 'manga-cli',
        current.page or 0, current.total or 0, zoom), 1.25)
end

local function request(direction)
    if quitting or busy or not current.loaded then return end
    cancel_pending_tap()
    busy = true
    cooldown = mp.get_time() + 0.16
    seq = seq + 1
    action = direction > 0 and 'next' or 'previous'
    publish(true)
end

local function single_tap(x, y)
    if quitting or busy or not current.loaded then return end
    if mp.get_time() < cooldown then return end
    if is_zoomed() then
        -- While inspecting a zoomed page, a stray tap must never turn the page.
        status(true)
        return
    end
    local d = mp.get_property_native('osd-dimensions') or {}
    local w = tonumber(d.w) or 0
    if w <= 0 then return end
    local ratio = clamp((x or w/2) / w, 0, 1)
    if ratio < LEFT_ZONE then
        request(-1)
    elseif ratio > RIGHT_ZONE then
        request(1)
    else
        status(true)
    end
end

local function schedule_single_tap(x, y)
    cancel_pending_tap()
    pending_tap = mp.add_timeout(DOUBLE_TAP_DELAY, function()
        pending_tap = nil
        single_tap(x, y)
    end)
end

local function pointer_pos()
    -- Newer mpv builds can expose native touch points. Termux:X11 normally
    -- supplies emulated mouse input, so keep that as a reliable fallback.
    local touches = mp.get_property_native('touch-pos') or {}
    if type(touches) == 'table' and touches[1] then
        local x, y, count = 0, 0, 0
        for _,p in pairs(touches) do
            if type(p) == 'table' and tonumber(p.x) and tonumber(p.y) then
                x, y, count = x + tonumber(p.x), y + tonumber(p.y), count + 1
            end
        end
        if count > 0 then return x / count, y / count end
    end
    local p = mp.get_property_native('mouse-pos') or {}
    return tonumber(p.x) or 0, tonumber(p.y) or 0
end

local function stop_drag_binding()
    mp.remove_key_binding('android-drag-move')
    drag.down = false
end

local function start_drag()
    drag.down = true
    drag.moved = false
    drag.start_x, drag.start_y = pointer_pos()
    drag.old_align_x = tonumber(mp.get_property_native('video-align-x')) or 0
    drag.old_align_y = tonumber(mp.get_property_native('video-align-y')) or 0
    drag.dims = mp.get_property_native('osd-dimensions') or {}

    mp.add_forced_key_binding('MOUSE_MOVE', 'android-drag-move', function()
        if not drag.down or not is_zoomed() then return end
        local x, y = pointer_pos()
        local dx, dy = x - drag.start_x, y - drag.start_y
        if not drag.moved and math.sqrt(dx*dx + dy*dy) < DRAG_THRESHOLD then return end
        drag.moved = true
        local dims = drag.dims or {}
        local overflow_x = (tonumber(dims.ml) or 0) + (tonumber(dims.mr) or 0)
        local overflow_y = (tonumber(dims.mt) or 0) + (tonumber(dims.mb) or 0)
        if math.abs(overflow_x) > 0.001 then
            local align_x = drag.old_align_x + 2 * dx / overflow_x
            mp.set_property_number('video-align-x', clamp(align_x, -1, 1))
        end
        if math.abs(overflow_y) > 0.001 then
            local align_y = drag.old_align_y + 2 * dy / overflow_y
            mp.set_property_number('video-align-y', clamp(align_y, -1, 1))
        end
        publish(false)
    end)
end

mp.add_forced_key_binding('MBTN_LEFT', 'android-touch', function(e)
    e = e or {event='press'}
    local event = e.event or 'press'
    if event == 'down' then
        start_drag()
        return
    end
    if event == 'up' then
        local x, y = pointer_pos()
        local moved = drag.moved
        stop_drag_binding()
        if not moved and mp.get_time() >= tap_guard_until then schedule_single_tap(x, y) end
        return
    end
    if event == 'press' then
        if mp.get_time() < tap_guard_until then return end
        local x, y = pointer_pos()
        schedule_single_tap(x, y)
    end
end, {complex=true})

mp.add_forced_key_binding('MBTN_LEFT_DBL', 'android-double-tap', function()
    cancel_pending_tap()
    stop_drag_binding()
    -- Some X11 stacks still emit the second button-up after the DBL event.
    -- Ignore it briefly so a zoom never turns the page 300 ms later.
    tap_guard_until = mp.get_time() + 0.22
    local x, y = pointer_pos()
    zoom_at(x, y)
end)

-- Keyboard/mouse fallbacks make the Android profile easy to debug on desktop X11.
mp.add_forced_key_binding('LEFT', 'android-prev', function() request(-1) end)
mp.add_forced_key_binding('RIGHT', 'android-next', function() request(1) end)
mp.add_forced_key_binding('ENTER', 'android-next-enter', function() request(1) end)
mp.add_forced_key_binding('MBTN_RIGHT', 'android-prev-mouse', function() request(-1) end)
mp.add_forced_key_binding('WHEEL_UP', 'android-prev-wheel', function() if not is_zoomed() then request(-1) end end)
mp.add_forced_key_binding('WHEEL_DOWN', 'android-next-wheel', function() if not is_zoomed() then request(1) end end)
mp.add_forced_key_binding('+', 'android-zoom-center', function()
    local d = mp.get_property_native('osd-dimensions') or {}
    zoom_at((tonumber(d.w) or 0)/2, (tonumber(d.h) or 0)/2)
end)
mp.add_forced_key_binding('=', 'android-zoom-center-eq', function()
    local d = mp.get_property_native('osd-dimensions') or {}
    zoom_at((tonumber(d.w) or 0)/2, (tonumber(d.h) or 0)/2)
end)
mp.add_forced_key_binding('-', 'android-reset-minus', function() reset_view(true) end)
mp.add_forced_key_binding('r', 'android-reset', function() reset_view(true) end)
mp.add_forced_key_binding('0', 'android-reset-zero', function() reset_view(true) end)
mp.add_forced_key_binding('TAB', 'android-status', function() status(true) end)
mp.add_forced_key_binding('t', 'android-retry', function()
    if quitting then return end
    seq = seq + 1
    action = 'retry'
    publish(true)
end)
mp.add_forced_key_binding('i', 'android-help', function()
    mp.osd_message(table.concat({
        'MANGA-CLI Android',
        'Tap left: previous page',
        'Tap right: next page',
        'Tap center: page status',
        'Double tap: 2x zoom / reset',
        'Drag while zoomed: pan',
        'Rotate device: refit page',
        'Q / Esc: save and exit',
    }, '\n'), 6)
end)

local function manual_quit()
    cancel_pending_tap()
    stop_drag_binding()
    quitting = true
    seq = seq + 1
    action = 'quit'
    publish(true)
    mp.commandv('quit', '4')
end
mp.add_forced_key_binding('q', 'android-quit-q', manual_quit)
mp.add_forced_key_binding('ESC', 'android-quit-esc', manual_quit)
mp.add_forced_key_binding('CLOSE_WIN', 'android-quit-close', manual_quit)

mp.register_script_message('show-page', function(payload)
    local data = utils.parse_json(payload or '')
    if type(data) ~= 'table' or type(data.path) ~= 'string' then return end
    cancel_pending_tap()
    stop_drag_binding()
    reset_view(false)
    show_indicator = data.show_page_indicator ~= false
    local incoming_view = type(data.view) == 'table' and data.view or {}
    persist_fit = (incoming_view.fit == 'width' or incoming_view.fit == 'page') and incoming_view.fit or 'page'
    double_tap_scale = clamp(tonumber(data.mobile_double_tap_zoom) or 2.0, 1.25, 3.0)
    current = {
        page=data.page,
        total=data.total,
        chapter_key=data.chapter_key,
        label=data.label or 'manga-cli',
        loaded=false,
    }
    load_error = ''
    action = ''
    busy = true
    mp.commandv('loadfile', data.path, 'replace')
    publish(true)
end)

mp.register_script_message('waiting', function(text)
    busy = true
    mp.osd_message(text or 'Loading...', 3600)
    publish(true)
end)

mp.register_script_message('ready', function(text)
    busy = false
    action = ''
    cooldown = mp.get_time() + 0.16
    if text and text ~= '' then mp.osd_message(text, 2.5) end
    publish(true)
end)

mp.register_event('file-loaded', function()
    local function finish(attempt)
        local ok, d = dimensions_ready()
        if ok then
            reset_view(false)
            current.loaded = true
            busy = false
            cooldown = mp.get_time() + 0.16
            ow_seen, oh_seen = tonumber(d.w) or 0, tonumber(d.h) or 0
            publish(true)
            mp.osd_message('', 0)
            status(false)
        elseif attempt < 40 then
            mp.add_timeout(0.05, function() finish(attempt + 1) end)
        else
            load_error = 'Could not determine page dimensions'
            busy = false
            publish(true)
        end
    end
    mp.add_timeout(0.02, function() finish(1) end)
end)

-- Rotation/resizing: a manga app should refit, not leave the old crop on screen.
mp.observe_property('osd-dimensions', 'native', function(_, d)
    if not current.loaded or not d then return end
    local w, h = tonumber(d.w) or 0, tonumber(d.h) or 0
    if w <= 0 or h <= 0 then return end
    if ow_seen > 0 and oh_seen > 0 and (w ~= ow_seen or h ~= oh_seen) then
        ow_seen, oh_seen = w, h
        cancel_pending_tap()
        stop_drag_binding()
        reset_view(false)
        if show_indicator then mp.osd_message('Refit after rotation', 0.7) end
        publish(false)
    else
        ow_seen, oh_seen = w, h
    end
end)

mp.register_event('end-file', function(e)
    if e and e.reason == 'error' then
        load_error = e.error or 'mpv could not open the image'
        busy = false
        publish(true)
    end
end)

mp.register_event('shutdown', function()
    cancel_pending_tap()
    stop_drag_binding()
    quitting = true
    publish(true)
end)

publish(true)
