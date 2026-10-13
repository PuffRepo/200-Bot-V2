import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from contextlib import closing
from types import SimpleNamespace
from v2.role_transition_apply import digest,validate_snapshot,check_population,check_member,apply_member,finish_database

CONFIG={'guild_id':'1','rank_role_ids':['10','11','15'],'placement_role_id':'12','restriction_role_ids':['13','14']}
def row(uid='100',status='change'):
    return {'discord_id':uid,'registry_name':'Player','status':status,'add_role_ids':['12'] if status=='change' else [],'remove_role_ids':['10','11'] if status=='change' else [],'preserve_role_ids':['1','99'] if status=='change' else ['1','12','99'] if status=='unchanged' else []}

class GuardsTest(unittest.TestCase):
    def test_original_partial_and_completed_states(self):
        r=row()
        for ids in (['1','10','11','99'],['1','10','11','12','99'],['1','11','12','99'],['1','12','99']):check_member(r,ids,CONFIG)
        check_member(r,['1','12','99'],CONFIG,True)
    def test_drift_or_restriction_blocks(self):
        for ids in (['10','11','13'],['10','11','15'],['11'],['99']):
            with self.assertRaises(ValueError):check_member(row(),ids,CONFIG)
        with self.assertRaises(ValueError):check_member(row(status='unchanged'),['1','99'],CONFIG)
    def test_missing_returned_or_outside_registry_members_block(self):
        p={'rows':[row(),row('101','absent')]}
        good={'100':{'role_ids':['1','10','11'],'bot':False}}
        check_population(p,good,CONFIG)
        for members in ({},dict(good,**{'101':{'role_ids':['1'],'bot':False}}),dict(good,**{'102':{'role_ids':['12'],'bot':False}})):
            with self.assertRaises(ValueError):check_population(p,members,CONFIG)
    def test_final_validation_requires_all_present_players_placed(self):
        p={'rows':[row(),row('101','unchanged')]};members={uid:{'role_ids':['1','12'],'bot':False} for uid in ('100','101')}
        check_population(p,members,CONFIG,True)
        members['100']['role_ids'].append('10')
        with self.assertRaises(ValueError):check_population(p,members,CONFIG,True)
    def test_snapshot_digest_identity_and_scope(self):
        p={'rows':[row()],'guild_id':'1','mode':'preview_only','discord_changes_applied':False,'summary':{'review':0},'outside_registry_role_holders':[]}
        players=[{'discord_id':'100','name':'Player'}];expected=digest(p)
        validate_snapshot(p,CONFIG,players,expected)
        bad=copy.deepcopy(p);bad['rows'][0]['registry_name']='Changed'
        with self.assertRaises(ValueError):validate_snapshot(bad,CONFIG,players,expected)
        bad=copy.deepcopy(p);bad['rows'][0]['remove_role_ids'].append('99')
        with self.assertRaises(ValueError):validate_snapshot(bad,CONFIG,players,digest(bad))
        with self.assertRaises(ValueError):validate_snapshot(p,CONFIG,[],expected)

class RoleOperationsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ids={'1','10','11','99'};self.events=[];self.fail_add=False;self.uncertain=False;self.ignore_add=False
        self.roles={i:SimpleNamespace(id=int(i)) for i in ('10','11','12','15')}
    async def fetch(self,uid):
        async def add(role,**kw):
            self.events.append(('add',str(role.id),kw));
            if self.fail_add:raise RuntimeError('failed')
            if not self.ignore_add:self.ids.add(str(role.id))
            if self.uncertain:self.uncertain=False;raise TimeoutError('uncertain')
        async def remove(role,**kw):
            self.events.append(('remove',str(role.id),kw));self.ids.remove(str(role.id))
        return SimpleNamespace(id=uid,guild=SimpleNamespace(id=1),bot=False,roles=[SimpleNamespace(id=int(i)) for i in self.ids],add_roles=add,remove_roles=remove)
    async def apply(self):return await apply_member(row(),CONFIG,self.fetch,self.roles,lambda x:None)
    async def test_add_before_remove_preserving_unrelated(self):
        self.assertEqual(await self.apply(),(1,2));self.assertEqual(self.ids,{'1','12','99'})
        self.assertEqual(self.events[0][0],'add');self.assertTrue(all(e[2]['atomic'] for e in self.events))
    async def test_add_failure_never_removes_ranks(self):
        self.fail_add=True
        with self.assertRaises(RuntimeError):await self.apply()
        self.assertEqual(self.ids,{'1','10','11','99'});self.assertEqual(len(self.events),1)
    async def test_unobserved_add_never_removes_ranks(self):
        self.ignore_add=True
        with self.assertRaises(ValueError):await self.apply()
        self.assertEqual(len(self.events),1)
    async def test_uncertain_add_resumes_from_live_state(self):
        self.uncertain=True
        with self.assertRaises(TimeoutError):await self.apply()
        self.assertEqual(await self.apply(),(0,2))
        self.assertEqual(sum(e[0]=='add' for e in self.events),1)
    async def test_partial_removal_resumes_and_completed_is_noop(self):
        self.ids={'1','11','12','99'}
        self.assertEqual(await self.apply(),(0,1));self.assertEqual(await self.apply(),(0,0))
    async def test_new_restriction_stops_before_mutation(self):
        self.ids.add('13')
        with self.assertRaises(ValueError):await self.apply()
        self.assertEqual(self.events,[])

class CompletionTest(unittest.TestCase):
    def test_completion_marker_and_audit_are_atomic(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript('CREATE TABLE tables(id INTEGER); CREATE TABLE registry_import_manifest(id INTEGER PRIMARY KEY,role_transition_applied INTEGER); INSERT INTO registry_import_manifest VALUES(1,0);')
            with self.assertRaises(RuntimeError):
                with db:
                    db.execute('BEGIN IMMEDIATE');finish_database(db,'reviewed',{'present':1886});raise RuntimeError('before commit')
            self.assertEqual(db.execute('SELECT role_transition_applied FROM registry_import_manifest').fetchone()[0],0)
            with db:
                db.execute('BEGIN IMMEDIATE');finish_database(db,'reviewed',{'present':1886})
            self.assertEqual(db.execute('SELECT role_transition_applied FROM registry_import_manifest').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT plan_digest FROM s9_role_transition_audit').fetchone()[0],'reviewed')
    def test_tables_block_completion(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript('CREATE TABLE tables(id INTEGER); INSERT INTO tables VALUES(1);')
            with self.assertRaises(ValueError):finish_database(db,'reviewed',{})
