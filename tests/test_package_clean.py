import hashlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
READER_HASH = "8f481504d6f39d0479e2dfae44c084db66f89d0282a06a1a52de477dd0414860"
FROZEN_BASE = {
    "acmanga/sources/mangakatana.py": "41187fd24af65849dba7ece55036e2583136e6316530b5a9dfb0fe697c0e6d52",
}


class PackageCleanTests(unittest.TestCase):
    def test_only_two_provider_modules_exist(self):
        source_dir = ROOT / "acmanga" / "sources"
        modules = sorted(p.stem for p in source_dir.glob("*.py") if p.name not in ("__init__.py", "base.py") and not p.name.startswith("__"))
        self.assertEqual(modules, ["mangakatana", "mangapill"])

    def test_removed_protocol_module_does_not_exist(self):
        self.assertFalse((ROOT / "acmanga" / "proto.py").exists())

    def test_reader_release_integrity(self):
        import manga
        self.assertTrue(manga.reader_integrity_ok())

    def test_mangakatana_parser_release_integrity(self):
        for rel, expected in FROZEN_BASE.items():
            digest = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
            self.assertEqual(digest, expected, rel)

    def test_version_is_081(self):
        self.assertEqual((ROOT / "VERSION").read_text(encoding="utf-8").strip(), "0.8.1")

    def test_chapter_screen_has_no_redundant_c_continue_hint(self):
        text = (ROOT / "manga.py").read_text(encoding="utf-8")
        self.assertNotIn('[("C", "continuar"), ("J", "capitulo")', text)

    def test_unknown_author_placeholder_is_not_emitted(self):
        text = (ROOT / "manga.py").read_text(encoding="utf-8")
        self.assertNotIn("Autor no indicado", text)
        self.assertNotIn("AUTOR NO INDICADO", text)

    def test_project_does_not_manage_global_mpv_config(self):
        needles = ('.config/mpv', 'mpv.conf', '--cache=', '--cache-secs=', '--demuxer-max-bytes=')
        files = [ROOT/'acmanga/reader.py', ROOT/'tools/install.py', ROOT/'install.sh']
        text = '\n'.join(path.read_text(encoding='utf-8') for path in files)
        for needle in needles:
            self.assertNotIn(needle, text)

    def test_no_compiled_or_temp_payload_in_source_tree(self):
        bad = []
        for path in ROOT.rglob("*"):
            if path.is_file() and (path.suffix == ".part" or path.name.endswith("~")):
                bad.append(str(path.relative_to(ROOT)))
        self.assertFalse(bad, bad)


if __name__ == "__main__":
    unittest.main()
