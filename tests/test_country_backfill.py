from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from v2.lab_store import LabStore
from v2.countries import set_country, player_countries
from v2.country_backfill import candidates, make_plan, apply_plan


class CountryBackfillTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'test.sqlite3';self.store=LabStore(self.path)
        for uid in (101,102,103):
            req=self.store.request_verify(uid,f'https://mkcentral.com/registry/players/profile?id={uid}')
            self.store.review(req,999,True,f'Player{uid}')
        self.fetch=lambda i:dict(id=i,country_code='JP',is_hidden=False)

    def plan(self):return make_plan(candidates(self.path),self.fetch,lambda _:None)

    def test_preview_read_only_and_explicit_clears_and_existing_flags_are_protected(self):
        set_country(self.store,101,'US',999,'Staff flag')
        set_country(self.store,102,'-',999,'Staff clear')
        with self.store.connect() as db:db.execute('UPDATE players SET mkc_id=NULL WHERE user_id=103')
        before=self.path.read_bytes()
        self.assertEqual(candidates(self.path),[])
        self.assertEqual(self.path.read_bytes(),before)
        with self.assertRaises(ValueError):
            with self.store.connect() as db:db.execute('UPDATE players SET mkc_id=103 WHERE user_id=103')
            candidates(self.path,0)

    def test_success_is_audited_idempotent_and_does_not_change_players_or_requests(self):
        with self.store.connect() as db:
            players=[tuple(r) for r in db.execute('SELECT * FROM players')]
            requests=[tuple(r) for r in db.execute('SELECT * FROM requests')]
        plan=self.plan()
        self.assertEqual(apply_plan(self.path,plan)['flags_added'],3)
        self.assertEqual(apply_plan(self.path,plan)['flags_added'],0)
        with self.store.connect() as db:
            self.assertEqual(player_countries(db),{101:'JP',102:'JP',103:'JP'})
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM players')],players)
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM requests')],requests)
            self.assertEqual(db.execute('SELECT count(*) FROM player_country_audit').fetchone()[0],3)
            self.assertIn('id=101',db.execute('SELECT reason FROM player_country_audit WHERE user_id=101').fetchone()[0])
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_mid_lookup_staff_change_clear_and_link_change_are_all_preserved(self):
        plan=self.plan()
        set_country(self.store,101,'US',999,'Staff flag')
        set_country(self.store,102,'-',999,'Staff clear')
        with self.store.connect() as db:db.execute('UPDATE players SET mkc_id=9999 WHERE user_id=103')
        self.assertEqual(apply_plan(self.path,plan)['flags_added'],0)
        with self.store.connect() as db:self.assertEqual(player_countries(db),{101:'US',102:''})

    def test_audit_failure_rolls_back_entire_batch(self):
        plan=self.plan()
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON player_country_audit WHEN NEW.user_id=102 BEGIN SELECT RAISE(ABORT,'audit failed'); END")
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):apply_plan(self.path,plan)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM player_countries').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM player_country_audit').fetchone()[0],0)

    def test_profile_identity_country_hidden_and_unavailable_fail_closed(self):
        profiles={101:dict(id=999,country_code='JP',is_hidden=False),
                  102:dict(id=102,country_code='ZZ',is_hidden=False),
                  103:dict(id=103,country_code='JP',is_hidden=True)}
        plan=make_plan(candidates(self.path),profiles.get,lambda _:None)
        self.assertEqual(plan['updates'],[])
        self.assertEqual(len(plan['skipped']),3)
        def offline(_):raise TimeoutError()
        self.assertEqual(make_plan(candidates(self.path),offline,lambda _:None)['updates'],[])

    def test_rate_limit_stops_remaining_requests(self):
        calls=[]
        def rate_limited(i):
            calls.append(i)
            raise HTTPError('https://mkcentral.com',429,'Too Many Requests',{},None)
        plan=make_plan(candidates(self.path),rate_limited,lambda _:None)
        self.assertEqual(len(calls),1)
        self.assertEqual(len(plan['skipped']),3)
        self.assertEqual({r['reason'] for r in plan['skipped']},{'rate_limited'})

    def test_imported_known_country_is_not_replaced_and_invalid_plan_never_writes(self):
        with self.store.connect() as db:
            db.execute('CREATE TABLE registry_import_audit(user_id INTEGER,source_json TEXT)')
            db.execute('INSERT INTO registry_import_audit VALUES(101,?)',('{"country_code":"FR"}',))
        self.assertEqual(len(candidates(self.path)),2)
        plan=self.plan();plan['updates'][0]['country_code']='ZZ'
        with self.assertRaises(ValueError):apply_plan(self.path,plan)
        self.assertFalse((self.path.parent/'missing.sqlite3').exists())
        with self.assertRaises(Exception):candidates(self.path.parent/'missing.sqlite3')
        self.assertFalse((self.path.parent/'missing.sqlite3').exists())
