"""Execute the actual Lua reader against a mocked mpv API, not a translation.

Needs a system liblua (already supplied by many mpv installations); skips cleanly
when no compatible shared library can be found. No X server, network or private data.
"""
import ctypes
import ctypes.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
CLOCK=100; TIMERS={}; EVENTS={}; MESSAGES={}; KEYS={}; OBSERVERS={}; LAST={}; LOADS=0
PROPS={['osd-dimensions']={w=1000,h=800}, ['video-params']={dw=1000,dh=3000},fullscreen=false}
local function copy(t)
    if type(t) ~= 'table' then return t end
    local o={};for k,v in pairs(t) do o[k]=copy(v) end;return o
end
local mp={}
function mp.get_time() return CLOCK end
function mp.get_property_native(n) return PROPS[n] end
function mp.get_property_number(n,d) return tonumber(PROPS[n]) or d end
function mp.get_property(n,d) return PROPS[n] or d end
function mp.set_property_number(n,v) PROPS[n]=v end
function mp.set_property_native(n,v) PROPS[n]=v end
function mp.osd_message(t,d) OSD=t;OSD_DURATION=d end
function mp.add_timeout(delay,fn)
    local t={at=CLOCK+delay,fn=fn,killed=false}
    function t:kill() self.killed=true end
    table.insert(TIMERS,t); return t
end
function advance(dt)
    local goal=CLOCK+dt
    for _=1,200 do
        local next_t=nil
        for _,t in ipairs(TIMERS) do
            if not t.killed and t.at <= goal and (not next_t or t.at < next_t.at) then next_t=t end
        end
        if not next_t then break end
        CLOCK=next_t.at;next_t.killed=true;next_t.fn()
    end
    CLOCK=goal
end
function mp.register_event(n,fn) EVENTS[n]=fn end
function mp.register_script_message(n,fn) MESSAGES[n]=fn end
function mp.observe_property(n,_,fn) OBSERVERS[n]=fn end
function mp.add_forced_key_binding(k,n,fn,flags) KEYS[k]=fn end
function mp.commandv(n,...)
    if n=='loadfile' then LOADS=LOADS+1;EVENTS['file-loaded']() end
    if n=='quit' then QUIT=select(1,...) end
end
package.preload['mp']=function() return mp end
package.preload['mp.utils']=function() return {
    format_json=function(t) LAST=copy(t); if FORMAT_JSON_NIL then return nil end; return '{}' end,
    parse_json=function(s) return INPUT end
} end
function show(page,h,position,fit,zoom,options)
    PROPS['video-params']={dw=1000,dh=h or 3000}
    INPUT={path='/tmp/test.png',page=page,total=5,chapter_key='chapter',label='Cap 1',
           view={position=position or 0,fit=fit or 'width',zoom=zoom or 0}}
    if options then for k,v in pairs(options) do INPUT[k]=v end end
    MESSAGES['show-page']('fixture');advance(0.3)
end
function key(k,event,scale)
    assert(KEYS[k], 'Missing binding: '..k)
    KEYS[k]({event=event or 'press',scale=scale or 1,is_mouse=k:find('MBTN')~=nil})
    advance(0.09)
