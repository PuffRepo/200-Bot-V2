"""Local HTTP server and child-process doubles only; no bot or Railway calls."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import test_statistics as fixtures
from v2.public_feed import create_server, PublicFeed


class PublicFeedTest(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.StatisticsTest();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.fixture.apply()
        self.server=create_server(self.fixture.store.path)
        self.thread=Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop)
        self.base='http://127.0.0.1:'+str(self.server.server_port)

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)

    def get(self,path,method='GET'):
        try:
            with urlopen(Request(self.base+path,method=method),timeout=5) as response:
                return response.status,response.read(),response.headers
        except HTTPError as error:
            return error.code,error.read(),error.headers

    def test_only_sanitized_json_and_health_are_exposed(self):
        before=self.fixture.store.path.read_bytes()
        status,body,headers=self.get('/public/s9.json')
        self.assertEqual(status,200);data=json.loads(body)
        self.assertEqual(data['season'],'S9');self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertIsNotNone(headers['X-S9-Generated-At'])
        for secret in ('verified_by','actor_id','source_json','strikes','reason','operation_key'):
            self.assertNotIn(secret,body.decode())
        self.assertEqual(self.fixture.store.path.read_bytes(),before)
        self.assertTrue(json.loads(self.get('/health')[1])['ready'])
        self.assertEqual(self.get('/public/s9.json','HEAD')[1],b'')
        for route in ('/','/private/production.sqlite3','/../../private/production.json','/.env','/public/'):
            self.assertEqual(self.get(route)[0],404)
        self.assertEqual(self.get('/public/s9.json','POST')[0],405)

    def test_refresh_uses_new_data_and_failure_does_not_serve_stale_cache(self):
        feed=PublicFeed(self.fixture.store.path,refresh_seconds=60)
        with patch('v2.public_feed.time.monotonic',side_effect=[0,30,61,61,122]):
            first,_=feed.get()
            with self.fixture.store.connect() as db:db.execute("UPDATE season_ratings SET mmr=6010 WHERE user_id=12 AND season='S9'")
            cached,_=feed.get();self.assertEqual(first,cached)
            fresh,_=feed.get();self.assertNotEqual(json.loads(first)['players'][11]['mmr'],json.loads(fresh)['players'][11]['mmr'])
            with self.fixture.store.connect() as db:db.execute("UPDATE lab_settings SET value='LAB-INITIAL' WHERE key='current_season'")
            with self.assertRaises(ValueError):feed.get()

    def test_unavailable_source_returns_503_without_creating_it_or_disclosing_path(self):
        missing=self.fixture.root/'missing.sqlite3'
        server=create_server(missing);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            try:urlopen('http://127.0.0.1:'+str(server.server_port)+'/public/s9.json',timeout=5)
            except HTTPError as error:
                self.assertEqual(error.code,503);self.assertNotIn(str(missing).encode(),error.read())
            else:self.fail('Missing source was served successfully')
            self.assertFalse(missing.exists())
        finally:server.shutdown();server.server_close();thread.join(timeout=3)

    def test_supervisor_optional_worker_cleanup_and_launch_failure_do_not_duplicate_bot(self):
        path=Path(__file__).resolve().parents[1]/'scripts/production_with_backups.py'
        spec=importlib.util.spec_from_file_location('feed_supervisor_test',path)
        supervisor=importlib.util.module_from_spec(spec);spec.loader.exec_module(supervisor)
        config=self.fixture.root/'config.json';config.write_text(json.dumps({'database_path':str(self.fixture.store.path)}))
        class Child:
            def __init__(self,bot=False):self.bot=bot;self.calls=0;self.returncode=None;self.terminated=False
            def poll(self):
                self.calls+=1
                if self.bot and self.calls>=3:self.returncode=2
                return self.returncode
            def terminate(self):self.terminated=True;self.returncode=-15
            def wait(self,**kwargs):return self.returncode
        for fail in (False,True):
            with self.subTest(launch_failure=fail):
                bot,feed,backup=Child(True),Child(),Child()
                side_effect=[bot,OSError('test failure') if fail else feed,backup]
                with patch('sys.argv',['supervisor','--config',str(config)]),patch.dict('os.environ',{'LOUNGE_BACKUP_BUCKET':'test','LOUNGE_PUBLIC_FEED_ENABLED':'1'}),patch.object(supervisor.signal,'signal'),patch.object(supervisor.time,'sleep'),patch.object(supervisor.time,'monotonic',return_value=100),patch.object(supervisor.subprocess,'Popen',side_effect=side_effect) as launch:
                    self.assertEqual(supervisor.main(),2)
                self.assertEqual(launch.call_count,3)
                self.assertTrue(backup.terminated)
                if not fail:self.assertTrue(feed.terminated)
                self.assertEqual(sum('v2_production.py' in call.args[0][1] for call in launch.call_args_list),1)

    def test_supervisor_restarts_failed_feed_without_restarting_bot(self):
        path=Path(__file__).resolve().parents[1]/'scripts/production_with_backups.py'
        spec=importlib.util.spec_from_file_location('feed_restart_test',path)
        supervisor=importlib.util.module_from_spec(spec);spec.loader.exec_module(supervisor)
        config=self.fixture.root/'config.json';config.write_text(json.dumps({'database_path':str(self.fixture.store.path)}))
        class Child:
            def __init__(self,bot=False,failed=False):
                self.bot=bot;self.returncode=1 if failed else None;self.calls=0;self.terminated=False
            def poll(self):
                self.calls+=1
                if self.bot and self.calls>=5:self.returncode=2
                return self.returncode
            def terminate(self):self.terminated=True;self.returncode=-15
            def wait(self,**kwargs):return self.returncode
        bot,failed,backup,replacement=Child(True),Child(failed=True),Child(),Child()
        now=[100]
        def sleep(_):now[0]+=40
        with patch('sys.argv',['supervisor','--config',str(config)]),patch.dict('os.environ',{'LOUNGE_BACKUP_BUCKET':'test','LOUNGE_PUBLIC_FEED_ENABLED':'1'}),patch.object(supervisor.signal,'signal'),patch.object(supervisor.time,'sleep',side_effect=sleep),patch.object(supervisor.time,'monotonic',side_effect=lambda:now[0]),patch.object(supervisor.subprocess,'Popen',side_effect=[bot,failed,backup,replacement]) as launch:
            self.assertEqual(supervisor.main(),2)
        self.assertEqual(launch.call_count,4)
        self.assertTrue(backup.terminated);self.assertTrue(replacement.terminated)
        self.assertEqual(sum('v2_production.py' in call.args[0][1] for call in launch.call_args_list),1)
