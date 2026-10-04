import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from acmanga.errors import SourceError
from acmanga.util import download_image_set

JPEG = b"\xff\xd8\xff" + (b"J" * 80)
PNG = b"\x89PNG\r\n\x1a\n" + (b"P" * 80)


def stream_writer(calls, active=None):
    lock = threading.Lock()
    def fake(url, source, dest_tmp, timeout, headers, retries=2, max_bytes=None):
        if active is not None:
            with lock:
                active["n"] += 1
                active["peak"] = max(active["peak"], active["n"])
            time.sleep(0.02)
        payload = PNG if url.endswith("2") else JPEG
        Path(dest_tmp).write_bytes(payload)
        with lock:
            calls.append(url)
            if active is not None:
                active["n"] -= 1
        return payload[:32], len(payload)
    return fake


class DownloadTests(unittest.TestCase):
    def test_parallel_download_order_and_cache(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch("acmanga.util._stream_image", side_effect=stream_writer(calls)):
            pages = download_image_set(["https://x/1", "https://x/2", "https://x/3"], tmp, "Test", "https://ref",
                                       workers=3, cache_identity="manga:chapter")
            self.assertEqual([p.name[:3] for p in pages], ["001", "002", "003"])
            self.assertEqual(len(calls), 3)
            again = download_image_set(["https://x/1", "https://x/2", "https://x/3"], tmp, "Test", "https://ref",
                                       workers=3, cache_identity="manga:chapter")
            self.assertEqual(len(calls), 3)
            self.assertEqual([p.name for p in again], [p.name for p in pages])

    def test_cache_identity_change_forces_redownload(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch("acmanga.util._stream_image", side_effect=stream_writer(calls)):
            urls = ["https://x/1"]
            download_image_set(urls, tmp, "Test", "https://ref", workers=1, cache_identity="berserk:c1")
            download_image_set(urls, tmp, "Test", "https://ref", workers=1, cache_identity="freesia:c1")
        self.assertEqual(len(calls), 2)

    def test_url_change_forces_redownload(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch("acmanga.util._stream_image", side_effect=stream_writer(calls)):
            download_image_set(["https://x/1"], tmp, "Test", "https://ref", workers=1, cache_identity="same")
            download_image_set(["https://y/1"], tmp, "Test", "https://ref", workers=1, cache_identity="same")
        self.assertEqual(len(calls), 2)

    def test_manifest_binds_identity_and_url_fingerprint(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch("acmanga.util._stream_image", side_effect=stream_writer(calls)):
            download_image_set(["https://x/1", "https://x/2"], tmp, "Test", "https://ref", workers=1,
                               cache_identity="mangakatana:berserk:c1")
            manifest = json.loads((Path(tmp) / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], 2)
        self.assertEqual(manifest["identity"], "mangakatana:berserk:c1")
        self.assertEqual(len(manifest["urls_sha256"]), 64)
        self.assertEqual(manifest["count"], 2)

    def test_corrupt_cached_page_forces_redownload(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch("acmanga.util._stream_image", side_effect=stream_writer(calls)):
            pages = download_image_set(["https://x/1"], tmp, "Test", "https://ref", workers=1, cache_identity="id")
            pages[0].write_bytes(b"<html>corrupt cached payload</html>" * 3)
            again = download_image_set(["https://x/1"], tmp, "Test", "https://ref", workers=1, cache_identity="id")
            self.assertEqual(len(calls), 2)
            self.assertTrue(again[0].read_bytes().startswith(b"\xff\xd8\xff"))

    def test_partial_download_failure_is_cleaned(self):
        def fake(url, source, dest_tmp, timeout, headers, retries=2, max_bytes=None):
            if url.endswith("2"):
                Path(dest_tmp).write_bytes(b"partial")
                raise SourceError("broken", kind="download", transient=True)
            Path(dest_tmp).write_bytes(JPEG)
            return JPEG[:32], len(JPEG)
        with tempfile.TemporaryDirectory() as tmp, mock.patch("acmanga.util._stream_image", side_effect=fake):
            with self.assertRaises(SourceError):
                download_image_set(["https://x/1", "https://x/2"], tmp, "Test", "https://ref", workers=2,
                                   cache_identity="id")
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_worker_override_is_clamped_to_three(self):
        calls = []
        active = {"n": 0, "peak": 0}
        with tempfile.TemporaryDirectory() as tmp, mock.patch("acmanga.util._stream_image", side_effect=stream_writer(calls, active)), \
             mock.patch.dict("os.environ", {"ACMANGA_DOWNLOAD_WORKERS": "99"}):
            download_image_set(["https://x/{}".format(i) for i in range(8)], tmp, "Test", "https://ref", workers=3,
                               cache_identity="id")
        self.assertLessEqual(active["peak"], 3)
        self.assertGreaterEqual(active["peak"], 2)


if __name__ == "__main__":
    unittest.main()
