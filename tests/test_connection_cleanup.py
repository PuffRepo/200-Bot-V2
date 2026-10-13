"""SQLite context exit must release handles without changing transactions."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from v2.lab_store import LabStore

class ConnectionCleanupTest(unittest.TestCase):
    def test_commit_and_close(self):
        with tempfile.TemporaryDirectory() as folder:
            store = LabStore(Path(folder) / 'lab.sqlite3')
            with store.connect() as db:
                db.execute('CREATE TABLE cleanup_test (value INTEGER)')
                db.execute('INSERT INTO cleanup_test VALUES (7)')
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute('SELECT 1')
            with store.connect() as check:
                self.assertEqual(check.execute('SELECT value FROM cleanup_test').fetchone()[0], 7)

    def test_rollback_and_close(self):
        with tempfile.TemporaryDirectory() as folder:
            store = LabStore(Path(folder) / 'lab.sqlite3')
            with store.connect() as db:
                db.execute('CREATE TABLE cleanup_test (value INTEGER)')
            with self.assertRaisesRegex(RuntimeError, 'rollback'):
                with store.connect() as db:
                    db.execute('INSERT INTO cleanup_test VALUES (7)')
                    raise RuntimeError('rollback')
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute('SELECT 1')
            with store.connect() as check:
                self.assertEqual(check.execute('SELECT COUNT(*) FROM cleanup_test').fetchone()[0], 0)
