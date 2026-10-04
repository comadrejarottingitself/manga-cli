import unittest
from unittest import mock

import manga
from acmanga.errors import MangaError


class FakeReader:
    instances = []

    def __init__(self, cache_dir, preferences=None):
        self.cache_dir = cache_dir
        self.preferences = preferences or {}
        self.next_chapter = None
        self.notes = []
        self.messages = []
        FakeReader.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def note(self, text):
        self.notes.append(text)

    def send(self, name, *args):
        self.messages.append((name, args))

    def close(self):
        pass


class Engine:
    def __init__(self, alternates=None):
        self.preferences = {}
        self.alternates = alternates or {}

    def chapter_alternates(self, chapter):
        return [dict(x) for x in self.alternates.get(chapter['id'], [])]

    def next_chapter(self, chapters, current):
        for i, chapter in enumerate(chapters):
            if chapter['id'] == current['id']:
                return chapters[i + 1] if i + 1 < len(chapters) else None
        return None

    def previous_chapter(self, chapters, current):
        for i, chapter in enumerate(chapters):
            if chapter['id'] == current['id']:
                return chapters[i - 1] if i > 0 else None
        return None


class ReadSequenceTests(unittest.TestCase):
    def setUp(self):
        FakeReader.instances.clear()
        self.chapters = [
            {'source': 'mangakatana', 'id': 'c1', 'number': '1', 'name': 'Chapter 1'},
            {'source': 'mangakatana', 'id': 'c2', 'number': '2', 'name': 'Chapter 2'},
            {'source': 'mangakatana', 'id': 'c3', 'number': '3', 'name': 'Chapter 3'},
        ]
        self.work = {'key': 'manga:x', 'title': 'X'}

    def quiet(self):
        return mock.patch.multiple(
            manga.ui,
            clear=mock.DEFAULT, brand=mock.DEFAULT, banner=mock.DEFAULT,
            key_hint=mock.DEFAULT, status=mock.DEFAULT,
            compact=lambda text, width: text or '', width=lambda: 100,
        )

    def test_last_page_continues_to_next_chapter_page_one_same_session(self):
        calls = []

        def fake_view(engine, state_file, state, work, chapter, cache_dir,
                      start_page=1, progress_callback=None, session=None):
            calls.append((chapter['id'], start_page, id(session)))
            if chapter['id'] == 'c1':
                return {'completed': True, 'page': 20, 'total': 20}
            return {'completed': False, 'page': 1, 'total': 18}

        with self.quiet(), mock.patch.object(manga, 'ReaderSession', FakeReader), \
             mock.patch.object(manga, 'view', side_effect=fake_view), \
             mock.patch.object(manga, 'is_saved', return_value=False), \
             mock.patch('builtins.print'):
            result = manga.read_sequence(Engine(), {}, self.work, self.chapters,
                                         self.chapters[0], start_page=20)

        self.assertEqual([(c[0], c[1]) for c in calls], [('c1', 20), ('c2', 1)])
        self.assertEqual(calls[0][2], calls[1][2], 'mpv session should be reused')
        self.assertIn('page 1/18 saved', result)
        self.assertTrue(any('Ch. 1' in n and 'Ch. 2' in n for n in FakeReader.instances[0].notes))

    def test_first_page_previous_opens_previous_chapter_at_last_page(self):
        calls = []

        def fake_view(engine, state_file, state, work, chapter, cache_dir,
                      start_page=1, progress_callback=None, session=None):
            calls.append((chapter['id'], start_page))
            if chapter['id'] == 'c2':
                return {'completed': False, 'previous': True, 'page': 1, 'total': 20}
            return {'completed': False, 'page': 14, 'total': 14}

        with self.quiet(), mock.patch.object(manga, 'ReaderSession', FakeReader), \
             mock.patch.object(manga, 'view', side_effect=fake_view), \
             mock.patch.object(manga, 'is_saved', return_value=False), \
             mock.patch('builtins.print'):
            result = manga.read_sequence(Engine(), {}, self.work, self.chapters,
                                         self.chapters[1], start_page=1)

        self.assertEqual(calls, [('c2', 1), ('c1', -1)])
        self.assertIn('page 14/14 saved', result)

    def test_source_fallback_does_not_break_canonical_next_chapter(self):
        alternate = {'source': 'mangapill', 'id': 'mp-c1', 'number': '1', 'name': 'Chapter 1'}
        engine = Engine({'c1': [alternate]})
        calls = []

        def fake_view(engine_obj, state_file, state, work, chapter, cache_dir,
                      start_page=1, progress_callback=None, session=None):
            calls.append((chapter['source'], chapter['id'], start_page))
            if chapter['source'] == 'mangakatana' and chapter['id'] == 'c1':
                raise MangaError('fuente temporalmente no disponible')
            if chapter['id'] == 'mp-c1':
                return {'completed': True, 'page': 20, 'total': 20}
            return {'completed': False, 'page': 1, 'total': 18}

        with self.quiet(), mock.patch.object(manga, 'ReaderSession', FakeReader), \
             mock.patch.object(manga, 'view', side_effect=fake_view), \
             mock.patch.object(manga, 'is_saved', return_value=False), \
             mock.patch('builtins.print'):
            manga.read_sequence(engine, {}, self.work, self.chapters,
                                self.chapters[0], start_page=20)

        self.assertEqual(calls[:3], [
            ('mangakatana', 'c1', 20),
            ('mangapill', 'mp-c1', 20),
            ('mangakatana', 'c2', 1),
        ])


if __name__ == '__main__':
    unittest.main()
