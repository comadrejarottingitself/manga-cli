"""Full Python controller + actual Lua code over real JSON snapshots.

Only mpv's display/input API and the image source are simulated. This is NOT a
visual mpv test. It catches wiring bugs that separate Lua/Python unit tests miss.
"""
import contextlib
import io
import json
import os
from concurrent.futures import Future
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import manga as app
from acmanga import reader
from acmanga.state import default_state, save_progress, load_state, progress_for
from tests.test_reader_lua import LuaVM, HARNESS, ROOT


WORK={'key':'mangakatana:fixture','title':'Fixture',
      'variants':[{'source':'mangakatana','id':'fixture','title':'Fixture'}]}
CHAPTERS=[{'source':'mangakatana','id':'c1','number':'1'},
          {'source':'mangakatana','id':'c2','number':'2'}]


def lua_value(value):
    if value is None:return 'nil'
    if value is True:return 'true'
    if value is False:return 'false'
    if isinstance(value,(int,float)):return str(value)
    if isinstance(value,str):return json.dumps(value,ensure_ascii=False)
    if isinstance(value,dict):
        return '{'+','.join('['+lua_value(k)+']='+lua_value(v) for k,v in value.items())+'}'
    return '{'+','.join(lua_value(v) for v in value)+'}'


class Stream:
    def __init__(self,total):self.total=total;self.requests=[];self.focused=[]
    def request(self,index,retry=False):self.requests.append(index)
    def focus(self,index):self.focused.append(index)
    def peek(self,index):return Path('/tmp/fixture-{}.png'.format(index)),None
    def close(self):pass


class BridgeSession(reader.ReaderSession):
    def __init__(self,folder,preferences,actions):
        super().__init__(folder,preferences)
        self.actions=list(actions);self.shown=[];self.polls=0;self.after_show=False
        self.streams={'c1':Stream(2),'c2':Stream(3)}
        self.temp=tempfile.TemporaryDirectory(prefix='manga-cli-bridge-')
        self.control=Path(self.temp.name)/'control.json'
        self.env=mock.patch.dict(os.environ,{'ACMANGA_READER_STATE':str(self.control)})
        self.env.start();self.vm=LuaVM()
        self.vm.run(HARNESS+'\nFORMAT_JSON_NIL=true\n')
        self.vm.run((ROOT/'acmanga/reader.lua').read_text(encoding='utf-8'))
    def ensure(self):pass
    def prepare(self,engine,chapter,lookahead=False):
        f=Future();f.set_result(self.streams[chapter['id']]);return f
    def activate(self,key):self.active_key=key
    def prefetch_next(self,engine):pass
    def send(self,name,*args):
        if name=='show-page':
            data=json.loads(args[0]);self.shown.append(data);self.after_show=True
            self.vm.run('INPUT='+lua_value(data)+";MESSAGES['show-page']('fixture');advance(.3)")
        else:
            self.vm.run('MESSAGES['+lua_value(name)+']('+','.join(lua_value(v) for v in args)+')')
    def snapshot(self):
        self.polls+=1
        if self.polls>200:raise AssertionError('Bridge scenario did not terminate')
        snap=reader.ReaderSession.snapshot(self)
        if self.after_show:
            self.after_show=False
            return snap
        if snap.get('loaded') and self.actions:
            # pending next/previous is handled by Python before injecting more.
            if not snap.get('busy'):
                action=self.actions.pop(0)
                if callable(action):action(self,snap)
                else:self.vm.run('key('+lua_value(action)+')')
                return reader.ReaderSession.snapshot(self)
        return snap
    def cancelled(self):
        return bool(reader.ReaderSession.snapshot(self).get('quit'))
    def close(self):
        if self.closed:return
        super().close();self.vm.close();self.env.stop()


class Engine:
    def __init__(self,prefs=None):self.preferences=prefs or {}
    def chapter_alternates(self,c):return []
    def next_chapter(self,chapters,current):
        i=chapters.index(current);return chapters[i+1] if i+1<len(chapters) else None
    def previous_chapter(self,chapters,current):
        i=chapters.index(current);return chapters[i-1] if i else None


