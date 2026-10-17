from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import importlib.util

from v2.backups import snapshot, run_backup, restore


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.deleted = []
        self.corrupt = False

    def upload_file(self, filename, bucket, key):
        self.objects[key] = Path(filename).read_bytes()

    def download_file(self, bucket, key, filename):
        Path(filename).write_bytes(b'corrupted' if self.corrupt else self.objects[key])

    def get_paginator(self, _):
        return self

    def paginate(self, **kwargs):
        # More than one page; include an object outside our prefix deliberately.
        keys = list(self.objects)
        for start in range(0, len(keys), 2):
            yield {'Contents': [{'Key': key} for key in keys[start:start+2]]}

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)
        del self.objects[Key]


class ProductionBackupTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.db = self.root / 'production.sqlite3'
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('CREATE TABLE players(user_id INTEGER PRIMARY KEY, name TEXT)')
            db.execute('INSERT INTO players VALUES(1, ?)', ('国際 player ★',))
            db.commit()

    def tearDown(self):
        self.directory.cleanup()

    def test_wal_commits_included_without_uncommitted_changes(self):
        with closing(sqlite3.connect(self.db)) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute("INSERT INTO players VALUES(2, 'committed')")
            writer.commit()
            writer.execute("INSERT INTO players VALUES(3, 'uncommitted')")
            target = self.root / 'copy.sqlite3'
            snapshot(self.db, target)
            with closing(sqlite3.connect(target)) as copy:
                self.assertEqual(copy.execute('SELECT COUNT(*) FROM players').fetchone()[0], 2)
                self.assertEqual(copy.execute('SELECT name FROM players WHERE user_id=1').fetchone()[0], '国際 player ★')
            writer.rollback()

    def test_missing_source_is_not_created(self):
        missing = self.root / 'missing.sqlite3'
        with self.assertRaises(sqlite3.OperationalError):
            snapshot(missing, self.root / 'copy.sqlite3')
        self.assertFalse(missing.exists())
        self.assertFalse((self.root / 'copy.sqlite3').exists())

    def test_snapshot_refuses_existing_destination(self):
        before = self.db.read_bytes()
        with self.assertRaises(FileExistsError):
            snapshot(self.db, self.db)
        self.assertEqual(before, self.db.read_bytes())

    def test_round_trip_restore_and_retention_scope(self):
        s3 = FakeS3()
        s3.objects['other/snapshot.sqlite3'] = b'other'
        s3.objects['lounge/s9/notes.txt'] = b'notes'
        keys = [run_backup(self.db, s3, 'bucket', 'lounge/s9/', 2) for _ in range(4)]
        self.assertEqual(s3.deleted, keys[:2])
        self.assertIn('other/snapshot.sqlite3', s3.objects)
        self.assertIn('lounge/s9/notes.txt', s3.objects)
        output = self.root / 'restored.sqlite3'
        restore(s3, 'bucket', 'lounge/s9/', keys[-1], output)
        with closing(sqlite3.connect(output)) as restored:
            self.assertEqual(restored.execute('SELECT name FROM players').fetchone()[0], '国際 player ★')

    def test_failed_remote_verification_never_prunes(self):
        s3 = FakeS3()
        for _ in range(2):
            run_backup(self.db, s3, 'bucket', 'lounge/s9/', 2)
        before = set(s3.objects)
        s3.corrupt = True
        with self.assertRaises(ValueError):
            run_backup(self.db, s3, 'bucket', 'lounge/s9/', 2)
        self.assertTrue(before <= set(s3.objects))
        self.assertEqual(s3.deleted, [])

    def test_restore_refuses_existing_output(self):
        s3 = FakeS3()
        key = run_backup(self.db, s3, 'bucket', 'lounge/s9/', 2)
        before = self.db.read_bytes()
        with self.assertRaises(FileExistsError):
            restore(s3, 'bucket', 'lounge/s9/', key, self.db)
        self.assertEqual(before, self.db.read_bytes())

    def test_restore_rejects_foreign_key_and_corrupt_download(self):
        s3 = FakeS3()
        key = run_backup(self.db, s3, 'bucket', 'lounge/s9/', 2)
        output = self.root / 'restore.sqlite3'
        with self.assertRaises(ValueError):
            restore(s3, 'bucket', 'another/', key, output)
        s3.corrupt = True
        with self.assertRaises(ValueError):
            restore(s3, 'bucket', 'lounge/s9/', key, output)
        self.assertFalse(output.exists())

    def test_broken_foreign_key_fails_snapshot(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('CREATE TABLE linked(player INTEGER REFERENCES players(user_id))')
            db.execute('INSERT INTO linked VALUES(999)')
            db.commit()
        target = self.root / 'copy.sqlite3'
        with self.assertRaises(ValueError):
            snapshot(self.db, target)
        self.assertFalse(target.exists())

    def test_supervisor_stops_backup_when_bot_exits(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/production_with_backups.py'
        spec = importlib.util.spec_from_file_location('backup_supervisor_test', script)
        supervisor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(supervisor)
        config = self.root / 'config.json'
        config.write_text('{"database_path": "private/production.sqlite3"}')
        class Child:
            def __init__(self, is_bot):
                self.is_bot, self.calls, self.returncode, self.terminated = is_bot, 0, None, False
            def poll(self):
                self.calls += 1
                if self.is_bot and self.calls >= 2:
                    self.returncode = 2
                return self.returncode
            def terminate(self):
                self.terminated = True
                self.returncode = -15
            def wait(self, **kwargs):
                return self.returncode
        bot, worker = Child(True), Child(False)
        with patch('sys.argv', ['supervisor', '--config', str(config)]), \
             patch.dict('os.environ', {'LOUNGE_BACKUP_BUCKET': 'test'}), \
             patch.object(supervisor.signal, 'signal'), \
             patch.object(supervisor.time, 'sleep'), \
             patch.object(supervisor.subprocess, 'Popen', side_effect=[bot, worker]):
            self.assertEqual(supervisor.main(), 2)
        self.assertTrue(worker.terminated)

    def test_settings_require_dedicated_prefix_and_multiple_copies(self):
        from v2.backups import settings
        with patch.dict('os.environ', {'LOUNGE_BACKUP_BUCKET': 'b', 'LOUNGE_BACKUP_PREFIX': '/'}, clear=True):
            with self.assertRaises(ValueError):
                settings()
        with patch.dict('os.environ', {'LOUNGE_BACKUP_BUCKET': 'b', 'LOUNGE_BACKUP_KEEP': '1'}, clear=True):
            with self.assertRaises(ValueError):
                settings()


if __name__ == '__main__':
    unittest.main()
