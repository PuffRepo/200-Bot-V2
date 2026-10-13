import tempfile
import unittest
from pathlib import Path
from v2.lab_store import LabStore,LabError
from v2.lab_ratings import LabRatings
from v2.moderation import Moderation

class RestoredModerationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=LabStore(Path(self.temp.name)/'lab.sqlite3');self.ratings=LabRatings(self.store);self.mod=Moderation(self.store)
        with self.store.connect() as db:
            for uid in (101,102):db.execute('INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES (?,?,?,999)',(uid,uid,f'Player{uid}'))
            db.execute("INSERT INTO season_ratings(season,user_id,mmr,revision) VALUES ('LAB-INITIAL',101,6000,0)")
    def mmr(self,uid=101):
        with self.store.connect() as db:
            row=db.execute("SELECT mmr FROM season_ratings WHERE user_id=? AND season=(SELECT value FROM lab_settings WHERE key='current_season')",(uid,)).fetchone()
            return row[0] if row else None
    def test_actual_deduction_floor_no_strike_and_idempotence(self):
        result=self.mod.deduct(999,101,10000,'Test','deduct1')
        self.assertEqual(result['after'],1);self.assertEqual(result['delta'],-5999)
        self.assertEqual(self.store.strike_summary()['history_count'],0)
        with self.assertRaises(LabError):self.mod.deduct(999,101,10000,'Test','deduct1')
        self.assertEqual(self.mmr(),1)
    def test_unplaced_deduction_rejected_strike_has_no_rating_assignment(self):
        with self.assertRaises(LabError):self.mod.deduct(999,102,100,'Test','deduct2')
        result=self.mod.strike(999,102,100,'Test','strike2')
        self.assertEqual(result['delta'],0);self.assertIsNone(self.mmr(102))
        self.mod.unstrike(999,result['strike_id'],'Correction','remove2')
        self.assertIsNone(self.mmr(102))
    def test_strike_and_removal_refund_actual_delta_history_preserved(self):
        result=self.mod.strike(999,101,10000,'Test','strike1')
        self.assertEqual(self.mmr(),1)
        self.mod.unstrike(999,result['strike_id'],'Correction','remove1')
        self.assertEqual(self.mmr(),6000)
        summary=self.store.strike_summary(101)
        self.assertEqual((summary['season_count'],summary['active_count'],summary['history_count']),(0,0,1))
        with self.assertRaises(LabError):self.mod.unstrike(999,result['strike_id'],'Repeat','remove-repeat')
    def test_third_strike_restriction_and_unstrike_release(self):
        for i in range(3):result=self.mod.strike(999,101,0,'Test',f'strike{i}')
        self.assertTrue(self.mod.member_state(101)['restricted'])
        self.mod.unstrike(999,result['strike_id'],'Correction','release')
        self.assertFalse(self.mod.member_state(101)['restricted'])
    def test_expiration_and_new_season_release_preserve_history(self):
        for i in range(3):self.mod.strike(999,101,0,'Test',f's{i}')
        with self.store.connect() as db:db.execute("UPDATE member_restrictions SET until_at=datetime('now','-1 second')")
        self.assertFalse(self.mod.member_state(101)['restricted'])
        self.store.start_lab_season('LAB-S997',999)
        self.assertEqual(self.store.strike_summary()['active_count'],0)
        self.assertEqual(self.store.strike_summary()['history_count'],3)
        self.assertIsNone(self.mmr())
    def test_removal_self_old_and_previous_season_denied(self):
        result=self.mod.strike(999,101,100,'Test','s1');sid=result['strike_id']
        with self.assertRaises(LabError):self.mod.unstrike(101,sid,'Self','self')
        with self.store.connect() as db:db.execute("UPDATE strikes SET created_at=datetime('now','-25 hours') WHERE id=?",(sid,))
        with self.assertRaises(LabError):self.mod.unstrike(999,sid,'Old','old')
        self.store.start_lab_season('LAB-S998',999)
        with self.assertRaises(LabError):self.mod.unstrike(999,sid,'Previous','previous')
        self.assertIsNone(self.mmr())
    def test_duplicate_strike_key_rolls_back(self):
        self.mod.strike(999,101,100,'Test','same')
        with self.assertRaises(LabError):self.mod.strike(999,101,100,'Test','same')
        self.assertEqual(self.mmr(),5900);self.assertEqual(self.store.strike_summary()['history_count'],1)
    def test_validation_and_unknown_player_no_partial_commit(self):
        for points in (-1,10001,True):
            with self.assertRaises(LabError):self.mod.deduct(999,101,points,'Test',str(points))
        with self.assertRaises(LabError):self.mod.strike(999,777,100,'Test','unknown')
        self.assertEqual(self.mmr(),6000);self.assertEqual(self.store.strike_summary()['history_count'],0)
    def test_removing_non_trigger_strike_releases_below_threshold(self):
        first=self.mod.strike(999,101,0,'Test','first')
        self.mod.strike(999,101,0,'Test','second');self.mod.strike(999,101,0,'Test','third')
        self.mod.unstrike(999,first['strike_id'],'Correction','remove-first')
        self.assertFalse(self.mod.member_state(101)['restricted'])
    def test_adjustment_invalidates_preview_and_blocks_lossy_reversal(self):
        with self.store.connect() as db:
            for i in range(11):db.execute('INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES (?,?,?,999)',(200+i,200+i,f'Other{i}'))
        raw='Player101 82 '+' '.join(f'Other{i} 82' for i in range(11))
        table=self.store.submit_table(888,1,raw)
        digest=self.ratings.preview(table,True)['digest']
        self.mod.deduct(999,101,100,'Test','before-table')
        with self.assertRaises(LabError):self.ratings.apply(table,888,digest,True)
        self.ratings.apply(table,888,self.ratings.preview(table,True)['digest'],True)
        request=self.ratings.request_reversal(table,888,'Correction','https://discord.com/channels/1/2/3')
        with self.assertRaises(LabError):self.mod.deduct(999,101,100,'Blocked','during-review')
        self.ratings.reject_reversal(request,999)
        self.mod.deduct(999,101,100,'Test','after-table')
        with self.assertRaises(LabError):self.ratings.request_reversal(table,888,'Correction','https://discord.com/channels/1/2/3')
