import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import manga
from acmanga.state import (
    availability_for,
    load_state,
    mark_updates_seen,
    save_manga,
    update_availability,
)


def work(title='Berserk'):
    return {
        'title': title,
        'identity': 'mangakatana:{}.1'.format(title.lower().replace(' ', '-')),
        'key': 'mangakatana:{}.1'.format(title.lower().replace(' ', '-')),
        'variants': [
            {
                'source': 'mangakatana',
                'id': '{}.1'.format(title.lower().replace(' ', '-')),
                'title': title,
                'ref': {'id': '{}.1'.format(title.lower().replace(' ', '-'))},
            }
        ],
    }


class AvailabilityStateTests(unittest.TestCase):
    def test_schema4_migrates_with_empty_availability_without_losing_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            path.write_text(json.dumps({
                'schema': 4,
                'saved': {
                    'mangakatana:berserk.1': {
                        'key': 'mangakatana:berserk.1', 'identity': 'mangakatana:berserk.1',
                        'title': 'Berserk', 'variants': [
                            {'source': 'mangakatana', 'id': 'berserk.1', 'title': 'Berserk', 'ref': {'id': 'berserk.1'}}
                        ],
                    }
                },
                'progress': {
                    'mangakatana:berserk.1': {
                        'title': 'Berserk', 'source': 'mangakatana', 'chapter_id': 'berserk.1:c128',
                        'title_id': 'berserk.1', 'chapter_number': '128', 'page': 14, 'total_pages': 22,
                    }
                },
                'last_read': 'mangakatana:berserk.1',
            }), encoding='utf-8')
            state = load_state(path)
            self.assertEqual(state['schema'], 6)
            self.assertEqual(state['progress']['mangakatana:berserk.1']['page'], 14)
            self.assertEqual(state['saved']['mangakatana:berserk.1']['availability']['status'], 'never')
            self.assertEqual(state['saved']['mangakatana:berserk.1']['availability']['known_total'], 0)

    def test_first_check_is_baseline_second_check_marks_only_new_chapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            state = {'schema': 5, 'saved': {}, 'progress': {}, 'last_read': None}
            manga_item = work()
            save_manga(path, state, manga_item)
            first = update_availability(path, state, manga_item, ['n:2', 'n:1'], '2', {'mangakatana': 2}, attempted_at=100)
            self.assertEqual(first['known_total'], 2)
            self.assertEqual(first['new_keys'], [])
            second = update_availability(path, state, manga_item, ['n:3', 'n:2', 'n:1'], '3', {'mangakatana': 3}, attempted_at=200)
            self.assertEqual(second['known_total'], 3)
            self.assertEqual(second['new_keys'], ['n:3'])
            self.assertEqual(second['checked_at'], 200)

    def test_unseen_updates_survive_checks_until_chapter_list_is_opened(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            state = {'schema': 5, 'saved': {}, 'progress': {}, 'last_read': None}
            manga_item = work()
            save_manga(path, state, manga_item)
            update_availability(path, state, manga_item, ['n:2', 'n:1'], '2', {'mangakatana': 2}, attempted_at=100)
            update_availability(path, state, manga_item, ['n:3', 'n:2', 'n:1'], '3', {'mangakatana': 3}, attempted_at=200)
            third = update_availability(path, state, manga_item, ['n:3', 'n:2', 'n:1'], '3', {'mangakatana': 3}, attempted_at=300)
            self.assertEqual(third['new_keys'], ['n:3'])
            self.assertTrue(mark_updates_seen(path, state, manga_item))
            self.assertEqual(availability_for(state, manga_item)['new_keys'], [])

    def test_partial_or_failed_check_never_erases_last_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            state = {'schema': 5, 'saved': {}, 'progress': {}, 'last_read': None}
            manga_item = work()
            save_manga(path, state, manga_item)
            update_availability(path, state, manga_item, ['n:3', 'n:2', 'n:1'], '3', {'mangakatana': 3}, attempted_at=100)
            partial = update_availability(path, state, manga_item, ['n:3'], '3', {'mangakatana': 1, 'mangapill': 0}, errors={'mangapill': 'timeout'}, attempted_at=200)
            self.assertEqual(partial['status'], 'partial')
            self.assertEqual(partial['known_total'], 3)
            self.assertEqual(partial['known_keys'], ['n:3', 'n:2', 'n:1'])
            failed = update_availability(path, state, manga_item, [], '', {}, errors={'mangakatana': 'offline'}, attempted_at=300)
            self.assertEqual(failed['status'], 'error')
            self.assertEqual(failed['known_total'], 3)
            self.assertEqual(failed['checked_at'], 100)

    def test_metadata_refresh_preserves_availability(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            state = {'schema': 5, 'saved': {}, 'progress': {}, 'last_read': None}
            manga_item = work()
            save_manga(path, state, manga_item)
            update_availability(path, state, manga_item, ['n:1'], '1', {'mangakatana': 1}, attempted_at=100)
            refreshed = dict(manga_item, author='Miura Kentaro')
            saved = save_manga(path, state, refreshed)
            self.assertEqual(saved['author'], 'Miura Kentaro')
            self.assertEqual(saved['availability']['known_total'], 1)
            self.assertEqual(saved['availability']['checked_at'], 100)


class SavedUpdateFlowTests(unittest.TestCase):
    def test_snapshot_keys_are_source_neutral_but_keep_groups_separate(self):
        engine = manga.make_engine()
        chapters = [
            {'source': 'mangakatana', 'id': 'mk2', 'number': '2', 'name': 'Chapter 2', 'groups': []},
            {'source': 'mangapill', 'id': 'mp1g', 'number': '1', 'name': 'Group 2 Chapter 1', 'groups': ['Group 2']},
            {'source': 'mangakatana', 'id': 'mk1', 'number': '1', 'name': 'Chapter 1', 'groups': []},
        ]
        keys = manga.chapter_snapshot_keys(engine, chapters)
        self.assertEqual(len(keys), 3)
        self.assertEqual(len(set(keys)), 3)
        self.assertIn('n:1.0000|g:', keys)
        self.assertIn('n:1.0000|g:group 2', keys)

    def test_update_saved_entry_forces_all_known_variants_without_search(self):
        class Engine:
            def __init__(self):
                self.invalidated = []
                self.calls = []
            def invalidate_chapters(self, item):
                self.invalidated.append(item['key'])
            def chapters(self, item, force=False, all_sources=False):
                self.calls.append((force, all_sources, item['key']))
                return ([
                    {'source': 'mangakatana', 'id': 'mk2', 'number': '2', 'name': 'Chapter 2', 'groups': []},
                    {'source': 'mangakatana', 'id': 'mk1', 'number': '1', 'name': 'Chapter 1', 'groups': []},
                ], {}, {'mangakatana': 2})
            @staticmethod
            def _chapter_key(chapter):
                return 'n:{:.4f}'.format(float(chapter['number']))
            @staticmethod
            def _group_key(chapter):
                return ''
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            state = {'schema': 5, 'saved': {}, 'progress': {}, 'last_read': None}
            manga_item = work()
            save_manga(path, state, manga_item)
            engine = Engine()
            with mock.patch.object(manga, 'STATE_FILE', path):
                availability, errors = manga.update_saved_entry(engine, state, state['saved'][manga_item['key']])
            self.assertFalse(errors)
            self.assertEqual(engine.calls, [(True, True, manga_item['key'])])
            self.assertEqual(availability['known_total'], 2)
            self.assertEqual(availability['latest_number'], '2')

    def test_status_text_is_compact_and_distinguishes_updates_and_errors(self):
        self.assertEqual(manga.availability_status_text({'status': 'never'}), 'Not checked')
        self.assertEqual(manga.availability_status_text({'status': 'ok', 'known_total': 404, 'new_keys': ['x', 'y']}), '404 ch. · +2 new')
        self.assertEqual(manga.availability_status_text({'status': 'error', 'known_total': 327}), '327 ch. · Not updated')

    def test_direct_reader_success_marks_saved_updates_seen_but_failure_does_not(self):
        class Engine:
            def chapter_alternates(self, chapter):
                return []
            def next_chapter(self, chapters, current):
                return None
        chapter = {'source': 'mangakatana', 'id': 'mk1', 'number': '1', 'name': 'Chapter 1'}
        result = {'completed': False, 'page': 1, 'total': 20}
        with mock.patch.object(manga, 'is_saved', return_value=True), \
             mock.patch.object(manga, 'mark_updates_seen') as seen, \
             mock.patch.object(manga, 'view', return_value=result), \
             mock.patch.object(manga.ui, 'clear'), mock.patch.object(manga.ui, 'brand'), \
             mock.patch.object(manga.ui, 'banner'), mock.patch.object(manga.ui, 'badge', return_value=''), \
             mock.patch.object(manga.ui, 'paint', side_effect=lambda value, *a: str(value)), \
             mock.patch.object(manga.ui, 'key_hint'), mock.patch('builtins.print'):
            manga.read_sequence(Engine(), {}, {'key': 'x', 'title': 'X'}, [chapter], chapter)
        seen.assert_called_once()

        from acmanga.errors import SourceError
        with mock.patch.object(manga, 'is_saved', return_value=True), \
             mock.patch.object(manga, 'mark_updates_seen') as seen, \
             mock.patch.object(manga, 'view', side_effect=SourceError('offline')), \
             mock.patch.object(manga, 'pause'), mock.patch.object(manga.ui, 'clear'), \
             mock.patch.object(manga.ui, 'brand'), mock.patch.object(manga.ui, 'banner'), \
             mock.patch.object(manga.ui, 'badge', return_value=''), \
             mock.patch.object(manga.ui, 'paint', side_effect=lambda value, *a: str(value)), \
             mock.patch.object(manga.ui, 'key_hint'), mock.patch.object(manga.ui, 'status'), \
             mock.patch('builtins.print'):
            manga.read_sequence(Engine(), {}, {'key': 'x', 'title': 'X'}, [chapter], chapter)
        seen.assert_not_called()


if __name__ == '__main__':
    unittest.main()
