"""Termux installer checks for the Android v0.8.3 profile."""
import os
import re
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
IS_NATIVE_TERMUX = bool(os.environ.get("TERMUX_VERSION")) or str(os.environ.get("PREFIX") or "").startswith("/data/data/com.termux/")


class AndroidInstallerStaticTests(unittest.TestCase):
    def test_android_installer_is_separate_from_debian_installer(self):
        text = (ROOT / "termux-install.sh").read_text(encoding="utf-8")
        self.assertIn("manga-cli android managed launcher v0.8.3", text)
        self.assertNotIn("install.sh", text)
        self.assertNotIn("apt-get", text)
        self.assertIn("reader_android.lua", text)

    def test_android_uninstaller_only_removes_android_code_and_managed_launcher(self):
        text = (ROOT / "termux-uninstall.sh").read_text(encoding="utf-8")
        self.assertIn("manga-cli-android-v083", text)
        self.assertIn("manga-termux", text)
        self.assertNotIn("anticomadreja-manga", text)
        self.assertNotIn(".cache", text)


@unittest.skipUnless(IS_NATIVE_TERMUX, "runtime installer test requires native Termux")
class AndroidInstallerRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="manga-cli-android-install-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.prefix = self.base / "prefix"
        (self.prefix / "bin").mkdir(parents=True)
        self.fakebin = self.base / "fakebin"
        self.fakebin.mkdir()
        for name in ("mpv", "termux-x11", "pgrep"):
            p = self.fakebin / name
            p.write_text("#!/data/data/com.termux/files/usr/bin/bash\nexit 0\n", encoding="utf-8")
            p.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update({
            "HOME": str(self.home),
            # Keep the native Termux prefix shape but isolate launcher writes.
            "PREFIX": str(self.prefix),
            "PATH": str(self.fakebin) + os.pathsep + os.environ.get("PATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        # The installer deliberately requires a native-Termux-shaped PREFIX.
        # tempfile lives below /data/data/com.termux on native Termux, so this remains isolated.
        self.assertTrue(str(self.prefix).startswith("/data/data/com.termux/"), self.prefix)

    def run_install(self):
        return subprocess.run(["bash", str(ROOT / "termux-install.sh")], env=self.env,
                              text=True, capture_output=True, timeout=30)

    def run_uninstall(self):
        return subprocess.run(["bash", str(ROOT / "termux-uninstall.sh")], env=self.env,
                              text=True, capture_output=True, timeout=30)

    @property
    def app(self):
        return self.home / ".local/lib/manga-cli-android-v083"

    @property
    def launcher(self):
        return self.prefix / "bin/manga-termux"

    def seed_private_data(self):
        data = self.home / ".local/share/anticomadreja-manga"
        cache = self.home / ".cache/anticomadreja-manga"
        data.mkdir(parents=True, exist_ok=True)
        cache.mkdir(parents=True, exist_ok=True)
        (data / "state.json").write_bytes(b"PRIVATE-STATE\n")
        (data / "settings.json").write_bytes(b"PRIVATE-SETTINGS\n")
        (cache / "keep").write_bytes(b"PRIVATE-CACHE\n")
        return {p: p.read_bytes() for p in (data / "state.json", data / "settings.json", cache / "keep")}

    def test_clean_install_and_reinstall_are_isolated_and_idempotent(self):
        private = self.seed_private_data()
        first = self.run_install()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertTrue((self.app / "manga.py").is_file())
        self.assertTrue((self.app / "acmanga/reader_android.lua").is_file())
        self.assertTrue(os.access(self.launcher, os.X_OK))
        for p, raw in private.items():
            self.assertEqual(p.read_bytes(), raw)
        second = self.run_install()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        for p, raw in private.items():
            self.assertEqual(p.read_bytes(), raw)

    def test_unknown_launcher_is_refused_before_code_replacement(self):
        self.launcher.write_text("unrelated launcher\n", encoding="utf-8")
        before = self.launcher.read_bytes()
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.launcher.read_bytes(), before)
        self.assertFalse(self.app.exists())

    def test_uninstall_removes_only_managed_android_files(self):
        private = self.seed_private_data()
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.run_uninstall()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.app.exists())
        self.assertFalse(self.launcher.exists())
        for p, raw in private.items():
            self.assertEqual(p.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
