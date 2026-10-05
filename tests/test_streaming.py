import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from acmanga.streaming import PageStream, chapter_key, prepare_stream, prune_pages, make_plan
from acmanga.errors import SourceError
from acmanga.state import clean_reader_view, default_state, save_progress, load_state
from acmanga.settings import load_settings, save_settings, default_settings

PNG=b'\x89PNG\r\n\x1a\n'+b'P'*100


def plan(count=10, servers=1):
    return {'source_name':'Test','referer':'https://example.test','url_sets':[
        ['https://example.test/{}/{}'.format(j,i) for i in range(1,count+1)] for j in range(servers)]}


def writer(calls, failed=()):
    def run(url,name,path,timeout,headers,retries=1):
        calls.append(url)
        if url in failed:
            raise SourceError('test failure',kind='download')
        Path(path).write_bytes(PNG)
        return PNG[:32],len(PNG)
    return run


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.streams=[]
    def tearDown(self):
        for s in self.streams:s.close()
        self.tmp.cleanup()
    def stream(self,*a,**k):
        s=PageStream(*a,**k);self.streams.append(s);return s

    def test_resume_requests_current_page_not_all_previous(self):
        calls=[]
        with mock.patch('acmanga.streaming._stream_image',side_effect=writer(calls)):
            s=self.stream(plan(30),self.root,workers=1);s.wait(18)
            self.assertEqual(calls,['https://example.test/0/18'])

    def test_bidirectional_prefetch_window_is_symmetric(self):
        calls=[]
        with mock.patch('acmanga.streaming._stream_image',side_effect=writer(calls)):
            s=self.stream(plan(30),self.root,prefetch=3,workers=2);s.wait(10);s.focus(10)
            until=time.monotonic()+1
            while len(s.ready)<7 and time.monotonic()<until:time.sleep(.01)
            self.assertEqual(set(s.ready),{7,8,9,10,11,12,13})
            self.assertEqual(len(calls),7)

    def test_prefetch_ten_keeps_ten_pages_on_each_side_when_available(self):
        calls=[]
        with mock.patch('acmanga.streaming._stream_image',side_effect=writer(calls)):
            s=self.stream(plan(40),self.root,prefetch=10,workers=2);s.wait(20);s.focus(20)
            until=time.monotonic()+2
            while len(s.ready)<21 and time.monotonic()<until:time.sleep(.01)
            self.assertEqual(set(s.ready),set(range(10,31)))
            self.assertEqual(len(calls),21)

    def test_bidirectional_prefetch_clamps_cleanly_at_chapter_edges(self):
        with mock.patch('threading.Thread.start'):
            s=self.stream(plan(12),self.root,prefetch=10,workers=2)
        s.request(1);s.focus(1)
        picked=[]
        with s.cv:
            while True:
                index=s._next_locked()
                if index is None:break
                picked.append(index);s.active.add(index)
        self.assertEqual(picked,list(range(1,12)))
        self.assertTrue(all(1 <= i <= 12 for i in picked))
        s.threads.clear()

    def test_partial_failure_keeps_successful_pages(self):
        calls=[]
        with mock.patch('acmanga.streaming._stream_image',side_effect=writer(calls,{'https://example.test/0/2'})):
            s=self.stream(plan(3),self.root,workers=1);one=s.wait(1)
            with self.assertRaises(SourceError):s.wait(2)
            self.assertTrue(one.exists());self.assertIn(1,s.ready)
            self.assertEqual(list(self.root.glob('*.part')),[])

    def test_retry_only_downloads_failed_page(self):
        calls=[]
        with mock.patch('acmanga.streaming._stream_image',side_effect=writer(calls,{'https://example.test/0/2'})):
            s=self.stream(plan(3),self.root,workers=1);s.wait(1)
            with self.assertRaises(SourceError):s.wait(2)
        with mock.patch('acmanga.streaming._stream_image',side_effect=writer(calls)):
            s.request(2,retry=True);s.wait(2)
        self.assertEqual(calls.count('https://example.test/0/1'),1)
        self.assertEqual(calls.count('https://example.test/0/2'),2)

    def test_valid_cache_avoids_network_on_reopen(self):
        calls=[]
        with mock.patch('acmanga.streaming._stream_image',side_effect=writer(calls)):
            s=self.stream(plan(),self.root);s.wait(4);s.close()
            other=self.stream(plan(),self.root);other.wait(4)
            self.assertEqual(len(calls),1)

    def test_corrupt_page_is_redownloaded(self):
        calls=[]
        with mock.patch('acmanga.streaming._stream_image',side_effect=writer(calls)):
            s=self.stream(plan(),self.root);path=s.wait(1);s.close();path.write_bytes(b'broken')
            other=self.stream(plan(),self.root);other.wait(1)
            self.assertEqual(len(calls),2)

    def test_alternate_server_used_for_only_failed_page(self):
        calls=[]
        with mock.patch('acmanga.streaming._stream_image',side_effect=writer(calls,{'https://example.test/0/1'})):
            s=self.stream(plan(3,2),self.root);s.wait(1)
            self.assertEqual(calls,['https://example.test/0/1','https://example.test/1/1'])

    def test_url_change_does_not_reuse_old_payload(self):
        calls=[]
        with mock.patch('acmanga.streaming._stream_image',side_effect=writer(calls)):
            s=self.stream(plan(3),self.root);s.wait(1);s.close()
            new=plan(3);new['url_sets'][0][0]='https://new.test/one'
            self.stream(new,self.root).wait(1)
            self.assertEqual(len(calls),2)

    def test_close_stops_scheduling_more_downloads(self):
        release=threading.Event();started=threading.Event();calls=[]
        def slow(*args,**kwargs):
            started.set();release.wait(1)
            return writer(calls)(*args,**kwargs)
        with mock.patch('acmanga.streaming._stream_image',side_effect=slow):
            s=self.stream(plan(30),self.root,workers=1);s.request(1);started.wait(1);s.close();release.set()
            for t in s.threads:t.join(1)
            self.assertEqual(len(calls),1);self.assertFalse(s.ready)
            self.assertFalse(list(self.root.glob('*.part')))

    def test_cached_name_cannot_escape_chapter(self):
        calls=[]
        with mock.patch('acmanga.streaming._stream_image',side_effect=writer(calls)):
            s=self.stream(plan(),self.root);s.wait(1);s.close()
            manifest=json.loads((self.root/'pages.json').read_text())
            manifest['files']['1']['name']='../secret.png'
            (self.root/'pages.json').write_text(json.dumps(manifest))
            self.stream(plan(),self.root).wait(1)
            self.assertEqual(len(calls),2)

    def test_discard_preserves_other_pages(self):
        calls=[]
        with mock.patch('acmanga.streaming._stream_image',side_effect=writer(calls)):
            s=self.stream(plan(),self.root);s.wait(1);s.wait(2);s.discard(2)
            self.assertTrue(s.ready[1].exists());self.assertNotIn(2,s.ready)

    def test_chapter_key_has_no_path_traversal(self):
        key=chapter_key({'source':'../../tmp','id':'/home/secret'})
        self.assertRegex(key,r'^ch-[a-f0-9]{32}$')

    def test_prune_keeps_active_and_noncache_directories(self):
        active=self.root/'pages'/('ch-'+'a'*32);old=self.root/'pages'/('ch-'+'b'*32)
        unrelated=self.root/'pages'/'personal'
        for p in [active,old,unrelated]:p.mkdir(parents=True)
        for p in [active,old]:
            with (p/'000001.jpg').open('wb') as f:f.truncate(40*1024*1024)
        prune_pages(self.root,keep={active.name},limit_mib=64)
        self.assertTrue(active.exists());self.assertFalse(old.exists());self.assertTrue(unrelated.exists())

    def test_reading_position_roundtrip_preserves_identity(self):
        path=self.root/'state.json';state=default_state()
        manga={'key':'mangakatana:test','title':'Test','variants':[{'source':'mangakatana','id':'test','title':'Test','ref':{'id':'test'}}]}
        ch={'source':'mangakatana','id':'test:c1','title_id':'test','number':'1'}
        save_progress(path,state,manga,ch,3,20,{'position':.62,'zoom':.2,'fit':'width'})
        new=load_state(path);rec=next(iter(new['progress'].values()))
        self.assertEqual(rec['page'],3);self.assertEqual(rec['view']['position'],.62)
        self.assertEqual(rec['chapter_id'],'test:c1');self.assertEqual(new['schema'],6)

    def test_reader_view_rejects_nan_and_bounds_values(self):
        clean=clean_reader_view({'position':float('nan'),'zoom':100,'horizontal':-999,'fit':'bad'})
        self.assertEqual(clean,{'position':0,'zoom':3,'horizontal':-1,'fit':'width'})

    def test_new_preferences_preserve_old_fields(self):
        path=self.root/'settings.json'
        path.write_text(json.dumps({'schema':2,'source_mode':'mangapill','source_priority':['mangapill','mangakatana'],'saved_sort':'title'}))
        prefs=load_settings(path)
        self.assertEqual(prefs['prefetch_pages'],3);self.assertEqual(prefs['source_mode'],'mangapill');self.assertEqual(prefs['reader_fit'],'page')
        prefs['cache_mib']=128;prefs['reader_fit']='page';save_settings(path,prefs)
        self.assertEqual(load_settings(path)['cache_mib'],128)

    def test_invalid_preferences_are_clamped(self):
        path=self.root/'settings.json';path.write_text(json.dumps({'prefetch_pages':99,'cache_mib':1,'scroll_step':float('nan')}))
        prefs=load_settings(path);self.assertEqual(prefs['prefetch_pages'],10);self.assertEqual(prefs['cache_mib'],64)
        self.assertEqual(prefs['scroll_step'],.10)
