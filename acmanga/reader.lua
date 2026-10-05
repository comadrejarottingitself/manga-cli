-- manga-cli 0.8.3 / mpv >= 0.35.1. No external Lua modules.
local mp = require 'mp'
local utils = require 'mp.utils'
-- BEGIN GENERATED TEXT
local L = {
    arrows = "Left/right: previous/next | HOME: top",
    clicks = "Left click: next | right click: previous",
    dimensions = "Could not determine page dimensions",
    edges = "Auto page turn at WIDTH edges: ",
    edges_help = "When ON: bottom = next; another upward input at top = previous",
    image_error = "mpv could not open the image",
    loading = "Loading... Esc to exit",
    modes = "F/V: full page/width | F11: fullscreen | +/-: zoom",
    off = "OFF",
    on = "ON",
    page = "PAGE",
    page_wheel = "PAGE: wheel down/up = next/previous",
    pan = "A/D: horizontal | R/0: reset | TAB: status",
    quit = "T: retry download | Q/Esc: save and exit",
    space = "SPACE: down one screen (with overlap)",
    title = "MANGA-CLI 0.8.3 / a comadreja project",
    width = "WIDTH",
    width_wheel = "WIDTH: wheel, up/down arrows, W/S = scroll",
}
-- END GENERATED TEXT
local control_path = os.getenv('ACMANGA_READER_STATE')
local current = {page=0, total=0, chapter_key='', label='', loaded=false}
local pos, xpos, extra_zoom, fit = 0, 0, 0, 'width'
local scroll_step, overlap = 0.10, 0.12
local auto_page_turn, show_indicator = true, true
local busy, quitting, seq = false, false, 0
local action, load_error = '', ''
local held, blocked = {}, {}
local last_wheel, wheel_locked, cooldown = -100, false, 0
local ow_seen, oh_seen, generation = 0, 0, 0
local deferred = nil

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

local function publish(now)
    if not control_path then return end
    if not now then
        if deferred then return end
        deferred = mp.add_timeout(0.08, function() deferred=nil; publish(true) end)
        return
    end
    if deferred then deferred:kill(); deferred=nil end
    local data = {seq=seq, action=action, page=current.page, total=current.total,
        chapter_key=current.chapter_key, loaded=current.loaded, quit=quitting,
        error=load_error, busy=busy,
        view={position=pos, horizontal=xpos, zoom=extra_zoom, fit=fit}}
    -- mpv 0.35.x can expose mp.utils.format_json but return nil for this table.
    -- Keep the native helper when it works, and fall back to our tiny encoder otherwise.
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

local function geometry()
    local d = mp.get_property_native('osd-dimensions') or {}
    local v = mp.get_property_native('video-params') or {}
    local ow, oh = tonumber(d.w) or 0, tonumber(d.h) or 0
    local vw, vh = tonumber(v.dw or v.w) or 0, tonumber(v.dh or v.h) or 0
    if ow <= 0 or oh <= 0 or vw <= 0 or vh <= 0 then return nil end
    local base = math.min(ow/vw, oh/vh)
    local desired = fit == 'width' and ow/vw or base
    local zoom = math.log(desired/base)/math.log(2) + extra_zoom
    zoom = clamp(zoom, -10, 10)
    local scale = base * 2^zoom
    return {ow=ow, oh=oh, zoom=zoom, overflow=math.max(0, vh*scale-oh),
            overflow_x=math.max(0, vw*scale-ow)}
end

local function apply_view()
    local g = geometry()
    if not g then return false end
    mp.set_property_number('video-zoom', g.zoom)
    mp.set_property_number('video-pan-x', 0)
    mp.set_property_number('video-pan-y', 0)
    mp.set_property_number('video-align-x', g.overflow_x > 1 and xpos or 0)
    mp.set_property_number('video-align-y', g.overflow > 1 and (2*pos-1) or 0)
    ow_seen, oh_seen = g.ow, g.oh
    return true
end

local function status(force)
    if not force and not show_indicator then return end
    local label = current.label or 'manga-cli'
    mp.osd_message(string.format('%s  |  %d/%d  |  %s', label, current.page,
        current.total, fit == 'width' and L.width or L.page), 1.5)
end

