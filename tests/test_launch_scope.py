import json
import tempfile
import unittest
from pathlib import Path
from v2.launch_scope import submission_tier
from v2.lab_store import LabStore, LabError
from v2.lab_ratings import LabRatings
ROOT=Path(__file__).resolve().parents[1]
class LaunchScopeTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=LabStore(Path(self.temp.name)/'test.sqlite3')
        with self.store.connect() as db:
            for i in range(1,13):db.execute('INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES (?,?,?,1)',(i,i,f'P{i}'))
        self.scores=' '.join(f'P{i} 82' for i in range(1,13))
    def test_each_live_channel_routes_to_own_tier(self):
        tiers=json.loads((ROOT/'settings/production.example.json').read_text())['tiers']
        for tier,item in tiers.items():
            self.assertEqual(submission_tier(tiers,item['submission_channel_id']),tier)
            with self.assertRaises(LabError):submission_tier(tiers,item['results_channel_id'])
        with self.assertRaises(LabError):submission_tier(tiers,'1010663109536534539')
    def test_ab_tables_apply_default_placement(self):
        for tier in ('tier-a','tier-b'):
            with self.subTest(tier=tier):
                tid=self.store.submit_table(90,3,self.scores,tier)
                result=LabRatings(self.store).preview(tid,routine=True)
                self.assertTrue(all(r['after']==3750 for r in result['rows']))
                self.store.cancel_table(tid,90,'test cleanup')
    def test_disabled_inputs_write_no_tables(self):
        for fmt,tier in [(6,'tier-all'),(3,'tier-s'),(3,'tier-sq')]:
            with self.assertRaises(LabError):self.store.submit_table(90,fmt,self.scores,tier)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM tables').fetchone()[0],0)
    def test_old_pending_6v6_cannot_be_approved(self):
        tid=self.store.submit_table(90,3,self.scores)
        with self.store.connect() as db:db.execute('UPDATE tables SET format_size=6 WHERE id=?',(tid,))
        with self.assertRaisesRegex(LabError,'6v6'):LabRatings(self.store).preview(tid,routine=True)
