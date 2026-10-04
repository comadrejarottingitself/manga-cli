import unittest
from unittest import mock

import manga
from acmanga.engine import MultiSourceEngine
from acmanga.errors import SourceError


class DummySource:
    def __init__(self, key, search_result=None):
        self.key = key
        self.name = key
        self.search_result = search_result

    def search(self, query):
        return [dict(self.search_result)] if self.search_result else []

    def chapters_all(self, ref):
        return {'items': []}

    def diagnostic(self):
        return {'source': self.key, 'ok': True, 'detail': 'ok'}


class RuntimeFlowTests(unittest.TestCase):
    def quiet_ui(self):
        return mock.patch.multiple(
            manga.ui,
            clear=mock.DEFAULT,
            brand=mock.DEFAULT,
            banner=mock.DEFAULT,
            key_hint=mock.DEFAULT,
            status=mock.DEFAULT,
            badge=mock.DEFAULT,
            paint=mock.DEFAULT,
        )

    def test_reader_falls_back_to_alternate_source(self):
        class Engine:
            def chapter_alternates(self, chapter):
                return [{'source': 'mangapill', 'id': 'mp1', 'number': '1', 'name': 'Chapter 1'}]

            def next_chapter(self, chapters, current):
                return None

        primary = {'source': 'mangakatana', 'id': 'mk1', 'number': '1', 'name': 'Chapter 1',
                   '_alternates': [{'source': 'mangapill', 'id': 'mp1', 'number': '1', 'name': 'Chapter 1'}]}
        calls = []

        def fake_view(engine, state_file, state, work, chapter, cache_dir, start_page=1, progress_callback=None, session=None):
            calls.append(chapter['source'])
            if chapter['source'] == 'mangakatana':
                raise SourceError('temporary source failure', source='mangakatana')
            return {'completed': False, 'page': 4, 'total': 18}

        with self.quiet_ui(), mock.patch.object(manga, 'view', side_effect=fake_view), mock.patch('builtins.print'):
            result = manga.read_sequence(Engine(), {}, {'key': 'x', 'title': 'X'}, [primary], primary, start_page=4)
        self.assertEqual(calls, ['mangakatana', 'mangapill'])
        self.assertIn('page 4/18 saved', result)

    def test_reader_reports_both_failures_without_traceback(self):
        class Engine:
            def chapter_alternates(self, chapter):
                return [{'source': 'mangapill', 'id': 'mp1', 'number': '1', 'name': 'Chapter 1'}]

        primary = {'source': 'mangakatana', 'id': 'mk1', 'number': '1', 'name': 'Chapter 1'}
        with self.quiet_ui(), mock.patch.object(manga, 'view', side_effect=SourceError('offline')), mock.patch.object(manga, 'pause'), mock.patch('builtins.print'):
            result = manga.read_sequence(Engine(), {}, {'key': 'x', 'title': 'X'}, [primary], primary)
        self.assertEqual(result, 'Could not open Ch. 1 · Chapter 1')

    def test_migrated_saved_title_can_be_rediscovered_on_new_sources(self):
        a = DummySource('mangakatana', {
            'source': 'mangakatana', 'id': 'mk', 'title': 'Kokou no Hito', 'author': '', 'language': 'en', 'ref': {'id': 'mk'}
        })
        b = DummySource('mangapill', {
            'source': 'mangapill', 'id': 'mp', 'title': 'Kokou no Hito', 'author': '', 'language': 'en', 'ref': {'id': 'mp'}
        })
        engine = MultiSourceEngine([a, b])
        old = {'key': 'kokou no hito', 'title': 'Kokou no Hito', 'author': '', 'variants': []}
        refreshed, errors = engine.refresh_manga(old)
        self.assertFalse(errors)
        self.assertEqual([v['source'] for v in refreshed['variants']], ['mangakatana', 'mangapill'])

    def test_legacy_progress_maps_by_chapter_number(self):
        engine = manga.make_engine()
        progress = {'source': '', 'chapter_id': None, 'chapter_number': '12', 'chapter_name': 'Chapter 12'}
        marker = engine.chapter_from_progress({'key': 'x'}, progress)
        self.assertEqual(marker['number'], '12')
        chapters = [
            {'source': 'mangakatana', 'id': '13', 'number': '13'},
            {'source': 'mangakatana', 'id': '12', 'number': '12'},
            {'source': 'mangakatana', 'id': '11', 'number': '11'},
        ]
        idx, found = engine.find_chapter(chapters, marker['number'])
        self.assertEqual(idx, 1)
        self.assertEqual(found['id'], '12')


    def test_completed_chapter_opens_next_automatically_from_page_one(self):
        class Engine:
            def chapter_alternates(self, chapter):
                return []

            def next_chapter(self, chapters, current):
                if current.get('id') == 'c1':
                    return chapters[1]
                return None

        chapters = [
            {'source': 'mangakatana', 'id': 'c1', 'number': '1', 'name': 'Chapter 1'},
            {'source': 'mangakatana', 'id': 'c2', 'number': '2', 'name': 'Chapter 2'},
        ]
        calls = []

        def fake_view(engine, state_file, state, work, chapter, cache_dir, start_page=1, progress_callback=None, session=None):
            calls.append((chapter['id'], start_page))
            if chapter['id'] == 'c1':
                return {'completed': True, 'page': 20, 'total': 20}
            return {'completed': False, 'page': 1, 'total': 18}

        with self.quiet_ui(), mock.patch.object(manga, 'view', side_effect=fake_view), mock.patch.object(manga.time, 'sleep'), mock.patch('builtins.print'):
            result = manga.read_sequence(Engine(), {}, {'key': 'x', 'title': 'X'}, chapters, chapters[0], start_page=20)
        self.assertEqual(calls, [('c1', 20), ('c2', 1)])
        self.assertIn('Ch. 2', result)
        self.assertIn('page 1/18 saved', result)

    def test_chapter_screen_enter_resumes_selected_progress_without_c_shortcut(self):
        chapters = [
            {'source': 'mangakatana', 'id': 'c3', 'number': '3', 'name': 'Chapter 3'},
            {'source': 'mangakatana', 'id': 'c2', 'number': '2', 'name': 'Chapter 2'},
            {'source': 'mangakatana', 'id': 'c1', 'number': '1', 'name': 'Chapter 1'},
        ]
        progress = {
            'source': 'mangakatana', 'chapter_id': 'c2', 'chapter_number': '2',
            'page': 7, 'total_pages': 20,
        }

        class Engine:
            def chapters(self, manga_item, force=False):
                return chapters, {}, {'mangakatana': len(chapters)}

            def chapter_from_progress(self, manga_item, progress_item):
                return None

            def _chapter_key(self, chapter):
                return ('number', str(chapter.get('number')))

        with self.quiet_ui(), \
             mock.patch.object(manga, 'progress_for', return_value=progress), \
             mock.patch.object(manga, 'is_saved', return_value=False), \
             mock.patch.object(manga, 'render_manga'), \
             mock.patch.object(manga.ui, 'read_key', side_effect=['enter', 'esc']), \
             mock.patch.object(manga, 'read_sequence', return_value='guardado') as reader, \
             mock.patch('builtins.print'):
            manga.chapter_flow(Engine(), {}, {'key': 'x', 'title': 'X'})

        reader.assert_called_once()
        args, kwargs = reader.call_args
        self.assertEqual(args[4]['id'], 'c2')
        self.assertEqual(kwargs['start_page'], 7)



if __name__ == '__main__':
    unittest.main()
