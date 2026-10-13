"""Publication transport doubles; real Discord delivery is a separate lab check."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import tempfile
import unittest
from v2.lab_store import LabStore,LabError
from v2.lab_ratings import LabRatings

class Embed:
    def __init__(self,**kwargs):self.footer=SimpleNamespace(text=None);self.fields=[]
    def add_field(self,**kwargs):self.fields.append(kwargs)
    def set_footer(self,**kwargs):self.footer=SimpleNamespace(**kwargs)
    def set_image(self,**kwargs):self.image=kwargs
class HTTPError(Exception):
    def __init__(self,status):self.status=status
class TextChannel:
    def __init__(self):
        self.id=333;self.guild=SimpleNamespace(id=111,me=object());self.send=AsyncMock();self.messages=[]
    def permissions_for(self,member):return SimpleNamespace(**dict.fromkeys(['view_channel','send_messages','attach_files','embed_links','read_message_history'],True))
    async def history(self,**kwargs):
        for message in self.messages:yield message
fake=SimpleNamespace(TextChannel=TextChannel,Embed=Embed,Color=SimpleNamespace(blurple=lambda:1),
                     AllowedMentions=SimpleNamespace(none=lambda:None),HTTPException=HTTPError,
                     File=lambda data,filename:SimpleNamespace(filename=filename,data=data))
spec=importlib.util.spec_from_file_location('publication_transport_test',Path(__file__).resolve().parents[1]/'v2/table_publication.py')
module=importlib.util.module_from_spec(spec)
with patch.dict(sys.modules,{'discord':fake}):spec.loader.exec_module(module)

class PublicationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=LabStore(Path(self.temp.name)/'lab.sqlite3')
        with self.store.connect() as db:
            for i in range(1,13):db.execute('INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES (?,?,?,-1)',(-100000-i,-200000-i,f'LAB{i:02d}'))
        self.ratings=LabRatings(self.store);self.raw=' '.join(f'LAB{i:02d} 82' for i in range(1,13))
        self.channel=TextChannel();self.bot=SimpleNamespace(get_channel=lambda _:self.channel,user=SimpleNamespace(id=999))
        self.publisher=module.TablePublisher(self.store,self.bot,111,{'tier-all':333})
        self.table=self.store.submit_table(222,3,self.raw)
        await self.publisher.prepare(self.table,'tier-all')
        for name in ('render_results','render_table'):
            patcher=patch.object(module,name,return_value=b'png');patcher.start();self.addCleanup(patcher.stop)
    def apply(self):self.ratings.apply(self.table,222,self.ratings.preview(self.table,True)['digest'],True)
    def application_count(self):
        with self.store.connect() as db:return db.execute('SELECT COUNT(*) FROM rating_applications').fetchone()[0]
    async def test_applied_two_embeds_retry_does_not_duplicate_or_apply(self):
        self.apply();self.channel.send.side_effect=[SimpleNamespace(id=1),SimpleNamespace(id=2)]
        await self.publisher.publish(self.table);await self.publisher.publish(self.table)
        self.assertEqual(self.channel.send.await_count,2);self.assertEqual(self.application_count(),1)
        self.assertEqual([c.kwargs['file'].filename for c in self.channel.send.call_args_list],['results.png','mmr.png'])
        self.assertEqual(self.publisher.pending(),[])
        self.assertEqual(module.render_table.call_args.args[-1][0]['before'],None)
        self.assertEqual(module.render_table.call_args.args[-1][0]['after'],3750)
    async def test_international_publication_keeps_case_distinct_players(self):
        import json
        names=['Will','will','louis shoggs','やまもと','👩🏽\u200d💻']+[f'Player{i}' for i in range(7)]
        with self.store.connect() as db:
            for i,name in enumerate(names,1):
                db.execute('UPDATE players SET name=? WHERE user_id=?',(name,-100000-i))
        self.store.cancel_table(self.table,222)
        self.raw=json.dumps([[n,82] for n in names],ensure_ascii=False)
        self.table=self.store.submit_table(222,3,self.raw)
        await self.publisher.prepare(self.table,'tier-all')
        self.apply();self.channel.send.side_effect=[SimpleNamespace(id=1),SimpleNamespace(id=2)]
        await self.publisher.publish(self.table)
        self.assertEqual([r['name'] for r in module.render_table.call_args.args[-1]],names)
        self.assertEqual(self.channel.send.await_count,2)

    async def test_partial_failure_retries_only_missing_embed(self):
        self.apply();self.channel.send.side_effect=[SimpleNamespace(id=1),HTTPError(403),SimpleNamespace(id=2)]
        with self.assertRaises(HTTPError):await self.publisher.publish(self.table)
        self.assertEqual(self.publisher.pending(),[self.table])
        await self.publisher.publish(self.table)
        self.assertEqual([c.kwargs['file'].filename for c in self.channel.send.call_args_list],['results.png','mmr.png','mmr.png'])
        self.assertEqual(self.application_count(),1)
    async def test_unapplied_and_canceled_tables_never_publish(self):
        self.assertEqual(self.publisher.pending(),[])
        with self.assertRaises(LabError):await self.publisher.publish(self.table)
        self.store.cancel_table(self.table,222)
        with self.assertRaises(LabError):await self.publisher.publish(self.table)
        self.channel.send.assert_not_awaited()
    async def test_wrong_guild_unconfigured_and_changed_destination_block(self):
        self.channel.guild.id=444
        with self.assertRaises(LabError):await self.publisher.destination('tier-all')
        with self.assertRaises(LabError):await self.publisher.destination('tier-c')
        self.channel.guild.id=111;self.apply();self.channel.id=555
        with self.assertRaises(LabError):await self.publisher.publish(self.table)
        self.channel.send.assert_not_awaited()
    async def test_uncertain_send_recovered_by_marker_or_blocks_resend(self):
        self.apply();self.channel.send.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):await self.publisher.publish(self.table)
        with self.assertRaises(LabError):await self.publisher.publish(self.table)
        self.assertEqual(self.channel.send.await_count,1)
        embed=self.channel.send.call_args.kwargs['embed']
        self.channel.messages=[SimpleNamespace(id=1,author=SimpleNamespace(id=999),embeds=[embed])]
        self.channel.send.side_effect=None;self.channel.send.return_value=SimpleNamespace(id=2)
        await self.publisher.publish(self.table)
        self.assertEqual(self.channel.send.await_count,2);self.assertEqual(self.application_count(),1)
    def reverse(self):
        request=self.ratings.request_reversal(self.table,222,'Wrong scores','https://discord.com/channels/1/2/3')
        self.ratings.approve_reversal(request,444,self.ratings.reversal_preview(request)['digest'])
    async def published_then_reverse(self):
        self.apply();self.channel.send.side_effect=[SimpleNamespace(id=1),SimpleNamespace(id=2)]
        await self.publisher.publish(self.table);self.reverse()
        self.posts={i:SimpleNamespace(id=i,author=SimpleNamespace(id=999),delete=AsyncMock()) for i in (1,2)}
        self.channel.fetch_message=AsyncMock(side_effect=lambda i:self.posts[i])
    async def test_retraction_deletes_only_owned_posts_once_preserves_ledger(self):
        await self.published_then_reverse()
        self.assertEqual(self.publisher.pending_retractions(),[self.table])
        await self.publisher.retract(self.table);await self.publisher.retract(self.table)
        for post in self.posts.values():post.delete.assert_awaited_once()
        self.assertEqual(self.publisher.pending_retractions(),[])
        self.assertEqual(self.application_count(),1)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM rating_events').fetchone()[0],12)
    async def test_retraction_partial_failure_and_already_deleted_recover(self):
        await self.published_then_reverse();self.posts[2].delete.side_effect=[HTTPError(403),HTTPError(404)]
        with self.assertRaises(HTTPError):await self.publisher.retract(self.table)
        await self.publisher.retract(self.table)
        self.posts[1].delete.assert_awaited_once();self.assertEqual(self.posts[2].delete.await_count,2)
        self.assertEqual(self.publisher.pending_retractions(),[])
    async def test_retraction_rejects_unreversed_and_other_author(self):
        with self.assertRaises(LabError):await self.publisher.retract(self.table)
        await self.published_then_reverse();self.posts[1].author.id=123
        with self.assertRaises(LabError):await self.publisher.retract(self.table)
        self.posts[1].delete.assert_not_awaited()
    async def test_replacement_retracts_original_before_publishing_two_new_embeds(self):
        await self.published_then_reverse()
        raw='LAB01 83 LAB02 81 '+' '.join(f'LAB{i:02d} 82' for i in range(3,13))
        table=self.store.submit_table(222,3,raw,evidence='https://discord.com/channels/1/2/3',replaces_table_id=self.table)
        self.store.review_table(table,444,True)
        self.ratings.apply(table,444,self.ratings.preview(table)['digest'])
        await self.publisher.prepare(table,'tier-all')
        self.channel.send.side_effect=[SimpleNamespace(id=3),SimpleNamespace(id=4)]
        await self.publisher.publish(table)
        for post in self.posts.values():post.delete.assert_awaited_once()
        self.assertEqual(self.channel.send.await_count,4)
        self.assertTrue(any(field['name']=='Replaces' for field in self.channel.send.call_args.kwargs['embed'].fields))
        self.assertEqual(self.application_count(),2)
