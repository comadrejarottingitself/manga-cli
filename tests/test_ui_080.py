"""0.8.x acceptance: appearance, English text, cell-safe frames and packaging."""
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import manga as app
from acmanga import ui
from acmanga.i18n import TEXT, tr
from acmanga.settings import default_settings, load_settings, save_settings, _normalized
from acmanga.state import default_state
from acmanga.terminal_text import cell_width, clean_text, compact, pad
from acmanga.theme import ACCENT_COLORS, PALETTES

ROOT = Path(__file__).resolve().parents[1]


def capture(function, *args, columns=80, rows=28, color=False, **kwargs):
    with mock.patch.object(ui, 'term_size', return_value=os.terminal_size((columns, rows))), \
         mock.patch.object(ui, 'color_enabled', return_value=color), \
         mock.patch.object(ui, 'clear'), contextlib.redirect_stdout(io.StringIO()) as output:
        function(*args, **kwargs)
    return output.getvalue()


class PaletteTests(unittest.TestCase):
    def tearDown(self):
        ui.apply_theme('crimson')

    def test_exact_ten_accents(self):
        self.assertEqual(ACCENT_COLORS, ('crimson', 'red', 'orange', 'amber', 'green',
                                         'lime', 'cyan', 'blue', 'purple', 'magenta'))

    def test_default_is_crimson(self):
        self.assertEqual(default_settings()['accent_color'], 'crimson')

    def test_accents_have_distinct_selection_colors(self):
        self.assertEqual(len({palette.accent for palette in PALETTES.values()}), 10)

    def test_palette_indexes_are_valid(self):
        for palette in PALETTES.values():
            self.assertTrue(all(isinstance(value, int) and 0 <= value <= 255 for value in palette))

    def test_invalid_values_use_crimson(self):
        for value in (None, '', 'BLUE', 'unknown', 7, [], {}, False):
            with self.subTest(value=value):
                self.assertEqual(_normalized({'accent_color': value})['accent_color'], 'crimson')
                self.assertEqual(ui.apply_theme(value), 'crimson')

    def test_old_settings_keep_reader_values_and_ignore_language(self):
        old = dict(reader_fit='width', prefetch_pages=5, auto_page_turn=False,
                   scroll_step=0.2, source_mode='mangapill', preferred_language='es', language='en')
        new = _normalized(old)
        for key in ('reader_fit', 'prefetch_pages', 'auto_page_turn', 'scroll_step', 'source_mode'):
            self.assertEqual(new[key], old[key])
        self.assertEqual(new['accent_color'], 'crimson')
        self.assertNotIn('preferred_language', new)
        self.assertNotIn('language', new)

    def test_all_colors_roundtrip_through_settings(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'settings.json'
            for color in ACCENT_COLORS:
                settings = dict(default_settings(), accent_color=color)
                save_settings(path, settings)
                self.assertEqual(load_settings(path), settings)

    def test_semantic_colors_never_change(self):
        before = (ui.GREEN, ui.YELLOW, ui.RED)
        for color in ACCENT_COLORS:
            ui.apply_theme(color)
            self.assertEqual((ui.GREEN, ui.YELLOW, ui.RED), before)
            for kind, code in (('ok', ui.GREEN), ('warn', ui.YELLOW), ('error', ui.RED)):
                output = capture(ui.status, 'Sample', kind, color=True)
                self.assertIn(code, output)
            output = capture(app.render_sources, {'title': 'Example'}, report={
                'mangakatana': {'status': 'error', 'error': 'Synthetic failure'}}, color=True)
            self.assertIn(ui.RED + ui.BOLD + '! ERROR', output)

    def test_brand_frames_title_version_and_selection_use_new_roles(self):
        outputs = []
        for color in ACCENT_COLORS:
            ui.apply_theme(color)
            output = capture(app.render_main, default_state(), default_settings(), color=True)
            self.assertIn(ui.MAROON, output)
            self.assertIn(ui.ACCENT, output)
            self.assertIn(ui.CRIMSON, output)
            self.assertIn(ui.BOLD + ui.ACCENT + '\u25b6 SEARCH', output)
            outputs.append(output)
        self.assertEqual(len(set(outputs)), 10)

    def test_badge_default_is_not_bound_to_old_palette(self):
        ui.apply_theme('blue')
        with mock.patch.object(ui, 'color_enabled', return_value=True):
            self.assertIn(ui.BLOOD, ui.badge('MK'))

    def test_no_color_and_dumb_term(self):
        for env in ({'NO_COLOR': '1', 'TERM': 'xterm-256color'}, {'TERM': 'dumb'}):
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys.stdout, 'isatty', return_value=True):
                self.assertEqual(ui.paint('text', ui.ACCENT), 'text')


