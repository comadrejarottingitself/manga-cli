"""Compile the dedicated Android reader with the system Lua runtime when available."""
from pathlib import Path
import ctypes
import ctypes.util
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AndroidLuaSyntaxTests(unittest.TestCase):
    def test_android_reader_compiles_with_lua_runtime(self):
        library = ctypes.util.find_library("lua5.4") or ctypes.util.find_library("lua")
        if not library:
            self.skipTest("Lua runtime library not installed")
        lua = ctypes.CDLL(library)
        lua.luaL_newstate.restype = ctypes.c_void_p
        lua.luaL_loadfilex.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        lua.luaL_loadfilex.restype = ctypes.c_int
        lua.lua_close.argtypes = [ctypes.c_void_p]
        state = lua.luaL_newstate()
        self.assertTrue(state)
        try:
            path = ROOT / "acmanga" / "reader_android.lua"
            code = lua.luaL_loadfilex(state, str(path).encode(), None)
            self.assertEqual(code, 0, "reader_android.lua has a Lua syntax error")
        finally:
            lua.lua_close(state)


if __name__ == "__main__":
    unittest.main(verbosity=2)
