"""0.7.5 acceptance tests: preferences, per-manga state and menu wiring."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import manga as app
from acmanga.reader import ReaderSession, normalize_settings
from acmanga.settings import default_settings, load_settings, save_settings
from acmanga.state import default_state, save_progress, load_state, progress_for
from acmanga.streaming import PageStream

A = {'key': 'mangakatana:work-a', 'identity': 'mangakatana:work-a',
     'title': 'Work A', 'variants': [{'source':'mangakatana','id':'work-a','title':'Work A'}]}
B = {'key': 'mangakatana:work-b', 'identity': 'mangakatana:work-b',
     'title': 'Work B', 'variants': [{'source':'mangakatana','id':'work-b','title':'Work B'}]}
CH = {'source':'mangakatana','id':'c1','number':'1'}
VIEW = {'position':0.63,'zoom':0.12,'horizontal':0,'fit':'width'}


class PreferenceTests(unittest.TestCase):
    def test_defaults_cover_every_requested_option(self):
        s = default_settings()
        self.assertEqual(s['reader_fit'],'page')
        self.assertEqual(s['prefetch_pages'],3)
        for key in ('auto_page_turn','remember_reader_mode','show_page_indicator','save_reader_position'):
            self.assertIs(s[key],True,key)

    def test_old_settings_keep_values_and_get_new_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'settings.json'
            p.write_text('{"schema":2,"source_mode":"mangapill","reader_fit":"width","cache_mib":1024,"prefetch_pages":8}')
            before=p.read_bytes()
            s=load_settings(p)
            self.assertEqual(p.read_bytes(),before)
            self.assertEqual(s['reader_fit'],'width')
            self.assertEqual(s['prefetch_pages'],8)  # No destructive migration of a valid older value.
            self.assertEqual(s['source_mode'],'mangapill')
            self.assertEqual(s['cache_mib'],1024)
            self.assertTrue(s['auto_page_turn'])

    def test_disabled_booleans_survive_roundtrip(self):
        s=default_settings()
        for key in ('auto_page_turn','remember_reader_mode','show_page_indicator','save_reader_position'):
            s[key]=False
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'settings.json';save_settings(p,s)
            self.assertEqual(load_settings(p),s)

    def test_invalid_boolean_values_use_safe_defaults(self):
        for invalid in ('false', 0, None, [], {}):
            with self.subTest(value=invalid):
                s=normalize_settings({'auto_page_turn':invalid,'show_page_indicator':invalid})
                self.assertIs(s['auto_page_turn'],True)
                self.assertIs(s['show_page_indicator'],True)

    def test_all_five_prefetch_choices_are_reachable(self):
        s=default_settings();s['prefetch_pages']=0
        got=[]
        for _ in range(5):
            got.append(s['prefetch_pages']);app.cycle_option(s,'prefetch_pages')
        self.assertEqual(got,[0,1,3,5,10])

    def test_all_boolean_options_toggle_both_ways(self):
        for key in ('auto_page_turn','remember_reader_mode','show_page_indicator','save_reader_position'):
            s=default_settings();app.cycle_option(s,key)
            self.assertFalse(s[key]);app.cycle_option(s,key,-1)
            self.assertTrue(s[key])

    def test_mode_and_scroll_option_choices(self):
        s=default_settings();app.cycle_option(s,'reader_fit');self.assertEqual(s['reader_fit'],'width')
        app.cycle_option(s,'reader_fit');self.assertEqual(s['reader_fit'],'page')
        app.cycle_option(s,'scroll_step');self.assertEqual(s['scroll_step'],.15)

    def test_prefetch_choices_queue_current_then_nearest_pages_on_both_sides(self):
        for radius in (0,1,3,5,10):
            with self.subTest(prefetch=radius), tempfile.TemporaryDirectory() as td:
                plan={'url_sets':[['https://example.invalid/{}.png'.format(i) for i in range(12)]], 'source_name':'Test'}
                with mock.patch('threading.Thread.start'):
                    s=PageStream(plan,td,prefetch=radius)
                s.request(3);s.focus(3)
                picked=[]
                with s.cv:
                    while True:
                        index=s._next_locked()
                        if index is None:break
                        picked.append(index);s.active.add(index)
                expected=[3]
                for distance in range(1,radius+1):
                    for index in (3+distance,3-distance):
                        if 1 <= index <= 12:
                            expected.append(index)
                self.assertEqual(picked,expected)
                s.threads.clear();s.close()


class PerMangaModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.p=self.root/'state.json';self.state=default_state()
    def tearDown(self):self.tmp.cleanup()
    def record(self,work,view=VIEW,mode='width'):
        save_progress(self.p,self.state,work,CH,2,12,view_state=view,reader_mode=mode)
    def session(self,preferences=None):
        s=ReaderSession(self.root,preferences);s.send=mock.Mock();return s
    def payload(self,s,**kwargs):
        s.show('/tmp/page.png',CH,2,12,**kwargs)
        return json.loads(s.send.call_args.args[1])

    def test_new_manga_uses_configured_default(self):
        s=self.session({'reader_fit':'width'});s.begin_manga(self.state,A)
        self.assertEqual(s.last_view['fit'],'width')

    def test_remembers_different_mode_for_each_manga(self):
        self.record(A)
        self.record(B,view=dict(VIEW,fit='page'),mode='page')
        state=load_state(self.p)
        s=self.session();s.begin_manga(state,A);self.assertEqual(s.last_view['fit'],'width')
        s.begin_manga(state,B);self.assertEqual(s.last_view['fit'],'page')
        s.begin_manga(state,A);self.assertEqual(s.last_view['fit'],'width')

    def test_remember_off_uses_global_even_with_saved_width(self):
        self.record(A)
        s=self.session({'reader_fit':'page','remember_reader_mode':False})
        s.begin_manga(self.state,A)
        self.assertEqual(s.last_view['fit'],'page')

    def test_mode_is_not_reloaded_at_each_chapter(self):
        self.record(A)
        s=self.session();s.begin_manga(self.state,A)
        s.last_view['fit']='page';s.begin_manga(self.state,A)
        self.assertEqual(s.last_view['fit'],'page')

    def test_legacy_progress_uses_saved_view_fit(self):
        self.state['progress'][A['key']]={'title':'Work A','view':dict(VIEW)}
        s=self.session();s.begin_manga(self.state,A)
        self.assertEqual(s.last_view['fit'],'width')

    def test_progress_without_mode_gets_full_page_default(self):
        self.state['progress'][A['key']]={'title':'Work A','page':2}
        s=self.session();s.begin_manga(self.state,A)
        self.assertEqual(s.last_view['fit'],'page')

    def test_mode_saved_even_when_no_vertical_view_is_stored(self):
        self.record(A,view=None,mode='width')
        state=load_state(self.p);rec=progress_for(state,A)
        self.assertNotIn('view',rec);self.assertEqual(rec['reader_mode'],'width')
        s=self.session();s.begin_manga(state,A)
        self.assertEqual(s.last_view['fit'],'width')
        self.assertEqual(s.last_view['position'],0)

    def test_saving_new_progress_preserves_previous_mode_when_not_updated(self):
        self.record(A)
        save_progress(self.p,self.state,A,CH,3,12,view_state={'fit':'page'},reader_mode=None)
        self.assertEqual(progress_for(load_state(self.p),A)['reader_mode'],'width')

    def test_previous_width_page_starts_at_bottom_even_if_unvisited(self):
        s=self.session({'reader_fit':'width'})
        p=self.payload(s,previous=True)
        self.assertEqual(p['view']['position'],1)
        self.assertEqual(p['view']['fit'],'width')

    def test_previous_width_bottom_overrides_visited_position(self):
        s=self.session({'reader_fit':'width'})
        p=self.payload(s,previous=True,saved=VIEW)
        self.assertEqual(p['view']['position'],1)

    def test_next_width_page_starts_at_top(self):
        s=self.session({'reader_fit':'width'});s.last_view=dict(VIEW)
        self.assertEqual(self.payload(s)['view']['position'],0)

    def test_previous_page_mode_stays_full_page(self):
        s=self.session();p=self.payload(s,previous=True)
        self.assertEqual(p['view']['position'],0)
        self.assertEqual(p['view']['fit'],'page')

    def test_restored_mode_mismatch_does_not_apply_old_zoom(self):
        s=self.session({'reader_fit':'page'});p=self.payload(s,saved=VIEW)
        self.assertEqual(p['view']['zoom'],0)
        self.assertEqual(p['view']['fit'],'page')

    def test_passes_options_to_real_lua_payload(self):
        s=self.session({'auto_page_turn':False,'show_page_indicator':False,'scroll_step':.20})
        p=self.payload(s)
        self.assertFalse(p['auto_page_turn']);self.assertFalse(p['show_page_indicator'])
        self.assertEqual(p['scroll_step'],.20)

    def test_no_state_write_when_only_selecting_initial_mode(self):
        self.record(A);before=self.p.read_bytes()
        s=self.session();s.begin_manga(self.state,A)
        self.assertEqual(self.p.read_bytes(),before)


class PersonalizationMenuTests(unittest.TestCase):
    def test_main_options_link_to_personalization(self):
        rows=app.option_rows(default_settings())
        self.assertIn('reader_customization',[r[0] for r in rows])

    def test_menu_exposes_all_requested_settings(self):
        keys={r[0] for r in app.reader_option_rows(default_settings())}
        self.assertTrue({'auto_page_turn','reader_fit','remember_reader_mode',
            'prefetch_pages','show_page_indicator','save_reader_position'} <= keys)
        self.assertEqual(keys,set(app.READER_OPTION_HELP))

    def test_submenu_edits_saves_updates_engine_and_returns(self):
        engine=mock.Mock();s=default_settings()
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'settings.json'
            with mock.patch.object(app,'SETTINGS_FILE',p), \
                 mock.patch.object(app.ui,'read_key',side_effect=['enter','down','enter','esc']), \
                 contextlib.redirect_stdout(io.StringIO()):
                app.reader_options_flow(engine,s)
            got=load_settings(p)
        self.assertFalse(got['auto_page_turn']);self.assertEqual(got['reader_fit'],'width')
        self.assertEqual(engine.update_preferences.call_count,2)

    def test_main_menu_reaches_submenu(self):
        with mock.patch.object(app.ui,'read_key',side_effect=['down','down','down','enter','esc']), \
             mock.patch.object(app,'reader_options_flow') as sub, \
             contextlib.redirect_stdout(io.StringIO()):
            app.options_flow(mock.Mock(),default_settings())
        sub.assert_called_once()

    def test_submenu_fits_80x24_terminal(self):
        for selected in range(8):
            with mock.patch.object(app.ui,'term_size',return_value=os.terminal_size((80,24))), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                app.render_reader_options(default_settings(),selected)
            lines=output.getvalue().splitlines()
            self.assertLessEqual(len(lines),24)
            self.assertLessEqual(max(map(len,lines)),80)
