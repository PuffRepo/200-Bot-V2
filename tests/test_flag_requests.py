"""Country-request transactions against temporary SQLite; no Discord/API access."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from v2.lab_store import LabStore,LabError
from v2.lab_ratings import LabRatings
from v2.loss_reductions import LossReductions
from v2.countries import player_countries,set_country
from v2.flag_requests import FlagRequests,country_choices,normalize_country

class FlagRequestTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=LabStore(Path(self.temp.name)/'test.sqlite3');LabRatings(self.store);LossReductions(self.store)
        self.flags=FlagRequests(self.store)
        with self.store.connect() as db:
            for uid in (1,2):db.execute('INSERT INTO players(user_id,name,verified_by) VALUES (?,?,99)',(uid,f'P{uid}'))
    def countries(self):
        with self.store.connect() as db:return player_countries(db)
    def test_registered_only_valid_country_and_one_pending(self):
        with self.assertRaises(LabError):self.flags.request(999,'JP')
        for invalid in ('ZZ','../us','JP] 100','unknown country'):
            with self.assertRaises(LabError):self.flags.request(1,invalid)
        rid=self.flags.request(1,'Japan','My country')
        with self.assertRaises(LabError):self.flags.request(1,'US')
        self.assertEqual(self.countries(),{})
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM flag_requests WHERE id=?',(rid,)).fetchone()
            self.assertEqual((row['country_code'],row['reason'],row['status']),('JP','My country','pending'))
    def test_approved_country_audit_status_and_no_rating_writes(self):
        rid=self.flags.request(1,' jp ','Member context')
        with self.store.connect() as db:
            before={table:[tuple(r) for r in db.execute('SELECT * FROM '+table)] for table in ('players','season_ratings','rating_events','member_sync_jobs')}
        preview=self.flags.preview(rid,99)
        self.assertEqual(self.flags.review(rid,99,True,preview['digest']),'approved')
        self.assertEqual(self.countries(),{1:'JP'})
        with self.store.connect() as db:
            audit=db.execute('SELECT before_code,after_code,actor_id,reason FROM player_country_audit').fetchone()
            self.assertEqual(tuple(audit)[:3],('','JP',99));self.assertIn('Member context',audit['reason'])
            self.assertEqual(db.execute('SELECT reviewer_id FROM flag_requests WHERE id=?',(rid,)).fetchone()[0],99)
            self.assertEqual(before,{table:[tuple(r) for r in db.execute('SELECT * FROM '+table)] for table in before})
        with self.assertRaises(LabError):self.flags.review(rid,99,True,preview['digest'])
        with self.assertRaises(LabError):self.flags.request(1,'JP')
    def test_rejection_and_self_review_protection(self):
        rid=self.flags.request(1,'JP')
        for approve in (True,False):
            with self.assertRaises(LabError):self.flags.review(rid,1,approve)
        self.assertEqual(self.flags.review(rid,99,False),'rejected');self.assertEqual(self.countries(),{})
        self.assertGreater(self.flags.request(1,'US'),rid)
    def test_concurrent_staff_country_change_and_clear_prevent_stale_approval(self):
        for new in ('US','-'):
            with self.subTest(new=new):
                rid=self.flags.request(1,'JP');preview=self.flags.preview(rid,99)
                set_country(self.store,1,new,100,'Staff correction')
                self.assertTrue(self.flags.preview(rid,99)['stale'])
                with self.assertRaises(LabError):self.flags.review(rid,99,True,preview['digest'])
                self.assertEqual(self.countries()[1],'' if new=='-' else new)
                self.flags.review(rid,99,False)
    def test_concurrent_approvals_write_only_one_country_audit(self):
        rid=self.flags.request(1,'JP')
        def approve(actor):
            try:return self.flags.review(rid,actor,True,self.flags.preview(rid,actor)['digest'])
            except LabError:return 'blocked'
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(approve,(99,100)))
        self.assertEqual(sorted(results),['approved','blocked'])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM player_country_audit').fetchone()[0],1)
    def test_audit_failure_rolls_back_country_and_request_status(self):
        import sqlite3
        rid=self.flags.request(1,'JP');preview=self.flags.preview(rid,99)
        with self.store.connect() as db:db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON player_country_audit BEGIN SELECT RAISE(ABORT,'audit failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.flags.review(rid,99,True,preview['digest'])
        self.assertEqual(self.countries(),{})
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT status FROM flag_requests WHERE id=?',(rid,)).fetchone()[0],'pending')
    def test_wrong_reviewer_digest_and_unrelated_country_change(self):
        rid=self.flags.request(1,'JP');preview=self.flags.preview(rid,99)
        with self.assertRaises(LabError):self.flags.review(rid,100,True,preview['digest'])
        set_country(self.store,2,'US',100,'Unrelated update')
        self.assertEqual(self.flags.review(rid,99,True,preview['digest']),'approved')
    def test_country_suggestions_codes_names_and_limit(self):
        self.assertEqual(country_choices('japan'),[('Japan (JP)','JP')])
        self.assertEqual(country_choices('jp')[0][1],'JP')
        self.assertLessEqual(len(country_choices('')),25)
        self.assertEqual(normalize_country('United States'),'US')
    def test_restart_adds_flag_table_without_rebuilding_old_requests(self):
        pending=self.store.request_name(1,'Changed')
        with self.store.connect() as db:
            schema=db.execute("SELECT sql FROM sqlite_master WHERE name='requests'").fetchone()[0]
            db.execute('DROP TABLE flag_requests')
        LabStore(self.store.path)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT sql FROM sqlite_master WHERE name='requests'").fetchone()[0],schema)
            self.assertEqual(db.execute('SELECT status FROM requests WHERE id=?',(pending,)).fetchone()[0],'pending')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.flags.request(1,'JP')
