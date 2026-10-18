"""Small public JSON endpoint. Never serves files or imports the Discord runtime."""
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from threading import Lock
import time
from urllib.parse import urlsplit

from v2.lab_store import LabError
from v2.statistics import snapshot


def read_public_snapshot(database):
    source = Path(database).resolve()
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True, timeout=3)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        data = snapshot(db)
        if any(int(p['user_id']) < 1 for p in data['players']):
            raise LabError('Refusing to publish fixture identities.')
        return data


class PublicFeed:
    def __init__(self, database, refresh_seconds=60):
        if not 1 <= refresh_seconds <= 300:
            raise ValueError('Refresh interval must be 1–300 seconds.')
        self.database = Path(database)
        self.refresh_seconds = refresh_seconds
        self.lock = Lock()
        self.cached = None
        self.refreshed = float('-inf')

    def get(self):
        with self.lock:
            if self.cached is not None and time.monotonic()-self.refreshed < self.refresh_seconds:
                return self.cached
            # A failed refresh returns an error, never old data with a new timestamp.
            data = read_public_snapshot(self.database)
            body = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',',':')).encode('utf-8')
            self.cached = body, data['generated_at']
            self.refreshed = time.monotonic()
            return self.cached


def create_server(database, host='127.0.0.1', port=0, refresh_seconds=60):
    feed = PublicFeed(database, refresh_seconds)

    class Handler(BaseHTTPRequestHandler):
        server_version = 'LoungePublicFeed'
        sys_version = ''

        def log_message(self, *_):
            pass

        def send_json(self, status, body, stamp=None):
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Length',str(len(body)))
            if stamp:
                self.send_header('X-S9-Generated-At',stamp)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path not in ('/public/s9.json','/health'):
                return self.send_json(404,b'{"error":"Not found"}')
            try:
                body, stamp = feed.get()
            except Exception as error:
                print(f'PUBLIC FEED ALERT: data unavailable ({type(error).__name__}).', flush=True)
                return self.send_json(503,b'{"error":"S9 public data unavailable"}')
            if path == '/health':
                body = json.dumps(dict(ready=True,season='S9',generated_at=stamp)).encode()
            self.send_json(200,body,stamp)

        do_HEAD = do_GET

        def do_POST(self):
            self.send_json(405,b'{"error":"Read-only endpoint"}')

        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST

    server = ThreadingHTTPServer((host,port), Handler)
    server.daemon_threads = True
    return server
