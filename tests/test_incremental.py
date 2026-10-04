"""Regression tests for responsive search and persistent-reader chapter flow."""
from concurrent.futures import Future
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from acmanga.engine import MultiSourceEngine
from tests.test_engine import FakeSource, item
from acmanga.streaming import background_call
from acmanga.reader import ReaderSession

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('manga_incremental_tests', ROOT/'manga.py')
app=importlib.util.module_from_spec(spec); spec.loader.exec_module(app)

class IncrementalTests(unittest.TestCase):
    def test_fast_source_published_before_slow_source_finishes(self):
        release=threading.Event(); appeared=threading.Event(); updates=[]
        class Slow(FakeSource):
            def search(self, query):
                if not release.wait(3):raise AssertionError('test synchronization timeout')
                return super().search(query)
        engine=MultiSourceEngine([
            Slow('mangakatana',[item('mangakatana','a','Example')]),
            FakeSource('mangapill',[item('mangapill','b','Example')])])
        def got(results,errors,stats):
            updates.append((results,stats)); appeared.set()
        task=background_call(lambda: engine.search('Example',progress_callback=got))
        try:
            self.assertTrue(appeared.wait(2))
            self.assertFalse(task.done())
            self.assertEqual(updates[0][1],{'mangapill':1})
        finally:release.set()
        results,errors=task.result(3)
        self.assertEqual(len(results),1)
        self.assertEqual(len(results[0]['variants']),2)
        self.assertFalse(errors)

    def test_failure_of_one_source_keeps_other_results(self):
        engine=MultiSourceEngine([
            FakeSource('mangakatana',search_error=RuntimeError('test: offline')),
            FakeSource('mangapill',[item('mangapill','b','Example')])])
        updates=[]
        results,errors=engine.search('Example',progress_callback=lambda r,e,s: updates.append((r,e)))
        self.assertTrue(results);self.assertIn('mangakatana',errors)
        self.assertTrue(updates[-1][0])

    def test_controller_keeps_same_reader_session_between_chapters(self):
        chapters=[{'source':'mangakatana','id':'c1','number':'1'},
                  {'source':'mangakatana','id':'c2','number':'2'}]
        chapters=list(reversed(chapters))  # Engine contract: newest chapter first.
        engine=MultiSourceEngine([FakeSource('mangakatana')])
        fake=mock.MagicMock()
        fake.__enter__.return_value=fake
        outcome=[{'page':1,'total':1,'completed':True}, {'page':1,'total':1,'completed':False}]
        with mock.patch.object(app,'ReaderSession',return_value=fake) as cls, \
             mock.patch.object(app,'view',side_effect=outcome) as v, \
             contextlib.redirect_stdout(io.StringIO()):
            app.read_sequence(engine,{'saved':{},'progress':{}},{'title':'Example'},chapters,chapters[-1])
        self.assertEqual(cls.call_count,1)
        self.assertEqual(v.call_count,2)
        self.assertIs(v.call_args_list[0].kwargs['session'],v.call_args_list[1].kwargs['session'])
        self.assertEqual(v.call_args_list[1].args[4]['id'],'c2')

    def test_previous_chapter_does_not_skip_alternative_id(self):
        engine=MultiSourceEngine([FakeSource('mangakatana'),FakeSource('mangapill')])
        chapters=[{'source':'mangakatana','id':'c1','number':'1'},
                  {'source':'mangakatana','id':'c2','number':'2',
                   'alternates':[{'source':'mangapill','id':'p2','number':'2'}]}]
        previous=engine.previous_chapter(list(reversed(chapters)),chapters[1]['alternates'][0])
        self.assertEqual(previous['id'],'c1')

    def test_show_next_resets_scroll_and_previous_restores_it(self):
        with tempfile.TemporaryDirectory() as td:
            s=ReaderSession(td)
            s.last_view={'position':.83,'horizontal':.3,'zoom':.2,'fit':'width'}
            s.send=mock.Mock()
            ch={'source':'mangakatana','id':'c1'}
            s.show('/tmp/one.png',ch,2,5)
            import json
            self.assertEqual(json.loads(s.send.call_args.args[1])['view']['position'],0)
            s.show('/tmp/one.png',ch,1,5,saved={'position':.83,'horizontal':.3,'zoom':.2,'fit':'width'})
            self.assertEqual(json.loads(s.send.call_args.args[1])['view']['position'],.83)
            s.close()
