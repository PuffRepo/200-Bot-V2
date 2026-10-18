import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from v2.lab_store import LabStore,LabError
from v2.lab_ratings import LabRatings
from v2.moderation import Moderation
from v2.member_sync import MemberSync

class Role:
    def __init__(self,rid,position):self.id=rid;self.position=position;self.managed=False
    def __ge__(self,other):return self.position>=other.position
    def is_default(self):return self.id==1
class Member:
    def __init__(self,guild,roles):self.id=101;self.guild=guild;self.roles=roles;self.nick=None;self.calls=[];self.fail=False
    @property
    def top_role(self):return max(self.roles,key=lambda r:r.position)
    async def edit(self,**kwargs):self.nick=kwargs['nick'];self.calls.append('nick')
    async def add_roles(self,role,**kwargs):
        if self.fail:raise RuntimeError('role failed')
        self.roles.append(role);self.calls.append(('add',role.id))
    async def remove_roles(self,role,**kwargs):self.roles.remove(role);self.calls.append(('remove',role.id))
class MemberSyncTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=LabStore(Path(self.temp.name)/'lab.sqlite3');self.ratings=LabRatings(self.store);self.mod=Moderation(self.store)
        with self.store.connect() as db:
            db.execute("INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES (101,101,'Player101',999)")
            db.execute("INSERT INTO season_ratings(season,user_id,mmr,revision) VALUES ('LAB-INITIAL',101,7500,0)")
        self.roles={i:Role(i,2) for i in range(2,30)};self.roles[1]=Role(1,0);self.roles[30]=Role(30,100)
        self.guild=SimpleNamespace(id=555,owner_id=999,me=SimpleNamespace(top_role=self.roles[30],guild_permissions=SimpleNamespace(manage_roles=True,manage_nicknames=True)),get_role=lambda rid:self.roles.get(rid))
        self.member=Member(self.guild,[self.roles[1],self.roles[2],self.roles[29]])
        async def fetch(uid):return self.member
        self.guild.fetch_member=fetch;self.bot=SimpleNamespace(get_guild=lambda gid:self.guild if gid==555 else None)
        self.config={'guild_id':555,'roles':{'placement':2,'loungeless':3,'chat_restricted':4,'verified_optional':5,'reporter':6,'staff':[7],'ranks':dict(zip(['Iron','Bronze','Silver','Gold','Platinum','Emerald','Ruby','Diamond','Master','Grandmaster'],range(10,20)))}}
        self.sync=MemberSync(self.store,self.ratings,self.mod,self.bot,self.config)
    async def test_rank_and_nickname_preserve_unrelated_roles(self):
        await self.sync.preflight();self.sync.queue([101,-100001]);self.assertEqual(await self.sync.drain(),[])
        self.assertEqual(self.member.nick,'Player101');self.assertEqual({r.id for r in self.member.roles},{1,5,15,29})
        calls=list(self.member.calls);await self.sync.sync(101);self.assertEqual(self.member.calls,calls)
    async def test_role_failure_persists_for_retry_without_rating_change(self):
        self.member.fail=True;self.sync.queue([101]);self.assertEqual(await self.sync.drain(),[101])
        self.member.fail=False;self.assertEqual(await self.sync.drain(),[])
        self.assertEqual(next(r for r in self.ratings.ratings()[1] if r['user_id']==101)['mmr'],7500)
    async def test_loss_credit_crosses_rank_boundary_and_retries_without_second_credit(self):
        from v2.loss_reductions import LossReductions
        loss=LossReductions(self.store)
        with self.store.connect() as db:
            db.execute("INSERT INTO lab_seasons(name,started_by) VALUES ('S9',999)")
            db.execute("UPDATE lab_settings SET value='S9' WHERE key='current_season'")
            for uid in range(101,113):
                if uid!=101:db.execute('INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES (?,?,?,999)',(uid,uid,f'Player{uid}'))
                db.execute("INSERT INTO season_ratings(season,user_id,mmr) VALUES ('S9',?,7500)",(uid,))
        scores=[67,67,70,70,80,80,85,85,90,90,100,100]
        table=self.store.submit_table(999,2,' '.join(f'Player{i} {s}' for i,s in zip(range(101,113),scores)))
        self.ratings.apply(table,999,self.ratings.preview(table,True)['digest'],True)
        await self.sync.sync(101)
        self.assertNotIn(15,{r.id for r in self.member.roles})
        preview=loss.preview(table,101,999,'0','Missed the table')
        loss.apply(table,101,999,'0','Missed the table',preview['digest'])
        self.member.fail=True
        self.assertEqual(await self.sync.drain(),[101])
        self.member.fail=False
        self.assertEqual(await self.sync.drain(),[])
        self.assertIn(15,{r.id for r in self.member.roles})
        self.assertEqual(self.ratings.player_statistics(101)['refunds'],200)
    async def test_restriction_removes_rank_then_expiry_restores_it(self):
        await self.sync.sync(101)
        for i in range(3):self.mod.strike(999,101,0,'Test',f's{i}')
        await self.sync.sync(101)
        self.assertIn(3,{r.id for r in self.member.roles});self.assertNotIn(15,{r.id for r in self.member.roles})
        with self.store.connect() as db:db.execute("UPDATE member_restrictions SET until_at=datetime('now','-1 second')")
        await self.sync.sync(101)
        self.assertNotIn(3,{r.id for r in self.member.roles});self.assertIn(15,{r.id for r in self.member.roles})
    async def test_wrong_guild_and_unmanageable_roles_block(self):
        self.config['guild_id']=888
        with self.assertRaises(LabError):await self.sync.sync(101)
        self.config['guild_id']=555;self.roles[15].position=100
        with self.assertRaises(LabError):await self.sync.preflight()
    async def test_owner_nickname_reports_pending_after_rank_update(self):
        self.guild.owner_id=101;self.sync.queue([101])
        self.assertEqual(await self.sync.drain(),[101]);self.assertIsNone(self.member.nick)
        self.assertIn(15,{r.id for r in self.member.roles})
    async def test_refresh_only_due_restrictions_and_no_repeated_expiry_jobs(self):
        for i in range(3):self.mod.strike(999,101,0,'Test',f'expire{i}')
        await self.sync.sync(101);self.member.calls.clear()
        await self.sync.refresh_restrictions();self.assertEqual(self.member.calls,[])
        with self.store.connect() as db:db.execute("UPDATE member_restrictions SET until_at=datetime('now','-1 second')")
        await self.sync.refresh_restrictions();self.assertIn(15,{r.id for r in self.member.roles})
        self.member.calls.clear();await self.sync.refresh_restrictions();self.assertEqual(self.member.calls,[])
    async def test_new_queue_during_network_write_is_not_lost(self):
        original_edit=self.member.edit
        async def edit_and_requeue(**kwargs):
            await original_edit(**kwargs)
            with self.store.connect() as db:db.execute("UPDATE season_ratings SET mmr=9000,revision=revision+1 WHERE user_id=101")
            self.sync.queue([101])
        self.member.edit=edit_and_requeue;self.sync.queue([101]);await self.sync.drain()
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT pending FROM member_sync_jobs WHERE user_id=101').fetchone()[0],1)
        await self.sync.drain();self.assertIn(16,{r.id for r in self.member.roles});self.assertNotIn(15,{r.id for r in self.member.roles})
