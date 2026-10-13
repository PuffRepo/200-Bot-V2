import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from scripts.lift_s9_restrictions import clear_member,GUILD,ROLE_IDS,APPROVED_IDS

class RestrictionResetTest(unittest.IsolatedAsyncioTestCase):
    def member(self,uid=None,roles=None):
        return SimpleNamespace(id=uid or min(APPROVED_IDS),guild=SimpleNamespace(id=GUILD),bot=False,
                               roles=[SimpleNamespace(id=i) for i in (roles if roles is not None else [*ROLE_IDS,99,100])],remove_roles=AsyncMock())
    def roles(self):return {i:SimpleNamespace(id=i) for i in ROLE_IDS}
    async def test_apply_removes_only_approved_roles_atomically(self):
        m=self.member();log=[]
        result=await clear_member(m,self.roles(),log.append,True)
        self.assertEqual(set(result),ROLE_IDS)
        self.assertEqual({c.args[0].id for c in m.remove_roles.call_args_list},ROLE_IDS)
        self.assertTrue(all(c.kwargs['atomic'] for c in m.remove_roles.call_args_list))
        self.assertEqual(log[0]['role_ids'],[str(i) for i in sorted([*ROLE_IDS,99,100])])
    async def test_preview_never_writes_roles(self):
        m=self.member();await clear_member(m,self.roles(),lambda _:None,False);m.remove_roles.assert_not_awaited()
    async def test_rerun_with_roles_already_absent_is_noop(self):
        m=self.member(roles=[99]);self.assertEqual(await clear_member(m,self.roles(),lambda _:None,True),[]);m.remove_roles.assert_not_awaited()
    async def test_unapproved_account_guild_or_bot_rejected(self):
        for change in ('id','guild','bot'):
            m=self.member()
            setattr(m,change,1 if change=='id' else SimpleNamespace(id=1) if change=='guild' else True)
            with self.assertRaises(ValueError):await clear_member(m,self.roles(),lambda _:None,True)
            m.remove_roles.assert_not_awaited()
    async def test_failure_stops_and_audit_precedes_api(self):
        m=self.member();log=[];m.remove_roles.side_effect=RuntimeError('network')
        with self.assertRaises(RuntimeError):await clear_member(m,self.roles(),log.append,True)
        self.assertEqual(m.remove_roles.await_count,1);self.assertEqual(log[-1]['event'],'remove_requested')
    async def test_unexpected_role_scope_rejected(self):
        m=self.member()
        with self.assertRaises(ValueError):await clear_member(m,{99:SimpleNamespace(id=99)},lambda _:None,True)
        m.remove_roles.assert_not_awaited()
