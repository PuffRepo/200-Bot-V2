import asyncio
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import tempfile
import unittest
from pathlib import Path
from v2.lab_store import LabStore, LabError
from v2.lab_ratings import LabRatings, POLICY_PATH
from v2.season_policy import load_policy
from v2.rating import calculate_table, placement_mmr
from v2.rating_preview import calculate_pre_mmr, calculate_mmr

class S9MathTest(unittest.TestCase):
    def setUp(self): self.policy=load_policy(POLICY_PATH)

    def test_all_placement_boundaries(self):
        for tier,cutoffs in [('tier-all',[50,80,110,140]),('tier-c',[60,90,120,150])]:
            values=[1000,2250,3750,5250,6000]
            for i,score in enumerate(cutoffs):
                self.assertEqual(placement_mmr(self.policy,score-1,tier),values[i])
                self.assertEqual(placement_mmr(self.policy,score,tier),values[i+1])
                self.assertEqual(placement_mmr(self.policy,score+1,tier),values[i+1])
            self.assertEqual(placement_mmr(self.policy,180,tier),6000)

    def test_legacy_reference_table_is_unchanged(self):
        scores=[129,70,66,96,95,54,108,80,53,87,81,65]
        mmrs=[10304,2741,2223,5189,3809,218,6050,4120,1954,2531,6833,3550]
        players=[{'user_id':i,'name':str(i),'score':s,'mmr':m} for i,(s,m) in enumerate(zip(scores,mmrs))]
        rows=calculate_table(self.policy,players,3,'tier-all')
        self.assertEqual([r['raw_delta'] for r in rows],[124]*3+[128]*3+[-58]*3+[-196]*3)

    def test_mixed_snapshot_and_no_placement_table_delta(self):
        scores=[100]*3+[90]*3+[75]*3+[63]*3
        mmrs=[6000,None,None]+[6000]*9
        players=[{'user_id':i,'name':str(i),'score':s,'mmr':m} for i,(s,m) in enumerate(zip(scores,mmrs))]
        rows=calculate_table(self.policy,players,3,'tier-all')
        self.assertEqual(rows[0]['after'],6266)
        self.assertEqual(rows[1]['after'],3750)
        self.assertIsNone(rows[1]['applied_delta'])

    def test_clamp_all_formats_and_floor(self):
        for fmt in (1,2,3,4,6):
            from scripts.test_rating_edges import scores_for
            scores=scores_for(fmt,'win')
            players=[{'user_id':i,'name':str(i),'score':score,'mmr':30000 if i<fmt else 1000} for i,score in enumerate(scores)]
            rows=calculate_table(self.policy,players,fmt,'tier-all')
            self.assertEqual(rows[0]['raw_delta'],12//fmt-1)
        players=[{'user_id':i,'name':str(i),'score':s,'mmr':1 if i==0 else 6000} for i,s in enumerate([0]+[90]*10+[84])]
        rows=calculate_table(self.policy,players,1,'tier-all')
        self.assertEqual(rows[0]['after'],1)
        self.assertEqual(rows[0]['applied_delta'],0)
        self.assertLess(rows[0]['raw_delta'],0)

    def test_unequal_ties_match_legacy(self):
        players=[{'user_id':i,'name':str(i),'score':82,'mmr':1000+i*800} for i in range(12)]
        for fmt in (1,2,3,4,6):
            teams=[[i,82*fmt,sum(p['mmr'] for p in players[i:i+fmt])/fmt,1] for i in range(0,12,fmt)]
            async def legacy():
                await calculate_mmr(teams,await calculate_pre_mmr(fmt,teams))
            asyncio.run(legacy())
            rows=calculate_table(self.policy,players,fmt,'tier-all')
            self.assertEqual([rows[i]['raw_delta'] for i in range(0,12,fmt)],[t[-1] for t in teams])

class S9LedgerTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=LabStore(Path(self.temp.name)/'lab.sqlite3')
        with self.store.connect() as db:
            for i in range(1,13):
                db.execute('INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES (?,?,?,-1)',(-100000-i,-200000-i,f'LAB{i:02d}'))
        self.ratings=LabRatings(self.store)

    def submit(self,scores=None,tier='tier-all'):
        scores=scores or [82]*12
        table=self.store.submit_table(111,3,' '.join(f'LAB{i:02d} {score}' for i,score in enumerate(scores,1)),tier)
        self.store.review_table(table,222,True)
        return table

    def test_placement_apply_once_and_restart(self):
        table=self.submit([49,50,79,80,109,110,139,140,0,60,80,88])
        preview=self.ratings.preview(table)
        with self.assertRaises(LabError):self.ratings.apply(table,111,preview['digest'])
        with self.assertRaises(LabError):self.ratings.apply(table,222,'wrong')
        rows=self.ratings.apply(table,222,preview['digest'])
        self.assertEqual([r['after'] for r in rows],[1000,2250,2250,3750,3750,5250,5250,6000,1000,2250,3750,3750])
        with self.assertRaises(LabError):self.ratings.apply(table,222,preview['digest'])
        self.assertEqual(LabRatings(LabStore(self.store.path)).ratings(),self.ratings.ratings())
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM rating_events').fetchone()[0],12)

    def test_stale_preview_and_atomic_rollback(self):
        first=self.submit();second=self.submit([83,81]+[82]*10)
        stale=self.ratings.preview(second)
        self.ratings.apply(first,222,self.ratings.preview(first)['digest'])
        with self.assertRaises(LabError):self.ratings.apply(second,222,stale['digest'])
        before=self.ratings.ratings()
        fresh=self.ratings.preview(second)
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail_event BEFORE INSERT ON rating_events WHEN NEW.user_id=-100005 BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.ratings.apply(second,222,fresh['digest'])
        self.assertEqual(self.ratings.ratings(),before)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM tables WHERE id=?',(second,)).fetchone()[0],'validated')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM rating_applications WHERE table_id=?',(second,)).fetchone()[0],0)

    def test_concurrent_application_only_one_succeeds(self):
        table=self.submit();digest=self.ratings.preview(table)['digest']
        def attempt():
            try:self.ratings.apply(table,222,digest);return True
            except LabError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:attempt(),range(2)))
        self.assertEqual(sorted(results),[False,True])

    def test_reordered_teams_and_players_are_duplicates(self):
        table=self.submit()
        groups=[[f'LAB{i:02d} 82' for i in range(start,start+3)] for start in (1,4,7,10)]
        raw=' '.join(' '.join(reversed(g)) for g in reversed(groups))
        with self.assertRaises(LabError):self.store.submit_table(333,3,raw,'tier-c')

    def test_season_reset_preserves_old_ratings_and_repeat_does_not_reset(self):
        table=self.submit();self.ratings.apply(table,222,self.ratings.preview(table)['digest'])
        self.assertTrue(self.store.start_lab_season('LAB-S9',222))
        self.assertTrue(all(r['mmr'] is None for r in self.ratings.ratings()[1]))
        new=self.submit();self.ratings.apply(new,222,self.ratings.preview(new)['digest'])
        before=self.ratings.ratings()
        self.assertFalse(self.store.start_lab_season('LAB-S9',222))
        self.assertEqual(self.ratings.ratings(),before)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM season_ratings').fetchone()[0],24)

    def test_fixture_case_and_old_table_block(self):
        old=self.submit()
        self.ratings.fixture_case('boundaries','LAB-S901',222)
        with self.assertRaises(LabError):self.ratings.preview(old)
        with self.assertRaises(LabError):self.ratings.fixture_case('placements','LAB-S901',222)
        rows=self.ratings.ratings()[1]
        self.assertEqual(rows[8]['rank'],'Platinum');self.assertEqual(rows[9]['rank'],'Emerald')
        self.assertEqual(rows[10]['rank'],'Master');self.assertEqual(rows[11]['rank'],'Grandmaster')

if __name__=='__main__':unittest.main()
