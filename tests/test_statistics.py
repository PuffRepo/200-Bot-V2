"""Temporary SQLite only: public S9 statistics must never expose private audit data."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from v2.lab_store import LabStore, LabError
from v2.lab_ratings import LabRatings
from v2.loss_reductions import LossReductions
from v2.statistics import snapshot, read_player_statistics, player_statistics
from scripts.export_public_s9 import export


class StatisticsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=LabStore(self.root/'test.sqlite3')
        self.ratings=LabRatings(self.store);self.reductions=LossReductions(self.store)
        with self.store.connect() as db:
            db.execute("INSERT INTO lab_seasons(name,started_by) VALUES ('S9',999)")
            db.execute("UPDATE lab_settings SET value='S9' WHERE key='current_season'")
            for uid in range(1,14):
                db.execute('INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES (?,?,?,999)',(uid,uid,f'P{uid}'))
                db.execute("INSERT INTO season_ratings(season,user_id,mmr) VALUES ('S9',?,?)",(uid,6000 if uid<13 else None))

    def apply(self,tier='tier-all',scores=None):
        scores=scores or [100,100,90,90,85,85,80,80,70,70,67,67]
        table=self.store.submit_table(999,2,' '.join(f'P{i} {s}' for i,s in enumerate(scores,1)),tier=tier)
        self.ratings.apply(table,999,self.ratings.preview(table,True)['digest'],True)
        return table

    def data(self):
        with self.store.connect() as db:
            db.execute('BEGIN');return snapshot(db)

    def test_country_is_public_but_country_audit_stays_private(self):
        from v2.countries import set_country
        set_country(self.store,1,'JP',999,'PRIVATE PROFILE REASON')
        self.apply()
        data=self.data()
        self.assertEqual(data['players'][0]['country_code'],'JP')
        self.assertNotIn('PRIVATE PROFILE REASON',json.dumps(data))

    def test_registered_unplayed_and_unplaced_have_honest_empty_stats(self):
        result=read_player_statistics(self.store,13)
        self.assertIsNone(result['player']['mmr']);self.assertIsNone(result['player']['position'])
        self.assertEqual(result['summary']['tables'],0);self.assertIsNone(result['summary']['gain_rate'])
        self.assertEqual(read_player_statistics(self.store,12)['player']['position'],1)

    def test_refunds_penalties_and_peak_are_separate(self):
        table=self.apply()
        p=self.reductions.preview(table,12,999,'1/2','PRIVATE-REASON')
        self.reductions.apply(table,12,999,'1/2','PRIVATE-REASON',p['digest'])
        self.reductions.moderation.deduct(999,12,40,'PRIVATE-PENALTY','private-operation')
        result=read_player_statistics(self.store,12);s=result['summary']
        self.assertEqual((result['player']['mmr'],result['player']['peak_mmr']),(5860,6000))
        self.assertEqual((s['original_delta'],s['refunds'],s['net_table_delta']),(-200,100,-100))
        self.assertEqual((s['gains'],s['losses'],s['ties']),(0,1,0))
        self.assertEqual(s['partner_average'],67)
        rendered=json.dumps(self.data())
        for private in ('PRIVATE-REASON','PRIVATE-PENALTY','private-operation','verified_by','actor_id','source_json','evidence','operation_key'):
            self.assertNotIn(private,rendered)

    def test_placement_and_zero_credit_are_not_wins_or_losses(self):
        with self.store.connect() as db:db.execute("UPDATE season_ratings SET mmr=NULL WHERE season='S9'")
        self.apply()
        result=read_player_statistics(self.store,12)
        self.assertEqual((result['summary']['tables'],result['summary']['placements']),(1,1))
        self.assertIsNone(result['summary']['gain_rate']);self.assertEqual(result['summary']['net_table_delta'],0)

    def test_filters_and_last_use_application_order_and_current_rank(self):
        self.apply();last=self.apply('tier-c',[82]*12)
        result=read_player_statistics(self.store,12,last=1)
        self.assertEqual([r['table_id'] for r in result['history']],[last])
        self.assertEqual(read_player_statistics(self.store,12,tier='tier-all')['summary']['tables'],1)
        self.assertEqual(read_player_statistics(self.store,12,format_size=1)['summary']['tables'],0)
        for filters in ({'last':0},{'last':True},{'last':1001},{'tier':'tier-s'},{'format_size':6}):
            with self.assertRaises(LabError):read_player_statistics(self.store,12,**filters)

    def test_reversed_results_not_in_public_stats_and_rename_preserves_scores(self):
        table=self.apply()
        request=self.ratings.request_reversal(table,999,'Wrong scores','https://discord.com/channels/1/2/3')
        self.ratings.approve_reversal(request,998,self.ratings.reversal_preview(request)['digest'])
        self.assertEqual(read_player_statistics(self.store,12)['summary']['tables'],0)
        self.assertEqual(self.data()['tables'],[])

    def test_stable_ids_and_current_names_with_international_player(self):
        self.apply()
        with self.store.connect() as db:db.execute("UPDATE players SET name='国際 ★' WHERE user_id=12")
        result=read_player_statistics(self.store,12)
        self.assertEqual(result['player']['name'],'国際 ★')
        self.assertEqual(result['history'][0]['score'],67)
        self.assertEqual(result['history'][0]['played_as'],'P12')
        with self.store.connect() as db:
            db.execute("INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES (123456789123456789,NULL,'Large ID',999)")
        self.assertTrue(any(p['user_id']=='123456789123456789' for p in self.data()['players']))

    def test_export_is_read_only_atomic_and_public(self):
        self.apply();target=self.root/'public.json'
        before=self.store.path.read_bytes()
        self.assertEqual(export(self.store.path,target),13)
        self.assertEqual(self.store.path.read_bytes(),before)
        data=json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual((data['schema_version'],data['season']),(1,'S9'))
        self.assertEqual(export(self.store.path,target),13)
        self.assertEqual(len(json.loads(target.read_text(encoding='utf-8'))['tables']),1)
        self.assertEqual(list(self.root.glob('.s9-export-*')),[])

    def test_export_refuses_db_sidecars_fixtures_and_missing_source(self):
        for suffix in ('','-wal','-shm','-journal'):
            with self.assertRaises(LabError):export(self.store.path,Path(str(self.store.path)+suffix))
        missing=self.root/'missing.sqlite3'
        with self.assertRaises(sqlite3.OperationalError):export(missing,self.root/'missing.json')
        self.assertFalse(missing.exists())
        with self.store.connect() as db:db.execute("INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES (-1,-1,'Fixture',999)")
        with self.assertRaises(LabError):export(self.store.path,self.root/'fixture.json')
        self.assertFalse((self.root/'fixture.json').exists())

    def test_prior_season_and_unknown_player_rejected(self):
        with self.assertRaises(LabError):read_player_statistics(self.store,777)
        with self.store.connect() as db:db.execute("UPDATE lab_settings SET value='LAB-INITIAL' WHERE key='current_season'")
        with self.assertRaises(LabError):self.data()
