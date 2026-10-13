import json
import tempfile
import unittest
from pathlib import Path
from v2.runtime_config import load_runtime
from v2.lab_store import LabError
ROOT=Path(__file__).resolve().parents[1]
class RuntimeConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)/'config.json'
        self.config=json.loads((ROOT/'settings/production.example.json').read_text());self.config['enabled']=True;self.config['application_id']='999'
        self.config['roles'].update(reporter='2',placement='3',loungeless='4',chat_restricted='5',staff=['6'])
        self.config['roles']['ranks']={k:str(i) for i,k in enumerate(self.config['roles']['ranks'],10)}
        self.config['channels']={k:str(i) for i,k in enumerate(self.config['channels'],100)}
        self.config['tiers']={'tier-all':{'submission_channel_id':'200','results_channel_id':'201'},'tier-c':{'submission_channel_id':'202','results_channel_id':'203'},'tier-b':{'submission_channel_id':'204','results_channel_id':'205'},'tier-a':{'submission_channel_id':'206','results_channel_id':'207'}}
    def load(self,mode='production'):
        self.path.write_text(json.dumps(self.config));return load_runtime(self.path,mode)
    def test_complete_valid_config_preserves_ids_and_default_policy(self):
        result=self.load();self.assertEqual(result['guild_id'],461383953937596416);self.assertEqual(result['legacy_policy']['strike_limit'],3)
        self.assertEqual(result['roles']['ranks']['Emerald'],15)
    def test_disabled_missing_and_duplicate_roles_fail_closed(self):
        self.config['enabled']=False
        with self.assertRaises(LabError):self.load()
        self.config['enabled']=True;self.config['roles']['ranks']['Ruby']=None
        with self.assertRaises(LabError):self.load()
        self.config['roles']['ranks']['Ruby']='15'
        with self.assertRaises(LabError):self.load()
    def test_cross_environment_and_lab_database_block_production(self):
        with self.assertRaises(LabError):self.load('lab')
        self.config['guild_id']='1553806194257432726'
        with self.assertRaises(LabError):self.load('production')
        self.config['database_path']='lab-v2.sqlite3';self.assertEqual(self.load('lab')['mode'],'lab')
    def test_unsupported_tier_and_duplicate_submissions_block(self):
        self.config['tiers']['tier-c']['submission_channel_id']='200'
        with self.assertRaises(LabError):self.load()
        self.config['tiers']['tier-c']['submission_channel_id']='202';self.config['tiers']['tier-s']={}
        with self.assertRaises(LabError):self.load()
    def test_offline_check_accepts_disabled_but_never_skips_ids(self):
        self.config['enabled']=False;self.path.write_text(json.dumps(self.config))
        self.assertEqual(load_runtime(self.path,require_enabled=False)['mode'],'production')
        self.config['roles']['placement']=None;self.path.write_text(json.dumps(self.config))
        with self.assertRaises(LabError):load_runtime(self.path,require_enabled=False)
