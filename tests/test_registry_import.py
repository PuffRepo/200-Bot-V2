import copy,json,tempfile,unittest
from pathlib import Path
from v2.registry_import import make_plan,apply_plan,require_completed_transition
from v2.lab_store import LabStore,LabError
class RegistryImportTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.target=Path(self.temp.name)/'production.sqlite3'
        self.source={'summary':{'approved_carry_forward_total':3},'members':[{'discord_id':str(i),'display_name':n,'username':u,'migration_group':'verified_placement_carry_forward','all_role_ids':['100'],'historical_registration_records':[{'mkc_id_raw':'99','mkc_namespace':'unresolved'}]} for i,n,u in [(1,'Same','user1'),(2,'same','user2'),(3,'Café!','user3')]]}
    def test_preview_deterministic_unique_names_and_no_database(self):
        p=make_plan(self.source);self.assertEqual(p,make_plan(self.source));self.assertFalse(self.target.exists())
        self.assertEqual(len({x['name'] for x in p['players']}),3)
        self.assertTrue(all(x['mkc_id'] is None for x in p['players']))
    def test_names_preserved_and_source_tampering_rejected(self):
        p=make_plan(self.source)
        self.assertEqual([r['name'] for r in p['players']], ['Same','same','Café!'])
        self.assertEqual(p['summary']['name_changes'],0)
        p['players'][0]['name']='Changed'
        with self.assertRaises(LabError):apply_plan(self.source,p,self.target,True)
        self.assertFalse(self.target.exists())
    def test_import_and_restart_preserve_null_links_and_no_ratings_strikes(self):
        p=make_plan(self.source);apply_plan(self.source,p,self.target,True);s=LabStore(self.target)
        with s.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM players WHERE mkc_id IS NULL').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM strikes').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM tables').fetchone()[0],0)
            self.assertEqual(db.execute("SELECT value FROM lab_settings WHERE key='current_season'").fetchone()[0],'S9')
            self.assertEqual(db.execute('SELECT role_transition_applied FROM registry_import_manifest').fetchone()[0],0)
        original=self.target.read_bytes()
        with self.assertRaises(LabError):apply_plan(self.source,p,self.target,True)
        self.assertEqual(original,self.target.read_bytes())
    def test_bad_roster_and_conflicting_overrides_rejected(self):
        bad=copy.deepcopy(self.source);bad['members'][1]['discord_id']='1'
        with self.assertRaises(LabError):make_plan(bad)
        with self.assertRaises(LabError):make_plan(self.source,{'1':'A','2':'a'})
        with self.assertRaises(LabError):make_plan(self.source,{'999':'A'})
    def test_reinstatement_instructions_preserved_not_executed(self):
        self.source['members'][0]['staff_decision']={'remove_role_ids_on_transition':['100'],'live_applied':False}
        p=make_plan(self.source);apply_plan(self.source,p,self.target,True)
        with LabStore(self.target).connect() as db:
            row=json.loads(db.execute('SELECT source_json FROM registry_import_audit WHERE user_id=1').fetchone()[0])
            self.assertEqual(row['source_role_ids'],['100']);self.assertFalse(row['staff_decision']['live_applied'])

    def test_production_start_blocked_until_transition(self):
        p=make_plan(self.source);apply_plan(self.source,p,self.target,True)
        with LabStore(self.target).connect() as db:
            with self.assertRaisesRegex(LabError,'transition is pending'):require_completed_transition(db,461383953937596416)
            with self.assertRaises(LabError):require_completed_transition(db,123)
            db.execute('UPDATE registry_import_manifest SET role_transition_applied=1')
            require_completed_transition(db,461383953937596416)
