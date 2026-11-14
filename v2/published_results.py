"""Read the exact results PNG attached to this bot's recorded public message."""
from collections import OrderedDict
from contextlib import closing
from io import BytesIO
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import Lock
import time
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

from PIL import Image

MAX_IMAGE_BYTES = 4 * 1024 * 1024
APPLICATION_ID = '1553807544013684807'


class ResultImageError(ValueError):
    """Fixed diagnostic labels only; never exception text from HTTP clients."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


def read_url(url, headers, limit):
    with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=8) as response:
        if response.status != 200:
            raise ResultImageError('source_unavailable')
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ResultImageError('response_too_large')
    return data


def validate_png(data):
    if len(data) > MAX_IMAGE_BYTES or not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ResultImageError('invalid_png')
    with Image.open(BytesIO(data)) as image:
        if image.format != 'PNG' or not (1 <= image.width <= 4096 and 1 <= image.height <= 4096):
            raise ResultImageError('invalid_image_dimensions')
        image.verify()
    return data


def attachment_url(message, channel_id, message_id, application_id):
    if str(message.get('id')) != str(message_id):
        raise ResultImageError('message_id_mismatch')
    if str(message.get('channel_id')) != str(channel_id):
        raise ResultImageError('channel_id_mismatch')
    if str(message.get('author', {}).get('id')) != str(application_id):
        raise ResultImageError('author_id_mismatch')
    files = [a for a in message.get('attachments', []) if a.get('filename') == 'results.png']
    if len(files) != 1:
        raise ResultImageError('results_attachment_missing')
    item = files[0]
    if type(item.get('size')) is not int or not 0 < item['size'] <= MAX_IMAGE_BYTES:
        raise ResultImageError('attachment_too_large')
    url = item.get('url', '')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in ('cdn.discordapp.com', 'media.discordapp.net')
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not re.fullmatch(r'/attachments/' + re.escape(str(channel_id)) + r'/[0-9]+/results\.png', parsed.path)):
        raise ResultImageError('attachment_url_invalid')
    return url


class PublishedResults:
    def __init__(self, database, fetch=read_url, token=None, application_id=APPLICATION_ID):
        self.database, self.fetch, self.token = Path(database), fetch, token
        self.application_id = str(application_id)
        self.lock = Lock()
        self.cache = OrderedDict()

    def reference(self, table_id):
        with closing(sqlite3.connect(self.database.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
            # Check before every request, even a cache hit: reversed results stop serving immediately.
            row = db.execute('''SELECT p.channel_id,p.results_id FROM table_publications p
                JOIN tables t ON t.id=p.table_id
                WHERE t.id=? AND t.status='applied' AND t.season='S9'
                AND (SELECT value FROM lab_settings WHERE key='current_season')='S9'
                AND p.results_state='sent' AND p.results_id IS NOT NULL''', (table_id,)).fetchone()
        if not row:
            raise LookupError('Published applied S9 table not found')
        if any(type(value) is not int or value < 1 for value in row):
            raise ResultImageError('publication_reference_invalid')
        return row

    def get(self, table_id):
        channel_id, message_id = self.reference(table_id)
        cache_key = (table_id, channel_id, message_id)
        with self.lock:
            now = time.monotonic()
            cached = self.cache.get(cache_key)
            if cached and now - cached[0] < 300:
                self.cache.move_to_end(cache_key)
                return cached[1]
            token = self.token or os.environ.get('DISCORD_BOT_TOKEN')
            if not token:
                raise ResultImageError('credentials_unavailable')
            metadata = self.fetch(
                f'https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}',
                {'Authorization': 'Bot ' + token, 'User-Agent': '200-Lounge-V2/1.0'}, 256 * 1024)
            message = json.loads(metadata)
            url = attachment_url(message, channel_id, message_id, self.application_id)
            # Never forward Discord credentials to the CDN or to the website.
            data = validate_png(self.fetch(url, {'User-Agent': '200-Lounge-V2/1.0'}, MAX_IMAGE_BYTES))
            # A reversal or replacement can race the HTTP calls; recheck before publishing bytes.
            if self.reference(table_id) != (channel_id, message_id):
                raise LookupError('Publication changed during image retrieval')
            self.cache[cache_key] = (time.monotonic(), data)
            self.cache.move_to_end(cache_key)
            while len(self.cache) > 8:
                self.cache.popitem(last=False)
            return data
