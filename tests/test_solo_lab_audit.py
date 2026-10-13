import tempfile
import unittest
from pathlib import Path
from scripts.solo_lab_audit import audit
from v2.lab_store import LabStore
from v2.lab_ratings import LabRatings

class SoloAuditTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'lab.sqlite3'
        self.store=LabStore(self.path)
        self.ratings=LabRatings(self.store)
        with self.store.connect() as db:
            for i in range(1,13):
                db.execute('INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES (?,?,?,-1)',(-100000-i,-200000-i,f'LAB{i:02d}'))

    def test_read_only_and_stable_fingerprint(self):
        before=self.path.read_bytes()
        first=audit(self.path);second=audit(self.path)
        self.assertTrue(first['consistent'])
        self.assertEqual(first,second)
        self.assertEqual(before,self.path.read_bytes())

    def test_applied_table_and_restart(self):
        table=self.store.submit_table(1,3,' '.join(f'LAB{i:02d} 82' for i in range(1,13)))
        self.store.review_table(table,2,True)
        self.ratings.apply(table,2,self.ratings.preview(table)['digest'])
        first=audit(self.path)
        self.assertTrue(first['consistent'])
        LabRatings(LabStore(self.path))
        self.assertEqual(first['state_fingerprint'],audit(self.path)['state_fingerprint'])

    def test_corrupt_event_is_flagged(self):
        table=self.store.submit_table(1,3,' '.join(f'LAB{i:02d} 82' for i in range(1,13)))
        self.store.review_table(table,2,True)
        self.ratings.apply(table,2,self.ratings.preview(table)['digest'])
        with self.store.connect() as db:
            db.execute('UPDATE rating_events SET before_mmr=1 WHERE table_id=?',(table,))
        report=audit(self.path)
        self.assertFalse(report['consistent'])
        self.assertEqual(report['checks']['event_balance_errors'],12)

    def test_missing_db_not_created(self):
        import sqlite3
        missing=self.path.with_name('missing.sqlite3')
        with self.assertRaises(sqlite3.OperationalError):audit(missing)
        self.assertFalse(missing.exists())

if __name__=='__main__':unittest.main()