local function request(direction, key)
    if quitting then return end
    if busy and direction > 0 then return end
    if not current.loaded then return end
    busy = true
    -- A held key must be released before scrolling the new page.
    for k, value in pairs(held) do if value then blocked[k]=true end end
    if key and held[key] then blocked[key]=true end
    wheel_locked = true
    cooldown = mp.get_time() + 0.18
    seq = seq + 1
    action = direction > 0 and 'next' or 'previous'
    publish(true)
end

local function scroll(direction, amount, key)
    if busy or not current.loaded then return end
    -- PAGINA is navigation, not scrolling. This is independent of both the
    -- boundary preference and the optional F11 fullscreen state.
    if fit == 'page' then
        request(direction, key)
        return
    end
    local g = geometry()
    if not g then return end
    if g.overflow <= 1 then
        -- Short images are not skipped on load. A deliberate input navigates
        -- only when automatic boundary changes in ANCHO are enabled.
        if auto_page_turn then request(direction, key) end
        return
    end
    local was_at_top = pos * g.overflow <= 1
    pos = clamp(pos + direction * amount * g.oh / g.overflow, 0, 1)
    apply_view()
    publish(false)
    if not auto_page_turn then return end
    if direction > 0 and (1-pos) * g.overflow <= 1 then
        -- Preserve the working 0.7.4 forward rule: reaching the bottom advances.
        pos = 1; apply_view(); request(1, key)
    elseif direction < 0 and was_at_top then
        -- First reach the top; a further upward input goes back. The previous
        -- image is opened at its bottom by the controller, not skipped on load.
        pos = 0; apply_view(); request(-1, key)
    end
end

local function bind(keys, name, callback, kind)
    for index, key in ipairs(keys) do
        mp.add_forced_key_binding(key, name .. '-' .. index, function(e)
            e = e or {event='press', scale=1}
            local event = e.event or 'press'
            if event == 'up' then
                held[key], blocked[key] = nil, nil
                return
            end
            if e.canceled then held[key], blocked[key]=nil,nil; return end
            if event == 'down' then held[key]=true; blocked[key]=nil end
            if kind == 'wheel' then
                local now = mp.get_time()
                local gap = now - last_wheel
                last_wheel = now
                if wheel_locked then
                    if gap < 0.18 or now < cooldown then return end
                    wheel_locked=false
                end
            elseif blocked[key] then return end
            if kind == 'once' and event == 'repeat' then return end
            if kind ~= 'utility' and mp.get_time() < cooldown then return end
            callback(clamp(e.scale or 1, 0.01, 8), key)
        end, {complex=true, scalable=true})
    end
end

bind({'LEFT','PGUP','BS','MBTN_RIGHT'}, 'page-prev', function(_,k) request(-1,k) end, 'once')
bind({'RIGHT','PGDWN','ENTER','MBTN_LEFT'}, 'page-next', function(_,k) request(1,k) end, 'once')
bind({'UP','w'}, 'pan-up', function(s,k) scroll(-1, scroll_step*s,k) end)
bind({'DOWN','s'}, 'pan-down', function(s,k) scroll(1, scroll_step*s,k) end)
bind({'WHEEL_UP'}, 'wheel-up', function(s,k) scroll(-1,scroll_step*s,k) end, 'wheel')
bind({'WHEEL_DOWN'}, 'wheel-down', function(s,k) scroll(1,scroll_step*s,k) end, 'wheel')
bind({'SPACE'}, 'space-action', function(s,k) scroll(1,(1-overlap)*s,k) end)
bind({'HOME'}, 'go-top', function() pos=0; apply_view(); publish(true) end, 'once')
bind({'END'}, 'go-bottom', function(_,k)
    if busy or not current.loaded then return end
    pos=1; apply_view(); publish(true)
    if fit == 'page' or auto_page_turn then request(1,k) end
end, 'once')
bind({'a','WHEEL_LEFT'}, 'pan-left', function(s)
    xpos=clamp(xpos-0.1*s,-1,1); apply_view(); publish(false)
end)
bind({'d','WHEEL_RIGHT'}, 'pan-right', function(s)
    xpos=clamp(xpos+0.1*s,-1,1); apply_view(); publish(false)
end)
bind({'+','='}, 'zoom-in', function(s)
    extra_zoom=clamp(extra_zoom+0.12*s,-2,3); apply_view(); publish(false)
end)
bind({'-'}, 'zoom-out', function(s)
    extra_zoom=clamp(extra_zoom-0.12*s,-2,3); apply_view(); publish(false)
end)
bind({'r','0'}, 'reset-view', function()
    pos,xpos,extra_zoom=0,0,0; apply_view(); publish(true); status()
end, 'once')
local function toggle_fit()
    fit=fit == 'width' and 'page' or 'width'
    extra_zoom=0
    apply_view(); publish(true); status()
