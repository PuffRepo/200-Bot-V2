"""UI transport doubles; real Pycord construction is checked separately."""
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from v2.lab_store import LabError
class View:
    def __init__(self,**kwargs):self.children=[]
    def stop(self):pass
    def add_item(self,item):self.children.append(item)
class Item:
    def __init__(self,**kwargs):self.__dict__.update(kwargs);self.values=[]
fake=SimpleNamespace(ui=SimpleNamespace(View=View,button=lambda **kw:lambda f:f,Button=Item,Select=Item,Modal=View,InputText=Item),ButtonStyle=SimpleNamespace(green=1,red=2,primary=3),HTTPException=RuntimeError)
spec=importlib.util.spec_from_file_location('review_ui_transport_test',Path(__file__).resolve().parents[1]/'v2/review_ui.py')
module=importlib.util.module_from_spec(spec)
with patch.dict(sys.modules,{'discord':fake}):spec.loader.exec_module(module)

class CorrectionButtonsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.preview={'digest':'first','content':'Before → after'}
        self.approve=AsyncMock(return_value='Applied once');self.reject=AsyncMock(return_value='Rejected')
        self.refresh=AsyncMock(return_value=self.preview)
        self.view=module.ReviewButtons(222,lambda i:True,self.preview,self.refresh,self.approve,self.reject)
        self.i=SimpleNamespace(user=SimpleNamespace(id=222),response=SimpleNamespace(send_message=AsyncMock(),defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()),edit_original_response=AsyncMock())
    async def test_reviewer_binding_and_revoked_staff_denied(self):
        self.i.user.id=111;self.assertFalse(await self.view.interaction_check(self.i))
        self.i.user.id=222;self.view.authorized=lambda i:False
        self.assertFalse(await self.view.interaction_check(self.i));self.approve.assert_not_awaited()
    async def test_approve_acknowledged_once_and_repeat_is_noop(self):
        await self.view.approve_button(None,self.i);await self.view.approve_button(None,self.i)
        self.approve.assert_awaited_once_with(self.i,self.preview);self.assertTrue(self.view.finished)
        self.assertEqual(self.i.response.defer.await_count,2)
    async def test_stale_refresh_requires_second_approval(self):
        self.approve.side_effect=[LabError('Stale'),'Applied once']
        self.refresh.return_value={'digest':'new','content':'Changed balances'}
        await self.view.approve_button(None,self.i)
        self.assertFalse(self.view.finished);self.assertEqual(self.view.preview['digest'],'new')
        self.assertIn('Nothing applied',self.i.edit_original_response.call_args.kwargs['content'])
        await self.view.approve_button(None,self.i);self.assertTrue(self.view.finished)
        self.assertEqual(self.approve.call_args.args[1]['digest'],'new')
    async def test_independence_failure_does_not_refresh_into_autoapproval(self):
        self.approve.side_effect=LabError('Independent reviewer required')
        await self.view.approve_button(None,self.i)
        self.assertFalse(self.view.finished);self.approve.assert_awaited_once()
        self.assertIn('Independent',self.i.followup.send.call_args.args[0])
    async def test_reject_and_timeout_never_approve(self):
        await self.view.reject_button(None,self.i);self.reject.assert_awaited_once();self.approve.assert_not_awaited()
        view=module.ReviewButtons(222,lambda i:True,self.preview,self.refresh,self.approve,self.reject)
        view.message=SimpleNamespace(edit=AsyncMock());await view.on_timeout()
        self.assertTrue(view.finished);self.approve.assert_not_awaited()
    async def test_picker_binds_actor_and_checks_current_authority(self):
        choose=AsyncMock();view=module.ChoiceView(222,lambda i:True,[Item(value='17')],choose)
        view.select.values=['17'];self.i.user.id=111;await view.selected(self.i);choose.assert_not_awaited()
        self.i.user.id=222;await view.selected(self.i);choose.assert_awaited_once_with(self.i,'17')
    async def test_modal_checks_authority_before_mutation(self):
        submit=AsyncMock();modal=module.TextModal('Reason',[{'value':'Correction'}],lambda i:False,submit)
        await modal.callback(self.i);submit.assert_not_awaited()
        modal.authorized=lambda i:True;await modal.callback(self.i);submit.assert_awaited_once_with(self.i,['Correction'])

class MessageSourceTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from v2.lab_store import LabStore
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=LabStore(Path(self.temp.name)/'lab.sqlite3')
        with self.store.connect() as db:
            db.execute('CREATE TABLE table_publications(table_id INTEGER,channel_id INTEGER,results_id INTEGER,mmr_id INTEGER)')
            db.execute('CREATE TABLE table_reversal_requests(table_id INTEGER,evidence TEXT,id INTEGER)')
            db.execute('INSERT INTO table_publications VALUES (31,333,555,556)')
    def test_original_evidence_uses_recorded_result_without_user_link(self):
        self.assertEqual(module.original_evidence(self.store,111,31),'https://discord.com/channels/111/333/555')
        with self.store.connect() as db:db.execute('UPDATE table_publications SET results_id=NULL')
        self.assertEqual(module.original_evidence(self.store,111,31),'https://discord.com/channels/111/333/556')
    def test_missing_link_requires_explicit_evidence_never_guesses(self):
        with self.assertRaises(LabError):module.original_evidence(self.store,111,999)
        self.assertEqual(module.original_evidence(self.store,111,999,'https://discord.com/channels/1/2/3'),'https://discord.com/channels/1/2/3')
    def test_context_source_rejects_wrong_guild_channel_author_or_message(self):
        args=[self.store,111,111,333,555,777,777,{'tier-all':333}]
        self.assertEqual(module.recorded_result(*args),31)
        for index,value in ((2,222),(3,444),(4,999),(5,888)):
            changed=list(args);changed[index]=value
            with self.assertRaises(LabError):module.recorded_result(*changed)
