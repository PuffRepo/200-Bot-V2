"""Public MKC fixtures and temporary SQLite; no live verification or API traffic."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

from v2.countries import player_countries
from v2.lab_store import LabStore, LabError
from v2.mkc_country import profile_country, read_profile, MAX_PROFILE_BYTES


class MKCCountryTest(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=LabStore(Path(self.temp.name)/'test.sqlite3')
        from v2.lab_ratings import LabRatings
        LabRatings(self.store)

    def request(self, uid=101, profile=8253):
        return self.store.request_verify(uid,f'https://mkcentral.com/registry/players/profile?id={profile}')

    def test_exact_public_profile_and_iso_country_required(self):
        profile={'id':8253,'country_code':'jp','is_hidden':False}
        self.assertEqual(profile_country(8253,lambda _:profile),'JP')
        for bad in [{**profile,'id':17},{**profile,'id':'8253'}, {**profile,'country_code':'ZZ'},
                    {**profile,'country_code':None},{**profile,'is_hidden':True}, {}, [], None]:
            self.assertEqual(profile_country(8253,lambda _:bad),'')
        for bad in [True,0,-1,'8253','../8253']:
            self.assertEqual(profile_country(bad,lambda _:self.fail('Unexpected fetch')),'')

    def test_network_and_invalid_json_leave_country_unavailable(self):
        for error in [URLError('offline'),TimeoutError(),json.JSONDecodeError('invalid','',0)]:
            def fail(_):raise error
            self.assertEqual(profile_country(8253,fail),'')

    def test_http_lookup_is_fixed_bounded_and_without_credentials(self):
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def read(self,limit):
                self.limit=limit
                return json.dumps({'id':8253,'country_code':'JP','is_hidden':False}).encode()
        response=Response()
        with patch('v2.mkc_country.build_opener') as opener:
            opener.return_value.open.return_value=response
            self.assertEqual(read_profile(8253)['country_code'],'JP')
            args,kwargs=opener.return_value.open.call_args
            self.assertEqual(args[0].full_url,'https://mkcentral.com/api/registry/players/8253')
            self.assertNotIn('Authorization',args[0].headers)
            self.assertEqual(kwargs['timeout'],8)
            self.assertEqual(response.limit,MAX_PROFILE_BYTES+1)
            response.read=lambda _:b'x'*(MAX_PROFILE_BYTES+1)
            self.assertIsNone(read_profile(8253))

    def test_approval_saves_country_and_audit_in_same_transaction(self):
        request=self.request()
        self.store.review(request,999,True,'International 名前',mkc_country='jp')
        with self.store.connect() as db:
            self.assertEqual(player_countries(db),{101:'JP'})
            audit=db.execute('SELECT user_id,before_code,after_code,actor_id,reason FROM player_country_audit').fetchone()
            self.assertEqual(tuple(audit[:4]),(101,'','JP',999))
            self.assertIn('id=8253',audit['reason'])
            self.assertEqual(db.execute('SELECT count(*) FROM rating_events').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM season_ratings').fetchone()[0],0)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        with self.assertRaises(LabError):self.store.review(request,999,True,'Again',mkc_country='US')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM player_country_audit').fetchone()[0],1)

    def test_country_audit_failure_rolls_back_registration_and_approval(self):
        request=self.request()
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail_country BEFORE INSERT ON player_country_audit BEGIN SELECT RAISE(ABORT,'audit unavailable'); END")
        with self.assertRaises(LabError):self.store.review(request,999,True,'Player',mkc_country='JP')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM players').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM player_countries').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT status FROM requests WHERE id=?',(request,)).fetchone()[0],'pending')

    def test_unknown_country_still_allows_verification_and_rejection_has_no_country_effect(self):
        self.store.review(self.request(),999,True,'Player')
        self.store.review(self.request(102,17),999,False,mkc_country='JP')
        with self.store.connect() as db:
            self.assertEqual(player_countries(db),{})
            self.assertEqual(db.execute('SELECT count(*) FROM players').fetchone()[0],1)

    def test_invalid_country_and_self_approval_do_not_register(self):
        request=self.request()
        for actor,code in [(999,'ZZ'),(101,'JP')]:
            with self.assertRaises(LabError):self.store.review(request,actor,True,'Player',mkc_country=code)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM players').fetchone()[0],0)

    def test_name_change_never_overwrites_existing_staff_country(self):
        from v2.countries import set_country
        self.store.review(self.request(),999,True,'Player',mkc_country='JP')
        set_country(self.store,101,'US',999,'Staff correction')
        self.store.review(self.store.request_name(101,'New name'),999,True,mkc_country='JP')
        with self.store.connect() as db:
            self.assertEqual(player_countries(db),{101:'US'})
            self.assertEqual(db.execute('SELECT count(*) FROM player_country_audit').fetchone()[0],2)
