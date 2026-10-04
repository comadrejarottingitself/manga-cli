import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import manga
from acmanga import ui
from acmanga.state import load_state, save_progress, save_state


class HistoryTests(unittest.TestCase):
    def test_schema5_progress_becomes_history_without_new_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            path.write_text(json.dumps({
                'schema': 5,
                'saved': {},
                'progress': {
                    'mangakatana:vagabond.3120': {
                        'title': 'Vagabond', 'source': 'mangakatana', 'title_id': 'vagabond.3120',
                        'chapter_id': 'vagabond.3120:c100', 'chapter_number': '100',
                        'page': 7, 'total_pages': 23, 'updated': 200,
                    },
                    'legacy:older': {
                        'title': 'Older', 'source': '', 'chapter_number': '1',
                        'page': 2, 'total_pages': 10, 'updated': 100,
                    },
                },
                'last_read': 'mangakatana:vagabond.3120',
            }), encoding='utf-8')
            state = load_state(path)
            self.assertEqual(state['schema'], 6)
            self.assertEqual(set(state), {'schema', 'saved', 'progress', 'last_read'})
            items = manga.history_items(state)
            self.assertEqual([item['manga']['title'] for item in items], ['Vagabond', 'Older'])
            self.assertEqual(items[0]['manga']['variants'][0]['source'], 'mangakatana')
            self.assertEqual(items[0]['manga']['variants'][0]['id'], 'vagabond.3120')
            self.assertEqual(manga.last_history_item(state)['manga']['title'], 'Vagabond')

    def test_new_progress_persists_minimal_manga_snapshot_for_unsaved_reading(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            state = {'schema': 6, 'saved': {}, 'progress': {}, 'last_read': None}
            work = {
                'key': 'mangapill:4/vagabond', 'identity': 'mangapill:4/vagabond',
                'title': 'Vagabond', 'author': 'Inoue Takehiko',
                'variants': [{'source': 'mangapill', 'id': '4/vagabond', 'title': 'Vagabond', 'ref': {'id': '4/vagabond'}}],
            }
            chapter = {
                'source': 'mangapill', 'id': 'mp100', 'title_id': '4/vagabond',
                'number': '100', 'name': 'Vagabond Chapter 100',
                'source_ref': {'manga_id': '4/vagabond', 'chapter_ref': 'x'},
            }
            save_progress(path, state, work, chapter, 8, 21)
            loaded = load_state(path)
            rec = loaded['progress']['mangapill:4/vagabond']
            self.assertEqual(rec['manga']['title'], 'Vagabond')
            self.assertEqual(rec['manga']['author'], 'Inoue Takehiko')
            self.assertEqual(rec['manga']['variants'][0]['id'], '4/vagabond')
            self.assertEqual(manga.history_items(loaded)[0]['progress']['page'], 8)

    def test_history_is_ordered_by_recent_progress_and_last_read_wins_pointer(self):
        state = {
            'schema': 6, 'saved': {},
            'progress': {
                'legacy:a': {'title': 'A', 'updated': 20, 'page': 1, 'total_pages': 2, 'manga': {'key': 'legacy:a', 'identity': 'legacy:a', 'title': 'A', 'variants': []}},
                'legacy:b': {'title': 'B', 'updated': 30, 'page': 1, 'total_pages': 2, 'manga': {'key': 'legacy:b', 'identity': 'legacy:b', 'title': 'B', 'variants': []}},
            },
            'last_read': 'legacy:a',
        }
        self.assertEqual([x['key'] for x in manga.history_items(state)], ['legacy:b', 'legacy:a'])
        self.assertEqual(manga.last_history_item(state)['key'], 'legacy:a')


class UiRegressionTests(unittest.TestCase):
    def capture(self, func, *args):
        lines = []
        with mock.patch.object(ui, 'width', return_value=80), mock.patch.object(ui, 'margin', return_value=0), mock.patch.object(ui, 'color_enabled', return_value=False), mock.patch.object(ui, 'emit', side_effect=lines.append):
            func(*args)
        return lines

    def test_banner_every_border_line_has_exact_canvas_width(self):
        lines = self.capture(ui.banner, 'CLANNAD', 'comprobando fuentes')
        self.assertEqual(len(lines), 3)
        self.assertEqual([len(line) for line in lines], [80, 80, 80])
        self.assertTrue(lines[0].endswith('╮'))
        self.assertTrue(lines[-1].endswith('╯'))

    def test_blood_theme_uses_brighter_readable_accents(self):
        self.assertNotEqual(ui.CYAN, '\033[36m')
        self.assertNotEqual(ui.MAGENTA, '\033[35m')
        self.assertEqual(ui.CYAN, ui.SCARLET)
        self.assertNotEqual(ui.CYAN, ui.BLOOD)
        self.assertIn('38;5;203m', ui.ACCENT)
        self.assertIn('38;5;217m', ui.TEXT_WARM)

    def test_chapter_screen_does_not_advertise_redundant_continue_key(self):
        manga_item = {'title': 'Berserk', 'variants': [{'source': 'mangakatana', 'id': 'x'}]}
        state = {'schema': 6, 'saved': {}, 'progress': {}, 'last_read': None}
        chapters = [{'source': 'mangakatana', 'id': 'c1', 'number': '1', 'name': 'Chapter 1', 'language': 'en'}]
        output = []
        with mock.patch.object(ui, 'clear'), mock.patch.object(ui, 'brand'), mock.patch.object(ui, 'banner'), \
             mock.patch.object(ui, 'section'), mock.patch.object(ui, 'rule'), mock.patch.object(ui, 'width', return_value=80), \
             mock.patch.object(ui, 'list_rows', return_value=5), mock.patch.object(ui, 'color_enabled', return_value=False), \
             mock.patch.object(ui, 'key_hint', side_effect=lambda items: output.extend(items)), mock.patch('builtins.print'):
            manga.render_manga(manga_item, state, chapters, {}, {'mangakatana': 1}, 0)
        self.assertNotIn(('C', 'continue'), output)
        self.assertIn(('J', 'chapter'), output)

    def test_information_screen_exposes_aliases_genres_and_type(self):
        lines = []
        item = {
            'title': 'Kokou no Hito', 'author': 'Sakamoto Shinichi', 'aliases': ['The Climber'],
            'year': '2007', 'status': 'Finished', 'type': 'manga', 'genres': ['Drama', 'Seinen'],
            'variants': [{'source': 'mangakatana', 'id': 'x'}],
        }
        state = {'schema': 6, 'saved': {}, 'progress': {}, 'last_read': None}
        with mock.patch.object(ui, 'clear'), mock.patch.object(ui, 'brand'), mock.patch.object(ui, 'frame_top'), \
             mock.patch.object(ui, 'frame_rule'), mock.patch.object(ui, 'frame_bottom'), \
             mock.patch.object(ui, 'frame_line', side_effect=lambda left='', right='', *a, **k: lines.append((left, right))):
            manga.render_information(item, state)
        self.assertIn(('Alternative titles', 'The Climber'), lines)
        self.assertIn(('Type', 'MANGA'), lines)
        self.assertIn(('Genres', 'Drama · Seinen'), lines)


if __name__ == '__main__':
    unittest.main()
