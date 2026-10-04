import copy
import json
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import Future
from unittest import mock

from acmanga import reader
from acmanga.state import default_state, load_state


class Stream:
    total=3
    def __init__(self):self.focused=[];self.requested=[]
    def request(self,n,retry=False):self.requested.append(n)
    def focus(self,n):self.focused.append(n)
    def peek(self,n):return Path('/tmp/fixture-{}.png'.format(n)),None
    def discard(self,n):pass


class Session:
    """A scripted fake mpv boundary. Page downloader/controller run unmodified."""
    def __init__(self,actions):
        self.actions=list(actions);self.snap={'seq':0};self.stream=Stream()
        self.positions={};self.cache_dir=Path('/tmp');self.started=reader.time.monotonic()
        self.stats={'chapters':0,'first_page_seconds':None,'page_load_seconds':[]}
        self.cycles=0;self.shown=[];self.pending=None
        self.preferences=reader.normalize_settings({}); self.previous_flags=[]
    def begin_manga(self,state,manga):pass
    def ensure(self):pass
    def waiting(self,text):pass
    def prepare(self,e,c):f=Future();f.set_result(self.stream);return f
    def activate(self,k):pass
    def snapshot(self):
        self.cycles+=1
        if self.cycles>200:raise AssertionError('Reader test did not finish')
        if self.pending:
            self.snap=self.pending;self.pending=None
        elif self.actions and self.snap.get('loaded') and self.snap.get('action')!='next':
            # The actual controller will receive the visible page first.
            marker=self.actions[0]
            if marker=='idle':self.actions.pop(0)
            else:
                self.actions.pop(0);self.snap['seq']+=1;self.snap['action']=marker
                if marker=='quit':self.snap['quit']=True
        return copy.deepcopy(self.snap)
    def cancelled(self):return bool(self.snap.get('quit'))
    def send(self,name,*args):
        if name=='ready':self.snap['action']=''
    def capture(self,snap):self.positions[(snap['chapter_key'],snap['page'])]=snap['view']
    def prefetch_next(self,engine):pass
    def show(self,path,ch,page,total,saved=None,previous=False):
        self.previous_flags.append(previous)
        self.shown.append((page,saved))
        self.pending={'chapter_key':reader.chapter_key(ch),'page':page,'total':total,'loaded':True,
            'seq':self.snap['seq'],'action':'','view':saved or {'position':0,'horizontal':0,'zoom':0,'fit':'width'}}


