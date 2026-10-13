"""Exercise UI callback logic using a tiny Discord transport double.

These are callback tests, not a live Discord or py-cord compatibility test.
"""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import sys
import tempfile
import unittest
from v2.lab_store import LabStore
from v2.lab_ratings import LabRatings

class ViewDouble:
    def __init__(self,timeout=None):self.children=[];self.timeout=timeout
    def stop(self):pass

def button(**kwargs):return lambda f:f
fake_discord=SimpleNamespace(ui=SimpleNamespace(View=ViewDouble,button=button),
    ButtonStyle=SimpleNamespace(green=1,red=2),File=lambda data,filename:SimpleNamespace(data=data,filename=filename),HTTPException=RuntimeError)
spec=importlib.util.spec_from_file_location('button_transport_test',Path(__file__).resolve().parents[1]/'v2/table_buttons.py')
module=importlib.util.module_from_spec(spec)
with patch.dict(sys.modules,{'discord':fake_discord}):spec.loader.exec_module(module)

class ButtonCallbackTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        renderer_patch=patch.object(module,'render_results',module.render_results_local if hasattr(module,'render_results_local') else __import__('v2.table_image',fromlist=['render_results_local']).render_results_local)
        renderer_patch.start();self.addCleanup(renderer_patch.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=LabStore(Path(self.temp.name)/'lab.sqlite3')
        with self.store.connect() as db:
            for i in range(1,13):db.execute('INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES (?,?,?,-1)',(-100000-i,-200000-i,f'LAB{i:02d}'))
        self.ratings=LabRatings(self.store)
        self.raw=' '.join(f'LAB{i:02d} 82' for i in range(1,13))
        self.table=self.store.submit_table(111,3,self.raw)
        self.view=module.TableButtons(self.store,self.ratings,self.table,111,self.ratings.preview(self.table,True)['digest'],lambda i:True)
        self.interaction=SimpleNamespace(user=SimpleNamespace(id=111),response=SimpleNamespace(defer=AsyncMock(),send_message=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()),edit_original_response=AsyncMock())
    async def test_other_user_or_revoked_permission_denied(self):
        self.interaction.user.id=222
        self.assertFalse(await self.view.interaction_check(self.interaction))
        self.interaction.user.id=111;self.view.authorized=lambda i:False
        self.assertFalse(await self.view.interaction_check(self.interaction))
    async def test_yes_once_with_immediate_ack(self):
        self.assertTrue(await self.view.interaction_check(self.interaction))
        await self.view.yes(None,self.interaction)
        self.interaction.response.defer.assert_awaited_once()
        self.assertTrue(self.view.finished)
        await self.view.yes(None,self.interaction)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM rating_applications').fetchone()[0],1)
    async def test_no_and_timeout_leave_ratings_untouched(self):
        await self.view.no(None,self.interaction)
        self.assertTrue(all(r['mmr'] is None for r in self.ratings.ratings()[1]))
        t=self.store.submit_table(111,3,self.raw)
        view=module.TableButtons(self.store,self.ratings,t,111,self.ratings.preview(t,True)['digest'],lambda i:True)
        view.message=SimpleNamespace(edit=AsyncMock())
        await view.on_timeout()
        self.assertTrue(view.finished)
        view.message.edit.assert_awaited_once()
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM rating_applications').fetchone()[0],0)
    async def test_stale_preview_refreshes_and_requires_another_yes(self):
        other=self.store.submit_table(111,3,self.raw.replace('LAB01 82 LAB02 82','LAB01 83 LAB02 81'))
        self.ratings.apply(other,111,self.ratings.preview(other,True)['digest'],True)
        old=self.view.digest
        await self.view.yes(None,self.interaction)
        self.assertNotEqual(old,self.view.digest);self.assertFalse(self.view.finished)
        refresh=self.interaction.edit_original_response.call_args.kwargs
        self.assertEqual([f.filename for f in refresh['files']],['results.png'])
        self.assertEqual(refresh['attachments'],[])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM rating_applications').fetchone()[0],1)
        await self.view.yes(None,self.interaction)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM rating_applications').fetchone()[0],2)
    async def test_disputed_preview_cannot_apply_from_button(self):
        self.store.flag_table(self.table,333,'Check this result')
        await self.view.yes(None,self.interaction)
        self.interaction.followup.send.assert_awaited_once()
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM rating_applications').fetchone()[0],0)

    async def test_publication_failure_does_not_reapply_ratings(self):
        self.view.publish=AsyncMock(side_effect=RuntimeError('delivery failed'))
        with self.assertLogs(level='ERROR'):
            await self.view.yes(None,self.interaction)
        self.assertTrue(self.view.finished)
        self.assertIn('delivery is pending',self.interaction.edit_original_response.call_args.kwargs['content'])
        await self.view.yes(None,self.interaction)
        self.view.publish.assert_awaited_once_with(self.table)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM rating_applications').fetchone()[0],1)
