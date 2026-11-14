"""Temporary SQLite, synthetic PNGs and fake HTTP; never contacts Discord."""
from contextlib import closing
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from PIL import Image

from v2.published_results import PublishedResults, attachment_url, validate_png, NoRedirect, ResultImageError
from v2.public_feed import create_server


class PublishedResultsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.database=Path(self.temp.name)/'test.sqlite3'
        with closing(sqlite3.connect(self.database)) as db:
            db.executescript('''CREATE TABLE lab_settings(key TEXT,value TEXT);
                INSERT INTO lab_settings VALUES('current_season','S9');
                CREATE TABLE tables(id INTEGER,status TEXT,season TEXT);
                INSERT INTO tables VALUES(1,'applied','S9');
                CREATE TABLE table_publications(table_id INTEGER,channel_id INTEGER,results_id INTEGER,results_state TEXT);
                INSERT INTO table_publications VALUES(1,123,456,'sent');''')
        buffer=BytesIO();Image.new('RGB',(2,2),'green').save(buffer,format='PNG');self.png=buffer.getvalue()
        self.message={'id':'456','channel_id':'123','author':{'id':'1553807544013684807'},
            'attachments':[{'filename':'results.png','size':len(self.png),
                            'url':'https://cdn.discordapp.com/attachments/123/789/results.png?ex=one&hm=signature'}]}
        self.calls=[]
        def fetch(url,headers,limit):
            self.calls.append((url,headers))
            return json.dumps(self.message).encode() if url.startswith('https://discord.com/api/') else self.png
        self.images=PublishedResults(self.database,fetch=fetch,token='test-secret')

    def update(self,sql):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(sql);db.commit()

    def test_exact_bytes_returned_cached_and_credentials_never_reach_cdn(self):
        before=self.database.read_bytes()
        self.assertEqual(self.images.get(1),self.png)
        self.assertEqual(self.images.get(1),self.png)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.calls[0][1]['Authorization'],'Bot test-secret')
        self.assertNotIn('Authorization',self.calls[1][1])
        self.assertEqual(self.database.read_bytes(),before)

    def test_missing_reversed_old_season_or_unpublished_tables_never_fetch_images(self):
        for sql in ["UPDATE tables SET status='reversed'", "UPDATE tables SET season='S8'",
                    "UPDATE lab_settings SET value='S8'", "UPDATE table_publications SET results_state='sending'",
                    "UPDATE table_publications SET results_id=NULL"]:
            with self.subTest(sql=sql):
                self.update(sql)
                with self.assertRaises(LookupError):self.images.get(1)
                self.update("UPDATE tables SET status='applied',season='S9'")
                self.update("UPDATE lab_settings SET value='S9'")
                self.update("UPDATE table_publications SET results_state='sent',results_id=456")
        with self.assertRaises(LookupError):self.images.get(999)
        self.assertEqual(self.calls,[])

    def test_reversed_table_cannot_use_cached_png(self):
        self.images.get(1)
        self.update("UPDATE tables SET status='reversed'")
        with self.assertRaises(LookupError):self.images.get(1)
        self.assertEqual(len(self.calls),2)

    def test_signed_url_is_resolved_again_after_cache_expiry(self):
        self.images.get(1)
        self.images.cache[next(iter(self.images.cache))]=(float('-inf'),self.png)
        self.message['attachments'][0]['url']='https://cdn.discordapp.com/attachments/123/789/results.png?ex=two'
        self.images.get(1)
        self.assertTrue(self.calls[-1][0].endswith('?ex=two'))

    def test_untrusted_attachment_urls_and_message_identities_are_rejected(self):
        for url in ['https://evil.example/results.png','http://cdn.discordapp.com/attachments/123/789/results.png',
                    'https://cdn.discordapp.com/attachments/999/789/results.png',
                    'https://secret@cdn.discordapp.com/attachments/123/789/results.png',
                    'https://cdn.discordapp.com:444/attachments/123/789/results.png']:
            with self.subTest(url=url):
                message=json.loads(json.dumps(self.message));message['attachments'][0]['url']=url
                with self.assertRaises(ValueError):attachment_url(message,123,456,'1553807544013684807')
        for field in ('id','channel_id','author'):
            message=json.loads(json.dumps(self.message));message[field]={'id':'999'} if field=='author' else '999'
            with self.assertRaises(ValueError):attachment_url(message,123,456,'1553807544013684807')
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,'Found',{},'https://evil.example'))

    def test_corrupt_and_oversized_pngs_fail_validation(self):
        for value in [b'not a PNG',b'\x89PNG\r\n\x1a\ninvalid',self.png+b'x'*(4*1024*1024)]:
            with self.assertRaises(Exception):validate_png(value)

    def test_diagnostic_labels_distinguish_failure_without_disclosing_metadata(self):
        self.images.token=None
        with patch.dict('os.environ',{},clear=True):
            with self.assertRaises(ResultImageError) as caught:self.images.get(1)
        self.assertEqual(caught.exception.code,'credentials_unavailable')
        message=json.loads(json.dumps(self.message));message['author']['id']='999'
        with self.assertRaises(ResultImageError) as caught:attachment_url(message,123,456,'1553807544013684807')
        self.assertEqual(caught.exception.code,'author_id_mismatch')
        self.assertNotIn('999',str(caught.exception))

    def test_a_reversal_during_download_blocks_delivery(self):
        original=self.images.fetch
        def raced(url,headers,limit):
            data=original(url,headers,limit)
            if 'cdn.discordapp.com' in url:self.update("UPDATE tables SET status='reversed'")
            return data
        self.images.fetch=raced
        with self.assertRaises(LookupError):self.images.get(1)
        self.assertEqual(len(self.images.cache),0)

    def test_http_routes_png_head_fallback_errors_and_never_expose_credentials(self):
        with patch('v2.published_results.PublishedResults',return_value=self.images):
            server=create_server(self.database)
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        def get(path,method='GET'):
            try:
                with urlopen(Request(base+path,method=method),timeout=5) as response:
                    return response.status,response.read(),response.headers
            except HTTPError as error:return error.code,error.read(),error.headers
        try:
            code,body,headers=get('/public/results/1.png')
            self.assertEqual((code,body),(200,self.png));self.assertEqual(headers['Content-Type'],'image/png')
            self.assertEqual(headers['Cache-Control'],'no-store')
            self.assertEqual(get('/public/results/1.png','HEAD')[1],b'')
            for route in ['/public/results/0.png','/public/results/../1.png','/public/results/999.png']:
                self.assertEqual(get(route)[0],404)
            self.assertEqual(get('/public/results/1.png','POST')[0],405)
            self.images.cache.clear()
            self.images.fetch=lambda *_: (_ for _ in ()).throw(ValueError('test-secret'))
            code,body,_=get('/public/results/1.png')
            self.assertEqual(code,503);self.assertNotIn(b'test-secret',body)
        finally:server.shutdown();server.server_close();thread.join(timeout=3)

