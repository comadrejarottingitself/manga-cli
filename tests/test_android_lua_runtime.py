"""Execute the actual Android Lua reader against a mocked mpv API.

This catches binding/state regressions in the real Lua source without pretending
that a host Lua VM can replace final Termux:X11 touch testing.
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
CLOCK=100; TIMERS={}; EVENTS={}; MESSAGES={}; KEYS={}; BINDING_KEYS={}; OBSERVERS={}; LAST={}; LOADS=0
PROPS={
 ['osd-dimensions']={w=1000,h=1800,ml=162,mr=162,mt=0,mb=0},
 ['video-params']={dw=1200,dh=1800},
 ['mouse-pos']={x=500,y=900}, ['touch-pos']={},
 ['video-zoom']=0, ['video-pan-x']=0, ['video-pan-y']=0,
 ['video-align-x']=0, ['video-align-y']=0,
}
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
    for _=1,500 do
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
function mp.unobserve_property(fn) end
function mp.add_forced_key_binding(k,n,fn,flags) KEYS[k]=fn;BINDING_KEYS[n]=k end
function mp.remove_key_binding(n)
    local k=BINDING_KEYS[n]
    if k then KEYS[k]=nil;BINDING_KEYS[n]=nil end
end
function mp.commandv(n,...)
    if n=='loadfile' then LOADS=LOADS+1;EVENTS['file-loaded']() end
    if n=='quit' then QUIT=select(1,...) end
end
package.preload['mp']=function() return mp end
package.preload['mp.utils']=function() return {
    format_json=function(t) LAST=copy(t); return '{}' end,
    parse_json=function(s) return INPUT end,
} end
function pointer(x,y)
    PROPS['mouse-pos']={x=x,y=y}
end
function show(page,fit,scale,indicator)
    INPUT={path='/tmp/test.png',page=page,total=5,chapter_key='chapter',label='Ch 1',
           view={position=0,horizontal=0,zoom=0,fit=fit or 'page'},
           show_page_indicator=(indicator ~= false), mobile_double_tap_zoom=scale or 2.0}
    MESSAGES['show-page']('fixture');advance(0.3)
end
function press(k,event)
    assert(KEYS[k], 'Missing binding: '..k)
    KEYS[k]({event=event or 'press',scale=1,is_mouse=k:find('MBTN')~=nil})
end
'''


class LuaVM:
    def __init__(self):
        name = next((ctypes.util.find_library(n) for n in ['lua5.4','lua5.3','lua5.2'] if ctypes.util.find_library(n)), None)
        if not name:
            raise unittest.SkipTest('No liblua available for Android reader harness')
        self.lib = ctypes.CDLL(name)
        self.lib.luaL_newstate.restype = ctypes.c_void_p
        self.lib.luaL_openlibs.argtypes = [ctypes.c_void_p]
        self.lib.luaL_loadstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self.lib.luaL_loadstring.restype = ctypes.c_int
        self.lib.lua_pcallk.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ssize_t, ctypes.c_void_p]
        self.lib.lua_pcallk.restype = ctypes.c_int
        self.lib.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        self.lib.lua_tolstring.restype = ctypes.c_char_p
        self.lib.lua_close.argtypes = [ctypes.c_void_p]
        self.state = self.lib.luaL_newstate()
        self.lib.luaL_openlibs(self.state)

    def run(self, code):
        result = self.lib.luaL_loadstring(self.state, code.encode('utf-8'))
        if not result:
            result = self.lib.lua_pcallk(self.state, 0, 0, 0, 0, None)
        if result:
            raise AssertionError(self.lib.lua_tolstring(self.state, -1, None).decode('utf-8', 'replace'))

    def close(self):
        self.lib.lua_close(self.state)


class AndroidLuaRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {'ACMANGA_READER_STATE': str(Path(self.tmp.name) / 'state.json')})
        self.env.start()
        self.vm = LuaVM()
        self.vm.run(HARNESS)
        self.vm.run((ROOT / 'acmanga' / 'reader_android.lua').read_text(encoding='utf-8'))

    def tearDown(self):
        if hasattr(self, 'vm'):
            self.vm.close()
        self.env.stop()
        self.tmp.cleanup()

    def test_right_tap_next_after_double_tap_window(self):
        self.vm.run("show(2);pointer(900,900);press('MBTN_LEFT','press');assert(LAST.action=='');advance(.31);assert(LAST.action=='next')")

    def test_left_tap_previous(self):
        self.vm.run("show(2);pointer(100,900);press('MBTN_LEFT','press');advance(.31);assert(LAST.action=='previous')")

    def test_center_tap_never_turns_page(self):
        self.vm.run("show(2);pointer(500,900);press('MBTN_LEFT','press');advance(.31);assert(LAST.action=='')")

    def test_double_tap_zoom_and_second_double_tap_reset(self):
        self.vm.run("show(2);pointer(800,700);press('MBTN_LEFT_DBL');assert(math.abs(PROPS['video-zoom']-1)<.00001);assert(LAST.action=='');press('MBTN_LEFT_DBL');assert(PROPS['video-zoom']==0);assert(PROPS['video-align-x']==0);assert(PROPS['video-align-y']==0)")

    def test_configurable_double_tap_factor(self):
        self.vm.run("show(2,'page',2.5);pointer(500,900);press('MBTN_LEFT_DBL');assert(math.abs(PROPS['video-zoom']-(math.log(2.5)/math.log(2)))<.00001)")

    def test_double_tap_guard_prevents_ghost_page_turn(self):
        self.vm.run("show(2);pointer(900,900);press('MBTN_LEFT_DBL');press('MBTN_LEFT','up');advance(.5);assert(LAST.action=='')")

    def test_single_tap_while_zoomed_is_status_only(self):
        self.vm.run("show(2);pointer(900,900);press('MBTN_LEFT_DBL');advance(.25);press('MBTN_LEFT','press');advance(.31);assert(LAST.action=='')")

    def test_drag_while_zoomed_changes_alignment_but_stays_bounded(self):
        self.vm.run("show(2);pointer(500,900);press('MBTN_LEFT_DBL');pointer(500,900);press('MBTN_LEFT','down');pointer(650,1050);press('MOUSE_MOVE');assert(PROPS['video-align-x']>=-1 and PROPS['video-align-x']<=1);assert(PROPS['video-align-y']>=-1 and PROPS['video-align-y']<=1);assert(math.abs(PROPS['video-align-x'])>0 or math.abs(PROPS['video-align-y'])>0);press('MBTN_LEFT','up')")

    def test_rotation_refits_zoomed_page(self):
        self.vm.run("show(2);pointer(500,900);press('MBTN_LEFT_DBL');assert(PROPS['video-zoom']>0);PROPS['osd-dimensions']={w=1800,h=1000,ml=0,mr=0,mt=162,mb=162};OBSERVERS['osd-dimensions']('',PROPS['osd-dimensions']);assert(PROPS['video-zoom']==0);assert(PROPS['video-align-x']==0);assert(PROPS['video-align-y']==0)")

    def test_mobile_view_preserves_incoming_desktop_fit_without_phone_zoom(self):
        self.vm.run("show(2,'width');assert(LAST.view.fit=='width');pointer(500,900);press('MBTN_LEFT_DBL');assert(LAST.view.zoom==0);assert(LAST.view.position==0);assert(LAST.view.fit=='width')")

    def test_busy_state_ignores_navigation(self):
        self.vm.run("show(2);MESSAGES.waiting('Loading');pointer(900,900);press('MBTN_LEFT','press');advance(.31);assert(LAST.action=='')")

    def test_manual_quit_uses_reader_contract_exit_code(self):
        self.vm.run("show(2);press('ESC');assert(LAST.quit);assert(QUIT=='4');assert(LAST.action=='quit')")


if __name__ == '__main__':
    unittest.main(verbosity=2)
