import tempfile
import unittest
from pathlib import Path
from v2.lab_store import LabStore,LabError
from v2.countries import set_country,player_countries
from v2.bulk_countries import preview,apply,digest

class BulkCountryTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=LabStore(Path(self.temp.name)/'test.sqlite3')
        with self.store.connect() as db:
            for i in range(1,4):db.execute('INSERT INTO players(user_id,name,verified_by) VALUES (?,?,99)',(i,f'P{i}'))
        self.records=[{'discord_id':str(i),'country_code':'JP'} for i in range(1,4)]
    def plan(self):
        with self.store.connect() as db:return preview(db,self.records)
    def test_batch_fill_audit_and_duplicate_retry(self):
        plan=self.plan();self.assertEqual(apply(self.store,self.records,digest(plan),99,'Reviewed country export'),3)
        with self.assertRaises(LabError):apply(self.store,self.records,digest(plan),99,'Reviewed country export')
        with self.store.connect() as db:
            self.assertEqual(player_countries(db),{1:'JP',2:'JP',3:'JP'})
            self.assertEqual(db.execute('SELECT COUNT(*) FROM player_country_audit').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM players').fetchone()[0],3)
        self.assertEqual(apply(self.store,self.records,digest(self.plan()),99,'Reviewed country export'),0)
    def test_explicit_clears_and_staff_records_are_protected(self):
        set_country(self.store,1,'-',99,'Explicit clear')
        set_country(self.store,2,'US',99,'Verified correction')
        plan=self.plan();self.assertEqual([r['status'] for r in plan['rows']],['protected_staff_record','protected_staff_record','fill_missing'])
        self.assertEqual(apply(self.store,self.records,digest(plan),99,'Reviewed country export'),1)
    def test_stale_concurrent_preview_writes_nothing(self):
        plan=self.plan();set_country(self.store,3,'US',99,'Concurrent correction')
        with self.assertRaises(LabError):apply(self.store,self.records,digest(plan),99,'Reviewed country export')
        with self.store.connect() as db:self.assertEqual(player_countries(db),{3:'US'})
    def test_duplicate_invalid_ids_and_countries_rejected(self):
        for records in [[self.records[0]]*2,[{'discord_id':1,'country_code':'JP'}],[{'discord_id':'1','country_code':'../us'}]]:
            with self.store.connect() as db:
                with self.assertRaises(LabError):preview(db,records)
    def test_existing_import_country_conflict_is_skipped(self):
        with self.store.connect() as db:
            db.execute('CREATE TABLE registry_import_audit(user_id INTEGER,source_json TEXT)')
            db.execute('INSERT INTO registry_import_audit VALUES (1,?)',('{"country_code":"US"}',))
            plan=preview(db,self.records)
        self.assertEqual(plan['rows'][0]['status'],'conflicting_country')
        self.assertEqual(apply(self.store,self.records,digest(plan),99,'Reviewed country export'),2)

    def test_coverage_audit_is_read_only_and_refuses_missing_database(self):
        import sqlite3
        from scripts.audit_player_countries import audit
        set_country(self.store,1,'JP',99,'Verified profile')
        before=self.store.path.read_bytes()
        self.assertEqual(audit(self.store.path),dict(players=3,with_country=1,missing_country=2,missing_country_with_recorded_mkc_id=0))
        self.assertEqual(before,self.store.path.read_bytes())
        missing=self.store.path.with_name('missing.sqlite3')
        with self.assertRaises(sqlite3.OperationalError):audit(missing)
        self.assertFalse(missing.exists())