class ReaderControllerTests(unittest.TestCase):
    def test_mpv_command_starts_maximized_and_gpu_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            playlist, conf, script = reader.write_mpv_files(tmp, [])
            with mock.patch.object(reader, '_mpv_supported_options', return_value={'window-maximized'}):
                cmd = reader.mpv_command('/usr/bin/mpv', playlist, conf, script, str(Path(tmp)/'sock'))
        self.assertIn('--vo=gpu', cmd)
        self.assertIn('--fs=no', cmd)
        self.assertIn('--border=yes', cmd)
        self.assertIn('--window-maximized=yes', cmd)
        self.assertNotIn('--geometry=85%x85%', cmd)
        self.assertNotIn('--border=no', cmd)

    def test_mpv_command_uses_full_area_fallback_without_maximize_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            playlist, conf, script = reader.write_mpv_files(tmp, [])
            with mock.patch.object(reader, '_mpv_supported_options', return_value=set()):
                cmd = reader.mpv_command('/usr/bin/mpv', playlist, conf, script, str(Path(tmp)/'sock'))
        self.assertIn('--geometry=100%x100%+0+0', cmd)

    def test_video_output_candidates_prefer_gpu_then_x11(self):
        with mock.patch.object(reader, '_mpv_video_outputs', return_value={'x11','gpu','xv'}):
            self.assertEqual(reader._mpv_output_candidates('/usr/bin/mpv'), ['gpu','x11'])
        with mock.patch.object(reader, '_mpv_video_outputs', return_value={'x11'}):
            self.assertEqual(reader._mpv_output_candidates('/usr/bin/mpv'), ['x11'])

    def test_reader_stability_rejects_vo_that_dies_after_ipc_startup(self):
        class DyingProcess:
            def __init__(self):
                self.calls = 0
            def poll(self):
                self.calls += 1
                return None if self.calls == 1 else 1
        self.assertFalse(reader.wait_for_reader_stable(DyingProcess(), timeout=0.01))

    def test_reader_stability_accepts_live_vo(self):
        class LiveProcess:
            def poll(self):
                return None
        self.assertTrue(reader.wait_for_reader_stable(LiveProcess(), timeout=0.01))


    def run_view(self,start,actions,state=None,preferences=None):
        with tempfile.TemporaryDirectory() as tmp:
            state=state if state is not None else default_state()
            manga={'key':'mangakatana:x','title':'X','variants':[{'source':'mangakatana','id':'x','title':'X','ref':{'id':'x'}}]}
            ch={'source':'mangakatana','id':'x:c1','title_id':'x','number':'1'}
            session=Session(actions)
            session.preferences=reader.normalize_settings(preferences)
            with mock.patch('acmanga.reader.time.sleep'):
                result=reader.view(object(),Path(tmp)/'state.json',state,manga,ch,tmp,start_page=start,session=session)
            return result,session,load_state(Path(tmp)/'state.json')

    def test_first_page_then_next_then_quit_persists_displayed_page(self):
        result,s,state=self.run_view(1,['idle','next','idle','quit'])
        self.assertEqual([p for p,_ in s.shown],[1,2]);self.assertEqual(result['page'],2)
        rec=next(iter(state['progress'].values()));self.assertEqual(rec['page'],2)

    def test_only_last_page_next_completes_chapter(self):
        result,s,state=self.run_view(3,['idle','next'])
        self.assertTrue(result['completed']);self.assertEqual(result['page'],3)

    def test_quitting_last_page_does_not_mark_completed(self):
        result,s,state=self.run_view(3,['idle','quit'])
        self.assertFalse(result['completed'])

    def test_previous_first_page_requests_previous_chapter(self):
        result,s,state=self.run_view(1,['idle','previous'])
        self.assertTrue(result['previous']);self.assertEqual(result['page'],1)

    def test_previous_chapter_starts_on_last_page(self):
        result,s,state=self.run_view(-1,['idle','quit'])
        self.assertEqual(s.shown[0][0],3)

    def test_resume_vertical_position_only_for_matching_chapter(self):
        state=default_state();state['progress']['mangakatana:x']={'title':'X','source':'mangakatana','chapter_id':'x:c1','page':2,'view':{'position':.55,'fit':'width','zoom':.2}}
        result,s,state=self.run_view(2,['idle','quit'],state)
        self.assertEqual(s.shown[0][1]['position'],.55)

    def test_position_off_ignores_old_position_and_omits_view_but_keeps_progress(self):
        state=default_state()
        state['progress']['mangakatana:x']={'title':'X','source':'mangakatana','chapter_id':'x:c1','page':2,
            'view':{'position':.55,'fit':'width','zoom':.2}}
        result,s,state=self.run_view(2,['idle','quit'],state,{'save_reader_position':False})
        self.assertIsNone(s.shown[0][1])
        rec=next(iter(state['progress'].values()))
        self.assertEqual(rec['page'],2)
        self.assertNotIn('view',rec)
        self.assertEqual(rec['reader_mode'],'width')

    def test_controller_marks_previous_chapter_entry_for_bottom(self):
        result,s,state=self.run_view(-1,['idle','quit'])
        self.assertEqual(s.shown[0][0],3)
        self.assertTrue(s.previous_flags[0])

    def test_controller_marks_previous_page_entry_for_bottom(self):
        result,s,state=self.run_view(2,['idle','previous','idle','quit'])
        self.assertEqual([p for p,_ in s.shown],[2,1])
        self.assertEqual(s.previous_flags,[False,True])

    def test_controller_does_not_change_saved_mode_when_remember_disabled(self):
        state=default_state()
        state['progress']['mangakatana:x']={'title':'X','reader_mode':'page'}
        result,s,state=self.run_view(2,['idle','quit'],state,{'remember_reader_mode':False})
        rec=next(iter(state['progress'].values()))
        self.assertEqual(rec['reader_mode'],'page')