class ReaderBridgeTests(unittest.TestCase):
    def test_lua_bottom_to_next_chapter_and_top_to_previous_bottom(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'state.json';state=default_state()
            save_progress(p,state,WORK,CHAPTERS[0],2,2,
                          view_state={'fit':'width','position':.999},reader_mode='width')
            session=BridgeSession(td,{'reader_fit':'width'},['WHEEL_DOWN','WHEEL_UP','ESC'])
            with mock.patch.object(app,'ReaderSession',return_value=session), \
                 mock.patch.object(app,'STATE_FILE',p),mock.patch.object(app,'CACHE_DIR',Path(td)), \
                 mock.patch('acmanga.reader.time.sleep'),contextlib.redirect_stdout(io.StringIO()):
                app.read_sequence(Engine(),state,WORK,CHAPTERS,CHAPTERS[0],2)
            self.assertEqual([(x['chapter_key'],x['page']) for x in session.shown],
                [(reader.chapter_key(CHAPTERS[0]),2),(reader.chapter_key(CHAPTERS[1]),1),
                 (reader.chapter_key(CHAPTERS[0]),2)])
            self.assertEqual(session.shown[1]['view']['position'],0)
            self.assertEqual(session.shown[2]['view']['position'],1)
            rec=progress_for(load_state(p),WORK)
            self.assertEqual(rec['chapter_id'],'c1');self.assertEqual(rec['page'],2)
            self.assertEqual(rec['view']['position'],1)

    def test_page_mode_previous_and_next_work_with_auto_off(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'state.json';state=default_state()
            session=BridgeSession(td,{'auto_page_turn':False},['WHEEL_UP','WHEEL_DOWN','ESC'])
            with session,mock.patch('acmanga.reader.time.sleep'):
                result=reader.view(Engine(),p,state,WORK,CHAPTERS[1],td,start_page=2,session=session)
            self.assertEqual([x['page'] for x in session.shown],[2,1,2])
            self.assertTrue(all(x['view']['fit']=='page' for x in session.shown))
            self.assertEqual(result['page'],2)

    def test_width_previous_unvisited_starts_at_bottom(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'state.json';state=default_state()
            session=BridgeSession(td,{'reader_fit':'width'},['WHEEL_UP','ESC'])
            with session,mock.patch('acmanga.reader.time.sleep'):
                reader.view(Engine(),p,state,WORK,CHAPTERS[1],td,start_page=2,session=session)
            self.assertEqual([x['page'] for x in session.shown],[2,1])
            self.assertEqual(session.shown[-1]['view']['position'],1)

    def test_auto_off_clamps_scroll_but_click_still_advances(self):
        def at_bottom(s,snap):
            self.assertEqual(snap['view']['position'],1)
            self.assertEqual(len(s.shown),1)
            s.vm.run("key('MBTN_LEFT')")
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'state.json';state=default_state()
            save_progress(p,state,WORK,CHAPTERS[1],1,3,
                          view_state={'fit':'width','position':.999},reader_mode='width')
            session=BridgeSession(td,{'auto_page_turn':False},['WHEEL_DOWN',at_bottom,'ESC'])
            with session,mock.patch('acmanga.reader.time.sleep'):
                reader.view(Engine(),p,state,WORK,CHAPTERS[1],td,session=session)
            self.assertEqual([x['page'] for x in session.shown],[1,2])
            self.assertEqual(session.shown[-1]['view']['position'],0)

    def test_fit_changes_saved_per_manga_and_restored_in_new_session(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'state.json';state=default_state()
            session=BridgeSession(td,{},['f','DOWN','ESC'])
            with session,mock.patch('acmanga.reader.time.sleep'):
                reader.view(Engine(),p,state,WORK,CHAPTERS[1],td,session=session)
            rec=progress_for(load_state(p),WORK)
            self.assertEqual(rec['reader_mode'],'width')
            self.assertGreater(rec['view']['position'],0)
            reopened=BridgeSession(td,{},['ESC'])
            with reopened,mock.patch('acmanga.reader.time.sleep'):
                reader.view(Engine(),p,load_state(p),WORK,CHAPTERS[1],td,session=reopened)
            self.assertEqual(reopened.shown[0]['view']['fit'],'width')
            self.assertEqual(reopened.shown[0]['view']['position'],rec['view']['position'])

    def test_disabled_position_keeps_mode_and_page_only(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'state.json';state=default_state()
            session=BridgeSession(td,{'save_reader_position':False},['f','DOWN','ESC'])
            with session,mock.patch('acmanga.reader.time.sleep'):
                reader.view(Engine(),p,state,WORK,CHAPTERS[1],td,session=session)
            rec=progress_for(load_state(p),WORK)
            self.assertNotIn('view',rec);self.assertEqual(rec['page'],1)
            self.assertEqual(rec['reader_mode'],'width')
