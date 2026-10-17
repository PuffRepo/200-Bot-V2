"""Consistent SQLite snapshots and verified S3-compatible off-volume backups."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('SQLite integrity check failed')
        if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise ValueError('SQLite foreign key check failed')


def snapshot(source, destination):
    """Includes committed WAL changes; never creates a missing source database."""
    source, destination = Path(source).resolve(), Path(destination)
    with destination.open('xb'):
        pass
    try:
        deadline = time.monotonic() + 120
        def progress(*_):
            if time.monotonic() > deadline:
                raise TimeoutError('Snapshot exceeded two minutes')
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src:
            with closing(sqlite3.connect(destination)) as dst:
                src.backup(dst, pages=256, progress=progress)
        validate(destination)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def settings():
    bucket = os.environ.get('LOUNGE_BACKUP_BUCKET', '').strip()
    prefix = os.environ.get('LOUNGE_BACKUP_PREFIX', '200-lounge/s9').strip('/')
    if not bucket or not prefix or not re.fullmatch(r'[A-Za-z0-9_./-]+', prefix):
        raise ValueError('Configure backup bucket and a nonempty dedicated prefix')
    keep = int(os.environ.get('LOUNGE_BACKUP_KEEP', '168'))
    if keep < 2:
        raise ValueError('Keep at least two backups')
    return bucket, prefix + '/', keep


def client():
    import boto3
    from botocore.config import Config
    endpoint = os.environ.get('LOUNGE_BACKUP_ENDPOINT') or None
    if endpoint and not endpoint.startswith('https://'):
        raise ValueError('Backup endpoint must use HTTPS')
    return boto3.client('s3', endpoint_url=endpoint,
                        config=Config(connect_timeout=10, read_timeout=60,
                                      retries={'max_attempts': 3}))


def key_pattern(prefix):
    return re.compile(re.escape(prefix) + r'\d{8}T\d{12}Z-[0-9a-f]{64}\.sqlite3')


def restore(s3, bucket, prefix, key, output):
    """Download and verify into a NEW file; never overwrite a production DB."""
    output = Path(output)
    if not key_pattern(prefix).fullmatch(key):
        raise ValueError('Key is not a backup owned by this prefix')
    if output.exists():
        raise FileExistsError('Restore output already exists')
    with tempfile.TemporaryDirectory(dir=output.parent) as directory:
        restored = Path(directory) / 'restored.sqlite3'
        s3.download_file(bucket, key, str(restored))
        expected = key.rsplit('-', 1)[1].removesuffix('.sqlite3')
        if digest(restored) != expected:
            raise ValueError('Downloaded backup checksum mismatch')
        validate(restored)
        # Exclusive create also protects against a file appearing during download.
        try:
            destination = output.open('xb')
        except FileExistsError:
            raise
        try:
            with destination, restored.open('rb') as source:
                import shutil
                shutil.copyfileobj(source, destination)
                destination.flush()
                os.fsync(destination.fileno())
        except BaseException:
            output.unlink(missing_ok=True)
            raise


def run_backup(source, s3, bucket, prefix, keep):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'snapshot.sqlite3'
        snapshot(source, path)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        key = prefix + stamp + '-' + digest(path) + '.sqlite3'
        s3.upload_file(str(path), bucket, key)
        # Verify the actual remote bytes and open the downloaded database.
        restore(s3, bucket, prefix, key, Path(directory) / 'verification.sqlite3')
        owned = []
        for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefix):
            owned.extend(item['Key'] for item in page.get('Contents', [])
                         if key_pattern(prefix).fullmatch(item['Key']))
        for old in sorted(owned, reverse=True)[keep:]:
            if old != key:
                s3.delete_object(Bucket=bucket, Key=old)
        return key