end
'''


class LuaVM:
    def __init__(self):
        name = next((ctypes.util.find_library(n) for n in ['lua5.4','lua5.3','lua5.2'] if ctypes.util.find_library(n)), None)
        if not name:
            raise unittest.SkipTest('No liblua available for the mpv API harness')
        self.lib = ctypes.CDLL(name)
        self.lib.luaL_newstate.restype=ctypes.c_void_p
        self.lib.luaL_openlibs.argtypes=[ctypes.c_void_p]
        self.lib.luaL_loadstring.argtypes=[ctypes.c_void_p,ctypes.c_char_p]
        self.lib.luaL_loadstring.restype=ctypes.c_int
        self.lib.lua_pcallk.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_ssize_t,ctypes.c_void_p]
        self.lib.lua_pcallk.restype=ctypes.c_int
        self.lib.lua_tolstring.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p]
        self.lib.lua_tolstring.restype=ctypes.c_char_p
        self.lib.lua_close.argtypes=[ctypes.c_void_p]
        self.state=self.lib.luaL_newstate();self.lib.luaL_openlibs(self.state)

    def run(self, code):
        result=self.lib.luaL_loadstring(self.state,code.encode('utf-8'))
        if not result:
            result=self.lib.lua_pcallk(self.state,0,0,0,0,None)
        if result:
            raise AssertionError(self.lib.lua_tolstring(self.state,-1,None).decode('utf-8','replace'))

    def close(self):
        self.lib.lua_close(self.state)


class LuaReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=mock.patch.dict(os.environ,{'ACMANGA_READER_STATE':str(Path(self.tmp.name)/'state.json')})
        self.env.start()
        self.vm=LuaVM()
        self.vm.run(HARNESS)
        self.vm.run((ROOT/'acmanga/reader.lua').read_text(encoding='utf-8'))

    def tearDown(self):
        if hasattr(self,'vm'): self.vm.close()
        self.env.stop();self.tmp.cleanup()


    def test_mpv_035_nil_format_json_falls_back_to_valid_json(self):
        # Reproduce the real Debian 12/mpv 0.35.1 failure reported by the user.
        self.vm.close()
        self.vm=LuaVM()
        self.vm.run(HARNESS + "\nFORMAT_JSON_NIL=true\n")
        self.vm.run((ROOT/'acmanga/reader.lua').read_text(encoding='utf-8'))
        import json
        state_path=Path(self.tmp.name)/'state.json'
        data=json.loads(state_path.read_text(encoding='utf-8'))
        self.assertEqual(data['page'],0)
        self.assertEqual(data['action'],'')
        self.assertIn('view',data)

    def test_all_down_methods_advance_on_reaching_bottom(self):
        for key in ['DOWN','s','SPACE','WHEEL_DOWN','END']:
            with self.subTest(key=key):
                self.vm.run("show(1,3000,0.995); local s=LAST.seq; key('%s'); assert(LAST.action=='next'); assert(LAST.seq==s+1)"%key)

    def test_fitting_image_never_advances_on_load(self):
        self.vm.run("show(1,500); assert(LAST.action=='');assert(LAST.page==1);key('DOWN');assert(LAST.action=='next')")

    def test_short_page_can_advance_with_wheel(self):
        self.vm.run("show(1,500);key('WHEEL_DOWN');assert(LAST.action=='next')")

    def test_left_click_next_right_click_previous(self):
        self.vm.run("show(2);key('MBTN_LEFT');assert(LAST.action=='next');show(3);key('MBTN_RIGHT');assert(LAST.action=='previous')")

    def test_repeat_does_not_turn_multiple_pages(self):
        self.vm.run("show(1,3000,.999);key('DOWN','down');assert(LAST.action=='next');show(2);key('DOWN','repeat');assert(LAST.view.position==0);key('DOWN','up');key('DOWN','down');assert(LAST.view.position>0)")

    def test_wheel_inertia_does_not_cross_new_page(self):
        self.vm.run("show(1,3000,.999);key('WHEEL_DOWN');show(2); CLOCK=CLOCK-0.2; key('WHEEL_DOWN');local p=LAST.view.position;key('WHEEL_DOWN');assert(LAST.view.position==p);advance(.3);key('WHEEL_DOWN');assert(LAST.view.position>p)")

    def test_scroll_is_screen_relative(self):
        self.vm.run("show(1,3000);key('DOWN');local a=LAST.view.position*2200;show(2,6000);key('DOWN');local b=LAST.view.position*5200;assert(math.abs(a-b)<.01);assert(math.abs(a-80)<.01)")

    def test_space_has_overlap(self):
        self.vm.run("show(1,3000);key('SPACE');assert(math.abs(LAST.view.position*2200-704)<.01)")

    def test_page_down_and_enter_explicit_next(self):
        self.vm.run("show(1);key('PGDWN');assert(LAST.action=='next');show(2);key('ENTER');assert(LAST.action=='next')")

    def test_page_up_and_backspace_explicit_previous(self):
        self.vm.run("show(2);key('PGUP');assert(LAST.action=='previous');show(1);key('BS');assert(LAST.action=='previous')")


    def test_debian_12_does_not_register_unknown_keypad_names(self):
        text=(ROOT/'acmanga/reader.lua').read_text(encoding='utf-8')
        self.assertNotIn('KP_ADD', text)
        self.assertNotIn('KP_SUBTRACT', text)

    def test_zoom_does_not_reset_position(self):
        self.vm.run("show(1,3000,.4);key('+');assert(math.abs(LAST.view.position-.4)<.0001);assert(LAST.view.zoom>0)")

    def test_resize_preserves_relative_position(self):
        self.vm.run("show(1,3000,.6);PROPS['osd-dimensions']={w=800,h=600};OBSERVERS['osd-dimensions']('',PROPS['osd-dimensions']);advance(.2);assert(LAST.view.position==.6)")

    def test_f_toggles_page_and_width_without_resizing_window(self):
        self.vm.run("show(1,3000,0,'page');assert(LAST.view.fit=='page');key('f');assert(LAST.view.fit=='width');assert(not PROPS.fullscreen);key('f');assert(LAST.view.fit=='page');assert(not PROPS.fullscreen)")

    def test_f11_keeps_true_fullscreen_as_optional_control(self):
        self.vm.run("show(1,3000,0,'page');key('F11');assert(PROPS.fullscreen);key('F11');assert(not PROPS.fullscreen)")

    def test_home_and_reset(self):
        self.vm.run("show(1,3000,.7,'width',.6);key('HOME');assert(LAST.view.position==0);key('r');assert(LAST.view.zoom==0)")

    def test_full_page_down_advances_after_fit_change(self):
        self.vm.run("show(1,3000,.4,'page');key('SPACE');assert(LAST.action=='next')")

    def test_quit_publishes_latest_view(self):
        self.vm.run("show(1,3000,.35);key('ESC');assert(LAST.quit);assert(QUIT=='4');assert(LAST.view.position==.35)")

    def test_pending_page_can_be_cancelled_with_previous(self):
        self.vm.run("show(2);key('MBTN_LEFT');advance(.3);key('MBTN_RIGHT');assert(LAST.action=='previous')")

    def test_retry_available_while_busy(self):
        self.vm.run("show(1);MESSAGES.waiting('descargando');key('t');assert(LAST.action=='retry')")

    def test_doubleclick_does_not_toggle_fullscreen(self):
        self.vm.run("show(1);key('MBTN_LEFT_DBL');assert(not PROPS.fullscreen);assert(LAST.action=='')")

    def test_help_uses_seconds_not_milliseconds(self):
        self.vm.run("show(1);key('i');assert(OSD_DURATION==7)")

    def test_bad_payload_ignored(self):
        self.vm.run("show(1);INPUT={};MESSAGES['show-page']('bad');assert(LOADS==1)")

    def test_decode_error_is_reported(self):
        self.vm.run("show(1);EVENTS['end-file']({reason='error',error='bad image'});assert(LAST.error=='bad image')")

    def test_page_mode_wheel_is_bidirectional(self):
        self.vm.run("show(2,3000,0,'page');key('WHEEL_UP');assert(LAST.action=='previous');show(1,3000,0,'page');key('WHEEL_DOWN');assert(LAST.action=='next')")

    def test_page_mode_wheel_ignores_auto_boundary_setting(self):
        self.vm.run("show(2,3000,0,'page',0,{auto_page_turn=false});key('WHEEL_UP');assert(LAST.action=='previous');show(1,3000,0,'page',0,{auto_page_turn=false});key('WHEEL_DOWN');assert(LAST.action=='next')")

    def test_page_mode_wheel_navigates_even_after_manual_zoom(self):
        self.vm.run("show(2,3000,0,'page',1.0);key('WHEEL_UP');assert(LAST.action=='previous')")

    def test_width_up_at_top_requests_previous(self):
        for name in ('WHEEL_UP', 'UP', 'w'):
            with self.subTest(key=name):
                self.vm.run("show(2,3000,0,'width');local s=LAST.seq;key('%s');assert(LAST.action=='previous');assert(LAST.seq==s+1)" % name)

    def test_width_up_in_middle_is_scroll_not_page_turn(self):
        self.vm.run("show(2,3000,.6,'width');local s=LAST.seq;key('WHEEL_UP');assert(LAST.view.position<.6);assert(LAST.view.position>0);assert(LAST.seq==s);assert(LAST.action=='')")

    def test_width_reaching_top_requires_one_further_upward_input(self):
        self.vm.run("show(2,3000,.01,'width');local s=LAST.seq;key('WHEEL_UP');assert(LAST.view.position==0);assert(LAST.seq==s);key('WHEEL_UP');assert(LAST.action=='previous')")

    def test_width_auto_off_bottom_stays_but_explicit_next_works(self):
        self.vm.run("show(2,3000,.999,'width',0,{auto_page_turn=false});key('WHEEL_DOWN');key('WHEEL_DOWN');assert(LAST.view.position==1);assert(LAST.action=='');key('MBTN_LEFT');assert(LAST.action=='next')")

    def test_width_auto_off_top_stays_but_explicit_previous_works(self):
        self.vm.run("show(2,3000,.01,'width',0,{auto_page_turn=false});key('WHEEL_UP');key('WHEEL_UP');assert(LAST.view.position==0);assert(LAST.action=='');key('MBTN_RIGHT');assert(LAST.action=='previous')")

    def test_auto_off_applies_to_all_downward_methods(self):
        for name in ('WHEEL_DOWN', 'DOWN', 's', 'SPACE', 'END'):
            with self.subTest(key=name):
                self.vm.run("show(2,3000,.999,'width',0,{auto_page_turn=false});local s=LAST.seq;key('%s');assert(LAST.view.position==1);assert(LAST.seq==s)" % name)

    def test_auto_off_applies_to_all_upward_methods(self):
        for name in ('WHEEL_UP', 'UP', 'w'):
            with self.subTest(key=name):
                self.vm.run("show(2,3000,0,'width',0,{auto_page_turn=false});local s=LAST.seq;key('%s');assert(LAST.view.position==0);assert(LAST.seq==s)" % name)

    def test_auto_off_short_width_page_never_turns_on_scroll(self):
        self.vm.run("show(2,500,0,'width',0,{auto_page_turn=false});local s=LAST.seq;key('WHEEL_UP');key('WHEEL_DOWN');key('SPACE');assert(LAST.seq==s)")

    def test_short_width_page_navigates_back_when_enabled(self):
        self.vm.run("show(2,500,0,'width');assert(LAST.action=='');key('WHEEL_UP');assert(LAST.action=='previous')")

    def test_previous_image_bottom_does_not_autoadvance_on_load(self):
        self.vm.run("show(1,3000,1,'width');local s=LAST.seq;advance(10);assert(LAST.seq==s);key('WHEEL_UP');assert(LAST.view.position<1);assert(LAST.seq==s)")

    def test_reverse_wheel_inertia_does_not_skip_page(self):
        self.vm.run("show(3,3000,0,'width');key('WHEEL_UP');assert(LAST.action=='previous');show(2,3000,1,'width'); CLOCK=CLOCK-.2;key('WHEEL_UP');local p=LAST.view.position;key('WHEEL_UP');assert(LAST.view.position==p);advance(.3);key('WHEEL_UP');assert(LAST.view.position<p)")

    def test_reverse_held_key_must_be_released_on_previous_image(self):
        self.vm.run("show(3,3000,0,'width');key('UP','down');assert(LAST.action=='previous');show(2,3000,1,'width');key('UP','repeat');assert(LAST.view.position==1);key('UP','up');key('UP','down');assert(LAST.view.position<1)")

    def test_page_mode_debounce_applies_both_directions(self):
        for key in ('WHEEL_DOWN','WHEEL_UP'):
            with self.subTest(key=key):
                self.vm.run("show(3,3000,0,'page');key('%s');local s=LAST.seq;show(2,3000,0,'page');CLOCK=CLOCK-.2;key('%s');key('%s');assert(LAST.seq==s);advance(.3);key('%s');assert(LAST.seq==s+1)" % (key,key,key,key))

    def test_indicator_off_hides_automatic_page_and_fit_messages(self):
        self.vm.run("MESSAGES.waiting('Loading');show(2,3000,0,'page',0,{show_page_indicator=false});assert(OSD=='');key('f');assert(OSD=='');key('TAB');assert(OSD:find('WIDTH'));key('i');assert(OSD:find('PAGE'))")

    def test_indicator_on_shows_page_numbers(self):
        self.vm.run("show(2,3000,0,'page');assert(OSD:find('2/5'));assert(OSD:find('PAGE'))")

    def test_indicator_off_does_not_suppress_errors(self):
        self.vm.run("show(2,3000,0,'page',0,{show_page_indicator=false});MESSAGES.ready('Test failure');assert(OSD=='Test failure')")

    def test_true_fullscreen_does_not_change_scroll_semantics(self):
        self.vm.run("show(2,3000,0,'page');key('F11');key('WHEEL_UP');assert(LAST.action=='previous');show(2,3000,.6,'width');key('WHEEL_UP');assert(LAST.action=='');assert(LAST.view.position<.6)")

    def test_home_never_navigates_to_previous(self):
        self.vm.run("show(2,3000,.6,'width');key('HOME');assert(LAST.view.position==0);assert(LAST.action=='')")

    def test_extreme_wheel_delta_requests_only_one_page(self):
        self.vm.run("show(2,3000,.99,'width');local s=LAST.seq;key('WHEEL_DOWN','press',800);key('WHEEL_DOWN','press',800);assert(LAST.seq==s+1)")

    def test_navigation_burst_while_loading_does_not_queue_pages(self):
        self.vm.run("show(2,3000,.99,'width');key('WHEEL_DOWN');local s=LAST.seq;MESSAGES.waiting('pending');for i=1,20 do key('WHEEL_DOWN') end;assert(LAST.seq==s)")