end
bind({'f','F','v','V'}, 'toggle-fit', toggle_fit, 'once')
bind({'F11'}, 'toggle-fullscreen', function()
    mp.set_property_native('fullscreen', mp.get_property_native('fullscreen') ~= true)
end, 'once')
bind({'TAB'}, 'reader-status', function() status(true) end, 'utility')
bind({'i','?'}, 'reader-help', function()
    mp.osd_message(table.concat({
        L.title,
        L.clicks,
        L.page_wheel,
        L.width_wheel,
        L.space,
        L.edges .. (auto_page_turn and L.on or L.off),
        L.edges_help,
        L.arrows,
        L.modes,
        L.pan,
        L.quit
    }, '\n'), 7)
end, 'utility')
bind({'t'}, 'retry', function()
    seq=seq+1; action='retry'; publish(true)
end, 'utility')
local function manual_quit()
    quitting=true; seq=seq+1; action='quit'; publish(true)
    mp.commandv('quit', '4')
end
bind({'q','ESC','CLOSE_WIN'}, 'manual-quit', manual_quit, 'utility')
-- No default fullscreen-on-double-click and no pause-on-click.
mp.add_forced_key_binding('MBTN_LEFT_DBL', 'no-double-left', function() end)
mp.add_forced_key_binding('MBTN_RIGHT_DBL', 'no-double-right', function() end)

mp.register_script_message('show-page', function(payload)
    local data = utils.parse_json(payload or '')
    if type(data) ~= 'table' or type(data.path) ~= 'string' then return end
    local saved = data.view or {}
    fit = saved.fit == 'page' and 'page' or 'width'
    pos = clamp(saved.position or 0,0,1)
    xpos = clamp(saved.horizontal or 0,-1,1)
    extra_zoom = clamp(saved.zoom or 0,-2,3)
    scroll_step=clamp(data.scroll_step or 0.10,0.02,0.5)
    overlap=clamp(data.overlap or 0.12,0,0.5)
    auto_page_turn=data.auto_page_turn ~= false
    show_indicator=data.show_page_indicator ~= false
    current={page=data.page,total=data.total,chapter_key=data.chapter_key,
        label=data.label or 'manga-cli',loaded=false}
    load_error=''; action=''; busy=true; generation=generation+1
    mp.commandv('loadfile', data.path, 'replace')
    publish(true)
end)

mp.register_script_message('waiting', function(text)
    busy=true; mp.osd_message(text or L.loading, 3600)
    publish(true)
end)
mp.register_script_message('ready', function(text)
    busy=false; action=''; cooldown=mp.get_time()+0.18
    if text and text ~= '' then mp.osd_message(text, 3) end
    publish(true)
end)

mp.register_event('file-loaded', function()
    local token=generation
    local function finish(attempt)
        if token ~= generation then return end
        if apply_view() then
            current.loaded=true; busy=false; cooldown=mp.get_time()+0.18
            wheel_locked=true; publish(true); mp.osd_message('',0); status()
        elseif attempt < 30 then
            mp.add_timeout(0.05,function() finish(attempt+1) end)
        else
            load_error=L.dimensions
            busy=false; publish(true)
        end
    end
    mp.add_timeout(0.02,function() finish(1) end)
end)
mp.observe_property('osd-dimensions','native',function(_,d)
    if not current.loaded or not d then return end
    if d.w ~= ow_seen or d.h ~= oh_seen then
        apply_view(); publish(false)
    end
end)
mp.register_event('end-file',function(e)
    if e and e.reason == 'error' then
        load_error=e.error or L.image_error
        busy=false; publish(true)
    end
end)
mp.register_event('shutdown',function()
    quitting=true; publish(true)
end)
publish(true)
