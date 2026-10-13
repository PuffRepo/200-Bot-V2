import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from v2.lab_store import LabError, LabStore


class LabSeasonTest(unittest.TestCase):
    def test_transition_preserves_history_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(Path(directory) / 'lab.sqlite3')
            req = store.request_verify(123, 'https://mkcentral.com/registry/players/profile?id=123')
            store.review(req, 456, True, 'Tester')
            store.strike(456, 123, 'before', 0)
            self.assertEqual(store.strike_summary()['active_count'], 1)
            self.assertTrue(store.start_lab_season('LAB-S9', 456))
            self.assertFalse(store.start_lab_season('LAB-S9', 456))
            self.assertEqual(store.strike_summary(), {'season': 'LAB-S9', 'history_count': 1, 'season_count': 0, 'active_count': 0})
            store.strike(456, 123, 'after', 0)
            self.assertEqual(store.strike_summary(123)['active_count'], 1)
            self.assertEqual(store.strike_summary(123)['history_count'], 2)
            LabStore(store.path)
            self.assertEqual(store.strike_summary()['season'], 'LAB-S9')
            store.start_lab_season('LAB-S10', 456)
            with self.assertRaises(LabError):
                store.start_lab_season('LAB-S9', 456)
            with self.assertRaises(LabError):
                store.start_lab_season('S9', 456)

    def test_migrate_old_strikes_without_deleting_or_relabeling_later(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lab.sqlite3'
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE strikes(id INTEGER PRIMARY KEY, user_id INTEGER, issuer_id INTEGER, reason TEXT, mmr_penalty INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP, expires_at TEXT DEFAULT (datetime('now','+30 days')))")
                db.execute("INSERT INTO strikes(id,user_id,issuer_id,reason,mmr_penalty) VALUES(42,123,456,'original',50)")
            store = LabStore(path)
            store.start_lab_season('LAB-S9', 456)
            LabStore(path)
            with store.connect() as db:
                row = db.execute('SELECT id,reason,mmr_penalty,season FROM strikes').fetchone()
                self.assertEqual(tuple(row), (42, 'original', 50, 'LAB-INITIAL'))
