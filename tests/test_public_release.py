"""Public-repository release guards for 0.8.1."""
from pathlib import Path
import os
import subprocess
import tempfile
import shutil
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PublicReleaseTests(unittest.TestCase):
    def test_public_repository_metadata_exists(self):
        for rel in (
            "README.md", "LICENSE", "SECURITY.md", ".gitignore", ".gitattributes", ".editorconfig",
            ".github/workflows/ci.yml", ".github/ISSUE_TEMPLATE/bug_report.yml",
            ".github/ISSUE_TEMPLATE/source_breakage.yml", ".github/pull_request_template.md",
        ):
            self.assertTrue((ROOT / rel).is_file(), rel)

    def test_privacy_guard_passes(self):
        result = subprocess.run(
            [os.sys.executable, str(ROOT / "tools/privacy_check.py")],
            cwd=ROOT, text=True, capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_install_script_bootstraps_debian_dependencies(self):
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("apt-get install -y", text)
        self.assertIn("python3", text)
        self.assertIn("mpv", text)
        self.assertIn("MANGA_CLI_SKIP_SYSTEM_DEPS", text)
        self.assertNotIn("pip install", text)

    def test_dependency_bootstrap_can_be_disabled_safely(self):
        with tempfile.TemporaryDirectory() as td:
            fakebin = Path(td) / "bin"
            fakebin.mkdir()
            (fakebin / "python3").symlink_to(Path(os.sys.executable))
            dirname = shutil.which("dirname")
            self.assertIsNotNone(dirname)
            (fakebin / "dirname").symlink_to(Path(dirname))
            env = os.environ.copy()
            env["PATH"] = str(fakebin)
            env["MANGA_CLI_SKIP_SYSTEM_DEPS"] = "1"
            env.pop("SUDO_USER", None)
            result = subprocess.run(
                ["/bin/sh", str(ROOT / "install.sh")], env=env,
                text=True, capture_output=True, timeout=10,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing system dependencies", result.stderr)
            self.assertIn("mpv", result.stderr)

    def test_dependency_bootstrap_installs_all_missing_runtime_packages_on_apt_system(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            fakebin = base / "bin"
            home = base / "home"
            fakebin.mkdir(); home.mkdir()
            dirname = shutil.which("dirname")
            self.assertIsNotNone(dirname)
            (fakebin / "dirname").symlink_to(Path(dirname))
            log = base / "apt.log"
            sudo = fakebin / "sudo"
            sudo.write_text("#!/bin/sh\nexec \"$@\"\n", encoding="utf-8")
            sudo.chmod(0o755)
            apt = fakebin / "apt-get"
            apt.write_text(
                "#!/bin/sh\n"
                "printf '%s\n' \"$*\" >> \"$APT_LOG\"\n"
                "if [ \"${1:-}\" = install ]; then\n"
                "  /bin/ln -s \"$REAL_PYTHON\" \"$FAKEBIN/python3\"\n"
                "  printf '%s\\n' '#!/bin/sh' \"printf '  x11  X11 software video output\\\\n'\" > \"$FAKEBIN/mpv\"\n"
                "  /bin/chmod +x \"$FAKEBIN/mpv\"\n"
                "fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            apt.chmod(0o755)
            env = os.environ.copy()
            env.update({"PATH": str(fakebin), "HOME": str(home),
                        "FAKEBIN": str(fakebin), "APT_LOG": str(log),
                        "REAL_PYTHON": str(Path(os.sys.executable)),
                        "PYTHONDONTWRITEBYTECODE": "1"})
            env.pop("SUDO_USER", None)
            result = subprocess.run(["/bin/sh", str(ROOT / "install.sh")], env=env,
                                    text=True, capture_output=True, timeout=40)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            calls = log.read_text(encoding="utf-8")
            self.assertIn("update", calls)
            self.assertIn("install -y python3 mpv", calls)
            self.assertTrue((home / ".local/bin/manga-cli").is_file())


    def test_publication_polish_does_not_change_reader_source_contracts(self):
        self.assertTrue((ROOT / "acmanga/reader.py").is_file())
        self.assertTrue((ROOT / "acmanga/engine.py").is_file())


if __name__ == "__main__":
    unittest.main()
