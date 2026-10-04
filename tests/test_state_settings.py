import json
import tempfile
import unittest
from pathlib import Path

from acmanga.settings import load_settings, save_settings
from acmanga.state import load_state, save_manga, save_progress, save_state, progress_for, is_saved


class MigrationTests(unittest.TestCase):
    def test_retired_source_is_removed_but_semantic_progress_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            old = {
                'schema': 2,
                'saved': {'work': {'key': 'work', 'title': 'Work', 'author': 'A', 'saved_at': 1,
                    'variants': [{'source': 'retired-provider', 'id': 'old', 'title': 'Work'}]}},
                'progress': {'work': {'title': 'Work', 'source': 'retired-provider', 'chapter_id': 'old-c',
                    'chapter_number': '17', 'page': 8, 'total_pages': 21}},
                'last_read': 'work',
            }
            path.write_text(json.dumps(old), encoding='utf-8')
            state = load_state(path)
            key = 'legacy:work'
            self.assertEqual(state['saved'][key]['variants'], [])
            self.assertEqual(state['progress'][key]['source'], '')
            self.assertIsNone(state['progress'][key]['chapter_id'])
            self.assertEqual(state['progress'][key]['chapter_number'], '17')
            self.assertEqual(state['progress'][key]['page'], 8)
            self.assertEqual(state['last_read'], key)
            save_state(path, state)
            self.assertNotIn('retired-provider', path.read_text(encoding='utf-8'))

    def test_active_variant_migrates_to_stable_provider_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            data = {'schema': 3, 'saved': {'x': {'key': 'x', 'title': 'X', 'variants': [
                {'source': 'mangakatana', 'id': 'x.1', 'title': 'X', 'language': 'en', 'ref': {'id': 'x.1'}}
            ]}}, 'progress': {'x': {'title': 'X', 'source': 'mangakatana', 'chapter_id': 'c1',
                                   'title_id': 'x.1', 'chapter_number': '1', 'page': 4}}, 'last_read': 'x'}
            path.write_text(json.dumps(data), encoding='utf-8')
            state = load_state(path)
            key = 'mangakatana:x.1'
            self.assertEqual(state['saved'][key]['variants'][0]['source'], 'mangakatana')
            self.assertEqual(state['progress'][key]['page'], 4)
            self.assertEqual(state['last_read'], key)

    def test_legacy_saved_item_rekeys_when_provider_is_rediscovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            path.write_text(json.dumps({
                'schema': 2,
                'saved': {'old': {'title': 'Kokou no Hito', 'variants': []}},
                'progress': {'old': {'title': 'Kokou no Hito', 'chapter_number': '10', 'page': 7}},
                'last_read': 'old',
            }), encoding='utf-8')
            state = load_state(path)
            manga = {'title': 'Kokou no Hito', 'author': 'Sakamoto', 'variants': [
                {'source': 'mangakatana', 'id': 'kokou.1', 'title': 'Kokou no Hito', 'ref': {'id': 'kokou.1'}}
            ]}
            saved = save_manga(path, state, manga)
            self.assertEqual(saved['key'], 'mangakatana:kokou.1')
            self.assertNotIn('legacy:kokou no hito', state['saved'])
            self.assertEqual(state['progress']['mangakatana:kokou.1']['page'], 7)
            self.assertEqual(state['last_read'], 'mangakatana:kokou.1')

    def test_progress_keeps_group_and_source_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            state = {'schema': 4, 'saved': {}, 'progress': {}, 'last_read': None}
            manga = {'title': 'Berserk', 'identity': 'mangapill:1/berserk', 'key': 'mangapill:1/berserk', 'variants': [
                {'source': 'mangapill', 'id': '1/berserk', 'title': 'Berserk', 'ref': {'id': '1/berserk'}}
            ]}
            save_manga(path, state, manga)
            chapter = {'source': 'mangapill', 'id': 'mpabc', 'title_id': '1/berserk', 'number': '1',
                       'name': 'Berserk Group 2 Chapter 1', 'groups': ['Group 2'],
                       'source_ref': {'manga_id': '1/berserk', 'chapter_ref': '1-2/berserk-group-2-chapter-1'}}
            save_progress(path, state, manga, chapter, 8, 37)
            rec = progress_for(state, manga)
            self.assertEqual(rec['groups'], ['Group 2'])
            self.assertEqual(rec['source_ref']['chapter_ref'], '1-2/berserk-group-2-chapter-1')
            self.assertEqual(rec['page'], 8)

    def test_same_title_different_provider_identity_can_coexist(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            state = {'schema': 4, 'saved': {}, 'progress': {}, 'last_read': None}
            a = {'title': 'Bibliomania', 'identity': 'mangapill:6082/bibliomania', 'key': 'mangapill:6082/bibliomania',
                 'variants': [{'source': 'mangapill', 'id': '6082/bibliomania', 'title': 'Bibliomania', 'ref': {'id': '6082/bibliomania'}}]}
            b = {'title': 'Bibliomania', 'identity': 'mangapill:8526/bibliomania', 'key': 'mangapill:8526/bibliomania',
                 'variants': [{'source': 'mangapill', 'id': '8526/bibliomania', 'title': 'Bibliomania', 'ref': {'id': '8526/bibliomania'}}]}
            save_manga(path, state, a)
            save_manga(path, state, b)
            self.assertEqual(len(state['saved']), 2)
            self.assertTrue(is_saved(state, a))
            self.assertTrue(is_saved(state, b))


    def test_malformed_state_fields_do_not_crash_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            path.write_text(json.dumps({
                'schema': 3,
                'saved': {'x': {'title': 'X', 'saved_at': 'not-a-number', 'aliases': 'wrong', 'genres': 123, 'variants': []}},
                'progress': [],
                'last_read': 'x',
            }), encoding='utf-8')
            state = load_state(path)
            self.assertIn('legacy:x', state['saved'])
            self.assertIsInstance(state['saved']['legacy:x']['saved_at'], int)
            self.assertEqual(state['saved']['legacy:x']['aliases'], [])

    def test_rekey_into_existing_identity_removes_legacy_duplicate_and_keeps_newer_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            state = {
                'schema': 4,
                'saved': {
                    'legacy:x': {'key': 'legacy:x', 'identity': 'legacy:x', 'title': 'X', 'author': '', 'aliases': [], 'year': '', 'status': '', 'type': 'manga', 'genres': [], 'variants': [], 'saved_at': 1},
                    'mangakatana:x.1': {'key': 'mangakatana:x.1', 'identity': 'mangakatana:x.1', 'title': 'X', 'author': '', 'aliases': [], 'year': '', 'status': '', 'type': 'manga', 'genres': [], 'variants': [{'source': 'mangakatana', 'id': 'x.1', 'title': 'X', 'ref': {'id': 'x.1'}}], 'saved_at': 2},
                },
                'progress': {
                    'legacy:x': {'title': 'X', 'source': '', 'chapter_number': '5', 'page': 9, 'total_pages': 10, 'updated': 20},
                    'mangakatana:x.1': {'title': 'X', 'source': 'mangakatana', 'chapter_id': 'old', 'title_id': 'x.1', 'chapter_number': '4', 'page': 2, 'total_pages': 10, 'updated': 10},
                },
                'last_read': 'legacy:x',
            }
            manga = {'key': 'mangakatana:x.1', 'identity': 'mangakatana:x.1', 'title': 'X', 'variants': [{'source': 'mangakatana', 'id': 'x.1', 'title': 'X', 'ref': {'id': 'x.1'}}]}
            # Simula redescubrimiento del legacy llamando con identidad de provider y sin key coincidente en el objeto legacy.
            from acmanga import state as state_mod
            state_mod._move_key(state, 'legacy:x', 'mangakatana:x.1')
            self.assertNotIn('legacy:x', state['saved'])
            self.assertNotIn('legacy:x', state['progress'])
            self.assertEqual(state['progress']['mangakatana:x.1']['page'], 9)
            self.assertEqual(state['last_read'], 'mangakatana:x.1')

    def test_unsaved_legacy_progress_keeps_last_read_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            path.write_text(json.dumps({
                'schema': 3, 'saved': {},
                'progress': {'old-reading': {'title': 'Variant', 'source': 'mangakatana',
                    'chapter_id': 'c3', 'title_id': 'variant.1', 'chapter_number': '3', 'page': 5}},
                'last_read': 'old-reading',
            }), encoding='utf-8')
            state = load_state(path)
            self.assertEqual(state['last_read'], 'legacy:variant')
            self.assertEqual(state['progress']['legacy:variant']['page'], 5)

    def test_old_settings_normalize_to_fast_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings.json'
            path.write_text(json.dumps({'schema': 1, 'source_mode': 'retired-provider',
                                        'source_priority': ['retired-provider'], 'saved_sort': 'title',
                                        'obsolete_language': 'xx'}), encoding='utf-8')
            settings = load_settings(path)
            self.assertEqual(settings['source_mode'], 'auto')
            self.assertEqual(settings['source_priority'], ['mangakatana', 'mangapill'])
            self.assertEqual(settings['saved_sort'], 'title')
            save_settings(path, settings)
            serialized = path.read_text(encoding='utf-8')
            self.assertNotIn('retired-provider', serialized)
            self.assertNotIn('obsolete_language', serialized)


if __name__ == '__main__':
    unittest.main()
