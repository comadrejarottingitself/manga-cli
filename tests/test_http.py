import gzip
import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from acmanga.errors import SourceError
from acmanga import util


class FakeResponse:
    def __init__(self, body, headers=None, url="https://example.test/final"):
        self.body = body
        self.headers = headers or {}
        self.url = url
        self.offset = 0
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def geturl(self):
        return self.url

    def read(self, size=-1):
        self.read_sizes.append(size)
        if size is None or size < 0:
            out = self.body[self.offset:]
            self.offset = len(self.body)
            return out
        out = self.body[self.offset:self.offset + size]
        self.offset += len(out)
        return out


class HttpTests(unittest.TestCase):
    def test_request_headers_are_lightweight_english(self):
        captured = {}
        def opener(req, timeout=None):
            captured['headers'] = {k.lower(): v for k, v in req.header_items()}
            captured['timeout'] = timeout
            return FakeResponse(b'ok')
        with mock.patch('urllib.request.urlopen', side_effect=opener):
            self.assertEqual(util.request_bytes('https://example.test/a', 'Unit', timeout=4, retries=0), b'ok')
        self.assertIn('firefox/128.0', captured['headers']['user-agent'].lower())
        self.assertEqual(captured['headers']['accept-language'], 'en-US,en;q=0.9')
        self.assertEqual(captured['headers']['accept-encoding'], 'gzip')
        self.assertEqual(captured['timeout'], 4)

    def test_gzip_is_decompressed(self):
        body = gzip.compress(b'<html>ok</html>')
        with mock.patch('urllib.request.urlopen', return_value=FakeResponse(body, {'Content-Encoding': 'gzip'})):
            self.assertEqual(util.request_bytes('https://example.test/a', 'Unit', retries=0), b'<html>ok</html>')

    def test_404_is_explicit_and_not_retried(self):
        err = urllib.error.HTTPError('https://x', 404, 'missing', {}, io.BytesIO())
        with mock.patch('urllib.request.urlopen', side_effect=err) as opened:
            with self.assertRaises(SourceError) as ctx:
                util.request_bytes('https://x', 'Unit', retries=3)
        self.assertEqual(ctx.exception.kind, 'not_found')
        self.assertFalse(ctx.exception.transient)
        self.assertEqual(opened.call_count, 1)

    def test_403_is_explicit_and_not_retried(self):
        err = urllib.error.HTTPError('https://x', 403, 'blocked', {}, io.BytesIO())
        with mock.patch('urllib.request.urlopen', side_effect=err) as opened:
            with self.assertRaises(SourceError) as ctx:
                util.request_bytes('https://x', 'Unit', retries=2)
        self.assertEqual(ctx.exception.kind, 'access')
        self.assertTrue(ctx.exception.transient)
        self.assertEqual(opened.call_count, 1)

    def test_429_retries_then_succeeds(self):
        err = urllib.error.HTTPError('https://x', 429, 'slow down', {'Retry-After': '0'}, io.BytesIO())
        with mock.patch('urllib.request.urlopen', side_effect=[err, FakeResponse(b'ok')]) as opened, mock.patch('time.sleep'):
            self.assertEqual(util.request_bytes('https://x', 'Unit', retries=1), b'ok')
        self.assertEqual(opened.call_count, 2)

    def test_url_error_becomes_transient_source_error(self):
        with mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('dns')), mock.patch('time.sleep'):
            with self.assertRaises(SourceError) as ctx:
                util.request_bytes('https://x', 'Unit', retries=1)
        self.assertEqual(ctx.exception.kind, 'network')
        self.assertTrue(ctx.exception.transient)

    def test_request_text_info_preserves_final_redirect_url(self):
        response = FakeResponse(b'<html><body>ok</body></html>', url='https://example.test/manga/final.1')
        with mock.patch('urllib.request.urlopen', return_value=response):
            text, final_url = util.request_text_info('https://example.test/search', 'Unit', retries=0)
        self.assertIn('<html>', text)
        self.assertEqual(final_url, 'https://example.test/manga/final.1')

    def test_expected_markers_are_soft_by_default(self):
        page = '<html><body><p>valid but changed layout</p></body></html>'
        self.assertEqual(util.validate_html(page, 'Unit', expected_any=['/manga/']), page)

    def test_invalid_json_is_explicit(self):
        with mock.patch('acmanga.util.request_bytes', return_value=b'not-json'):
            with self.assertRaises(SourceError) as ctx:
                util.request_json('https://x', 'Unit', retries=0)
        self.assertEqual(ctx.exception.kind, 'invalid_response')

    def test_challenge_html_is_not_misreported_as_zero_results(self):
        page = '<html><body><div id="cf-chl-widget">Checking your browser</div></body></html>'
        with self.assertRaises(SourceError) as ctx:
            util.validate_html(page, 'Unit', context='search', expected_any=['/manga/'])
        self.assertEqual(ctx.exception.kind, 'challenge')
        self.assertTrue(ctx.exception.transient)

    def test_unexpected_html_layout_is_parser_error(self):
        page = '<html><body><p>Maintenance page with enough content to be syntactically HTML</p></body></html>'
        with self.assertRaises(SourceError) as ctx:
            util.validate_html(page, 'Unit', context='search', expected_any=['/manga/'], strict=True)
        self.assertEqual(ctx.exception.kind, 'parser')

    def test_stream_image_reads_in_bounded_chunks(self):
        response = FakeResponse(b'\xff\xd8\xff' + b'J' * (140 * 1024))
        with tempfile.TemporaryDirectory() as tmp, mock.patch('urllib.request.urlopen', return_value=response):
            dest = Path(tmp) / 'page.part'
            head, size = util._stream_image('https://x/image.jpg', 'Unit', dest, timeout=2, headers={}, retries=0)
            self.assertTrue(head.startswith(b'\xff\xd8\xff'))
            self.assertEqual(size, dest.stat().st_size)
            self.assertTrue(all(n == 64 * 1024 for n in response.read_sizes[:-1]))
            self.assertEqual(response.read_sizes[-1], 64 * 1024)

    def test_stream_image_rejects_non_image_and_removes_partial(self):
        response = FakeResponse(b'<html>not an image</html>' * 10)
        with tempfile.TemporaryDirectory() as tmp, mock.patch('urllib.request.urlopen', return_value=response):
            dest = Path(tmp) / 'page.part'
            with self.assertRaises(SourceError) as ctx:
                util._stream_image('https://x/image.jpg', 'Unit', dest, timeout=2, headers={}, retries=0)
            self.assertEqual(ctx.exception.kind, 'invalid_response')
            self.assertFalse(dest.exists())

    def test_relevance_matches_words_not_substrings(self):
        self.assertLessEqual(util.relevance('Another Title', 'Kokou no Hito'), 0)
        self.assertGreater(util.relevance('Kokou no Hito', 'Kokou no Hito'), 900)

    def test_relevance_matches_internal_separator_variants(self):
        self.assertGreater(util.relevance('HIMA-TEN!', 'Himaten!'), 900)
        self.assertGreater(util.relevance('Hima Ten', 'Hima-Ten'), 900)
        self.assertNotEqual(util.normalize_title('HIMA-TEN!'), util.normalize_title('Himaten!'))

    def test_compact_matching_does_not_enable_compact_substring_matches(self):
        self.assertLessEqual(util.relevance('Hima-Ten Another Story', 'Himaten'), 0)
        self.assertLessEqual(util.relevance('AB', 'A B'), 0)

    def test_build_url_encodes_query(self):
        url = util.build_url('https://x/', '/search', {'q': 'Kokou no Hito', 'page': 1})
        self.assertIn('q=Kokou+no+Hito', url)
        self.assertIn('page=1', url)


if __name__ == '__main__':
    unittest.main()