class CellTextTests(unittest.TestCase):
    def test_ascii_and_padding_cells(self):
        self.assertEqual(cell_width(' A '), 3)
        self.assertEqual(cell_width('     '), 5)

    def test_cjk_fullwidth_and_combining_cells(self):
        self.assertEqual(cell_width('\u5b9d\u77f3\u306e\u56fd'), 8)
        self.assertEqual(cell_width('e\u0301'), 1)
        self.assertEqual(cell_width('\uff21\uff22'), 4)

    def test_emoji_variation_and_keycap_budget(self):
        self.assertEqual(cell_width('\u2764\ufe0f'), 2)
        self.assertEqual(cell_width('#\ufe0f\u20e3'), 2)
        self.assertEqual(compact('\u2764\ufe0f' * 8, 7), '\u2764\ufe0f' * 2 + '...')

    def test_ascii_ellipsis(self):
        self.assertEqual(compact('abcdefghij', 7), 'abcd...')

    def test_fitting_value_is_not_truncated(self):
        self.assertEqual(compact('\u5b9d\u77f3\u306e\u56fd', 8), '\u5b9d\u77f3\u306e\u56fd')

    def test_cjk_truncation_never_splits_a_wide_character(self):
        self.assertEqual(compact('\u5b9d\u77f3\u306e\u56fd', 7), '\u5b9d\u77f3...')
        self.assertLessEqual(cell_width(compact('\u5b9d\u77f3\u306e\u56fd', 6)), 6)

    def test_combining_mark_stays_with_base(self):
        self.assertEqual(compact('e\u0301' * 10, 5), 'e\u0301e\u0301...')
        self.assertEqual(compact('\u0301hello', 20), 'hello')

    def test_zero_and_tiny_widths(self):
        for width in range(-2, 4):
            self.assertEqual(compact('long text', width), '.' * max(0, width))

    def test_none_empty_and_zero(self):
        self.assertEqual(compact(None, 10), '')
        self.assertEqual(compact('', 10), '')
        self.assertEqual(compact(0, 10), '0')

    def test_multiline_fields_are_single_line(self):
        self.assertEqual(compact('a\n\tb\r\nc', 20), 'a b c')

    def test_ansi_and_bidi_controls_are_stripped(self):
        dirty = '\x1b[31mred\x1b[0m\x1b]0;bad title\x07\u202eX'
        self.assertEqual(clean_text(dirty), 'redX')
        self.assertEqual(cell_width('\x1b[31mABC\x1b[0m'), 3)

    def test_padding_uses_cells_not_codepoints(self):
        self.assertEqual(pad('\u5b9d\u77f3', 6), '\u5b9d\u77f3  ')
        self.assertEqual(cell_width(pad('e\u0301', 8)), 8)

    def test_long_right_field_preserves_label(self):
        output = capture(ui.frame_line, 'Alternative titles', '\u5b9d\u77f3\u306e\u56fd' * 80)
        self.assertIn('Alternative titles', output)
        self.assertIn('...', output)
        self.assertLessEqual(cell_width(output.rstrip('\n')), 80)

    def test_both_oversized_columns_stay_inside_frame(self):
        for width in (24, 32, 40, 60, 80, 100, 160):
            output = capture(ui.frame_line, 'Very long title ' * 30, '\u5b9d\u77f3' * 300, columns=width)
            self.assertLessEqual(cell_width(output.rstrip('\n')), width)

    def test_banner_width_includes_label_spaces(self):
        for title in ('Heading', '\u5b9d\u77f3\u306e\u56fd' * 30):
            text = capture(ui.banner, title, 'Subtitle')
            self.assertEqual({cell_width(line) for line in text.splitlines()}, {77})

    def test_randomized_cell_budgets(self):
        rng = random.Random(800)
        pieces = ['a', '\u5b9d', '\uff21', 'e\u0301', '\u2764\ufe0f', ' ', '\t', '\n']
        for _ in range(400):
            value = ''.join(rng.choice(pieces) for _ in range(rng.randrange(60)))
            limit = rng.randrange(45)
            result = compact(value, limit)
            self.assertLessEqual(cell_width(result), limit)
            self.assertNotIn('\n', result)


class AppearanceFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'settings.json'
        self.settings = default_settings()
        self.engine = mock.Mock()
        self.addCleanup(ui.apply_theme, 'crimson')

    def run_flow(self, keys, render=None):
        with mock.patch.object(app, 'SETTINGS_FILE', self.path), \
             mock.patch.object(ui, 'read_key', side_effect=keys), \
             mock.patch.object(app, 'render_appearance', side_effect=render), \
             contextlib.redirect_stdout(io.StringIO()):
            app.appearance_flow(self.engine, self.settings)

    def test_preview_is_live_before_save(self):
        seen = []
        self.run_flow(['down', 'down', 'esc'], lambda *a, **k: seen.append(ui.THEME_NAME))
        self.assertEqual(seen, ['crimson', 'red', 'orange'])

    def test_preview_does_not_write_settings(self):
        self.run_flow(['down', 'esc'])
        self.assertFalse(self.path.exists())
        self.assertEqual(self.settings['accent_color'], 'crimson')
        self.engine.update_preferences.assert_not_called()

    def test_escape_restores_preexisting_color_and_file_bytes(self):
        self.settings['accent_color'] = 'blue'
        save_settings(self.path, self.settings)
        before = self.path.read_bytes()
        self.run_flow(['down', 'down', 'esc'])
        self.assertEqual(ui.THEME_NAME, 'blue')
        self.assertEqual(self.path.read_bytes(), before)

    def test_enter_saves_preview_and_updates_engine(self):
        self.run_flow(['down', 'down', 'enter'])
        self.assertEqual(load_settings(self.path)['accent_color'], 'orange')
        self.assertEqual(self.settings['accent_color'], 'orange')
        self.assertEqual(ui.THEME_NAME, 'orange')
        self.engine.update_preferences.assert_called_once_with(self.settings)

    def test_last_palette_is_reachable_and_reopens_selected(self):
        self.run_flow(['end', 'enter'])
        selected = []
        self.run_flow(['esc'], lambda index, **k: selected.append(index))
        self.assertEqual(selected, [9])
        self.assertEqual(self.settings['accent_color'], 'magenta')

    def test_save_failure_keeps_existing_file_settings_and_restores_on_escape(self):
        save_settings(self.path, self.settings)
        before = self.path.read_bytes()
        seen = []
        with mock.patch.object(app, 'save_settings', side_effect=PermissionError('test denied')):
            self.run_flow(['down', 'enter', 'esc'], lambda *a, **kw: seen.append(kw.get('flash')))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.settings['accent_color'], 'crimson')
        self.assertEqual(ui.THEME_NAME, 'crimson')
        self.assertTrue(any(text and 'Could not save appearance' in text for text in seen))
        self.engine.update_preferences.assert_not_called()

    def test_interruption_restores_original_theme(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_flow(['down', KeyboardInterrupt()])
        self.assertEqual(ui.THEME_NAME, 'crimson')

    def test_preview_does_not_write_reading_state(self):
        state_path = self.path.with_name('state.json')
        state_path.write_bytes(b'PRIVATE SYNTHETIC SENTINEL')
        self.run_flow(['end', 'enter'])
        self.assertEqual(state_path.read_bytes(), b'PRIVATE SYNTHETIC SENTINEL')

    def test_settings_menu_opens_appearance(self):
        with mock.patch.object(ui, 'read_key', side_effect=['down'] * 4 + ['enter', 'esc']), \
             mock.patch.object(app, 'appearance_flow') as open_menu, \
             contextlib.redirect_stdout(io.StringIO()):
            app.options_flow(self.engine, self.settings)
        open_menu.assert_called_once_with(self.engine, self.settings)


class ScreenTests(unittest.TestCase):
    def test_main_menu_is_english(self):
        text = capture(app.render_main, default_state(), default_settings())
        for word in ('MANGA-CLI', 'SEARCH', 'SAVED', 'HISTORY', 'SETTINGS', 'v0.8.3'):
            self.assertIn(word, text)
        for word in ('GUARDADOS', 'OPCIONES', 'BUSCAR'):
            self.assertNotIn(word, text)

    def test_information_is_single_line_cell_safe_at_many_widths(self):
        manga = {'title': '\u5b9d\u77f3\u306e\u56fd' * 30, 'author': 'Author ' * 40,
                 'aliases': ['\u5b9d\u77f3\u306e\u56fd' * 80, 'e\u0301' * 100],
                 'genres': ['Genre' * 60], 'year': 2000, 'status': 'Complete', 'type': 'manga',
                 'variants': [{'source': 'custom source ' * 30}]}
        for columns in (24, 32, 40, 60, 80, 100, 120, 160):
            text = capture(app.render_information, manga, default_state(), columns=columns)
            lines = [line for line in text.splitlines() if line]
            self.assertTrue(all(cell_width(line) <= columns for line in lines), (columns, lines))
            self.assertIn('...', text)
            self.assertEqual(len(lines), 17)

    def test_language_rows_are_absent_from_details_sources_and_settings(self):
        manga = {'title': 'Example', 'variants': [{'source': 'mangakatana'}]}
        state = default_state()
        outputs = [capture(app.render_information, manga, state),
                   capture(app.render_manga_card, manga, state, 0),
                   capture(app.render_sources, manga), capture(app.render_options, default_settings(), 0)]
        for text in outputs:
            for forbidden in ('Preferred language', 'Idioma', 'ENGLISH ONLY', 'ENGLISH', '[EN]'):
                self.assertNotIn(forbidden, text)

    def test_chapter_language_badge_is_absent(self):
        text = capture(app.render_manga, {'title': 'Example'}, default_state(),
                       [{'source': 'mangakatana', 'id': 'c1', 'number': '1', 'language': 'en'}], {}, {}, 0)
        self.assertNotIn('[EN]', text)

    def test_appearance_fits_80x24_for_every_selection(self):
        for selected in range(10):
            text = capture(app.render_appearance, selected, columns=80, rows=24)
            lines = text.splitlines()
            self.assertLessEqual(len(lines), 24)
            self.assertTrue(all(cell_width(line) <= 80 for line in lines))
            self.assertIn(tr('color.' + ACCENT_COLORS[selected]), text)

    def test_options_and_about_fit_80x24(self):
        for func, args in ((app.render_options, (default_settings(), 4)), (app.render_about, ())):
            text = capture(func, *args, columns=80, rows=24)
            self.assertLessEqual(len(text.splitlines()), 24)
            self.assertTrue(all(cell_width(line) <= 80 for line in text.splitlines()))

    def test_about_has_final_brand_and_readme_guide_without_license_status(self):
        text = capture(app.render_about)
        self.assertIn('Tutorial / guide', text)
        self.assertIn('README.md', text)
        self.assertIn('Inspired by ani-cli.', text)
        self.assertIn('a comadreja project', text)
        self.assertNotIn('Not selected', text)
        self.assertNotIn('License', text)

    def test_main_header_is_clean_and_continue_hint_is_hidden(self):
        state = default_state()
        state['progress']['example'] = {'title': 'Example', 'chapter': '1', 'page': 1, 'updated': 1}
        text = capture(app.render_main, state, default_settings(), 0)
        self.assertIn('ONLINE READING', text)
        self.assertNotIn('MK + MP', text)
        self.assertNotIn('C  continue', text.lower())
        self.assertNotIn('C Continue', text)

    def test_hidden_continue_shortcut_remains_backward_compatible(self):
        state = default_state()
        state['progress']['example'] = {'title': 'Example', 'chapter': '1', 'page': 1, 'updated': 1}
        with mock.patch.object(ui, 'read_key', return_value='c'), mock.patch.object(app, 'render_main'):
            self.assertEqual(app.main_menu(state, default_settings()), 'continue')


class LocalizationAndPackageTests(unittest.TestCase):
    def test_catalogue_values_are_strings_and_keys_are_unique(self):
        raw = (ROOT / 'acmanga/locales/en.json').read_text(encoding='utf-8')
        pairs = json.loads(raw, object_pairs_hook=list)
        self.assertEqual(len(pairs), len({key for key, _ in pairs}))
        self.assertTrue(all(isinstance(value, str) for _, value in pairs))

    def test_all_literal_message_references_exist(self):
        for path in [ROOT / 'manga.py', *(ROOT / 'acmanga').rglob('*.py')]:
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == 'tr' and node.args and isinstance(node.args[0], ast.Constant)):
                    self.assertIn(node.args[0].value, TEXT, str(path))

    def test_generated_lua_and_bootstrap_catalogues_are_current(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/sync_text.py'), '--check'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_key_is_not_silently_displayed(self):
        with self.assertRaises(KeyError):
            tr('test.nonexistent')

    def test_cli_help_is_english(self):
        result = subprocess.run([sys.executable, str(ROOT / 'manga.py'), '--help'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertIn('lightweight terminal manga reader', result.stdout)
        self.assertIn('offline self-test', result.stdout)
        self.assertNotIn('diagnostico', result.stdout)

    def test_repo_documents_are_present(self):
        for rel in ('README.md', 'CHANGELOG.md', 'LICENSE', 'CONTRIBUTING.md', '.gitignore',
                    'ROADMAP.md', 'docs/LOCALIZATION.md', 'docs/RELEASE_CHECKLIST.md'):
            self.assertTrue((ROOT / rel).is_file(), rel)
        self.assertIn('mit license', (ROOT / 'LICENSE').read_text().lower())

    def test_no_private_data_or_runtime_files_in_release_source(self):
        forbidden = {'state.json', 'settings.json', 'history.json', 'progress.json',
                     'control.json', 'timings.json', 'manifest.json', '.env'}
        from tools._release import release_paths
        paths = [ROOT / rel for rel in release_paths(ROOT)]
        self.assertFalse([str(path) for path in paths if path.name in forbidden or
                          path.suffix in ('.log', '.part', '.zip', '.key', '.pem')])
