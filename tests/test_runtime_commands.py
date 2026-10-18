"""Build real Pycord commands but interrupt startup before any login/API call."""
import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
ROOT=Path(__file__).resolve().parents[1]
class StopBeforeLogin(Exception):pass

@unittest.skipUnless(importlib.util.find_spec('discord'),'Install requirements-v2-lab.txt to check real Pycord command construction')
class RuntimeCommandsTest(unittest.IsolatedAsyncioTestCase):
    async def construct(self,mode,exercise=None):
        import discord
        with patch.object(sys,'path',[str(ROOT/'scripts'),*sys.path]):
            from scripts.v2_lab import serve_lab
        bots=[];original=discord.Bot.__init__
        def capture(bot,*args,**kwargs):original(bot,*args,**kwargs);bots.append(bot)
        async def no_login(*args,**kwargs):raise StopBeforeLogin()
        with tempfile.TemporaryDirectory() as directory:
            runtime=None
            if mode=='production':
                from v2.runtime_config import load_runtime
                config=json.loads((ROOT/'settings/production.example.json').read_text())
                config.update(enabled=True,application_id='999',database_path=str(Path(directory)/'production.sqlite3'))
                config['roles'].update(reporter='2',placement='3',loungeless='4',chat_restricted='5',staff=['6'])
                config['roles']['ranks']={k:str(i) for i,k in enumerate(config['roles']['ranks'],10)}
                config['channels']={k:str(i) for i,k in enumerate(config['channels'],100)}
                config['tiers']={'tier-all':{'submission_channel_id':'200','results_channel_id':'201'},'tier-c':{'submission_channel_id':'202','results_channel_id':'203'},'tier-b':{'submission_channel_id':'204','results_channel_id':'205'},'tier-a':{'submission_channel_id':'206','results_channel_id':'207'}}
                path=Path(directory)/'config.json';path.write_text(json.dumps(config));runtime=load_runtime(path)
            with patch.dict(os.environ,{'V2_LAB_DB':str(Path(directory)/'lab.sqlite3'),'V2_LAB_MEMBER_CONFIG':''}),patch.object(discord.Bot,'__init__',capture),patch.object(discord.Bot,'start',no_login):
                with self.assertRaises(StopBeforeLogin):await serve_lab('NOT-A-REAL-TOKEN',runtime)
            self.built_bot=bots[0]
            if exercise:await exercise(bots[0],runtime)
            return {c.name for c in bots[0].pending_application_commands}

    async def test_registered_member_flag_request_and_staff_queue_approval(self):
        from v2.countries import player_countries
        async def exercise(bot,runtime):
            commands={c.name:c for c in bot.pending_application_commands}
            command=commands['flag']
            closure=dict(zip(command.callback.__code__.co_freevars,(c.cell_contents for c in command.callback.__closure__)))
            flags=closure['flag_requests'];store=flags.store
            actor=SimpleNamespace(id=123,roles=[],guild_permissions=SimpleNamespace(manage_guild=False))
            ctx=SimpleNamespace(author=actor,guild_id=runtime['guild_id'],channel_id=runtime['channels']['debug'],respond=AsyncMock(),defer=AsyncMock(),followup=SimpleNamespace(send=AsyncMock()))
            with patch.object(bot,'fetch_channel',new=AsyncMock(return_value=SimpleNamespace(send=AsyncMock()))):
                await command.callback(ctx,'Japan','Member context')
                self.assertIn('name/flag-request channel',ctx.respond.call_args.args[0])
                ctx.channel_id=runtime['channels']['name_requests']
                await command.callback(ctx,'Japan','Member context')
                self.assertIn('registered and verified',ctx.followup.send.call_args.args[0])
                with store.connect() as db:db.execute("INSERT INTO players(user_id,name,verified_by) VALUES (123,'Member',999)")
                actor.roles=[SimpleNamespace(id=runtime['roles']['chat_restricted'])]
                await command.callback(ctx,'Japan','Member context')
                self.assertIn('unrestricted',ctx.respond.call_args.args[0])
                actor.roles=[]
                await command.callback(ctx,'Japan','Member context')
                self.assertIn('recorded for staff review',ctx.followup.send.call_args.args[0])
                with store.connect() as db:
                    self.assertEqual(player_countries(db),{})
                    flag_id=db.execute('SELECT id FROM flag_requests').fetchone()[0]
                verify_id=store.request_verify(456,'https://mkcentral.com/registry/players/profile?id=456')
                actor.id=999;actor.guild_permissions.manage_guild=True;ctx.channel_id=runtime['channels']['debug']
                await commands['pending'].callback(ctx)
                queue=ctx.followup.send.call_args.kwargs['view']
                self.assertEqual({o.value for o in queue.select.options},{str(verify_id),f'flag:{flag_id}'})
                interaction=SimpleNamespace(user=actor,guild_id=runtime['guild_id'],response=SimpleNamespace(send_message=AsyncMock(),defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()),edit_original_response=AsyncMock())
                await queue.choose(interaction,f'flag:{flag_id}')
                view=interaction.followup.send.call_args.kwargs['view']
                self.assertIn('Japan (JP)',interaction.followup.send.call_args.args[0])
                actor.guild_permissions.manage_guild=False
                self.assertFalse(await view.interaction_check(interaction))
                actor.guild_permissions.manage_guild=True
                self.assertTrue(await view.interaction_check(interaction))
                await view.children[0].callback(interaction)
                await view.children[0].callback(interaction)
                with store.connect() as db:
                    self.assertEqual(player_countries(db),{123:'JP'})
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM player_country_audit').fetchone()[0],1)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM member_sync_jobs').fetchone()[0],0)
                    self.assertEqual(db.execute('SELECT status FROM requests WHERE id=?',(verify_id,)).fetchone()[0],'pending')
                queue.stop();view.stop()
                # A later staff correction invalidates an already-open flag preview.
                from v2.countries import set_country
                next_id=flags.request(123,'US')
                await queue.choose(interaction,f'flag:{next_id}')
                stale_view=interaction.followup.send.call_args.kwargs['view']
                set_country(store,123,'CA',100,'Concurrent staff correction')
                await stale_view.children[0].callback(interaction)
                self.assertFalse(stale_view.finished)
                self.assertTrue(stale_view.children[0].disabled)
                await stale_view.children[1].callback(interaction)
                with store.connect() as db:
                    self.assertEqual(player_countries(db),{123:'CA'})
                    self.assertEqual(db.execute('SELECT status FROM flag_requests WHERE id=?',(next_id,)).fetchone()[0],'rejected')
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM player_country_audit').fetchone()[0],2)
        await self.construct('production',exercise)

    async def test_empty_command_lists_omit_view_for_ephemeral_followups(self):
        async def exercise(bot,runtime):
            async def strict_send(*args,**kwargs):
                # Pycord treats an explicitly supplied view differently from omission.
                if 'view' in kwargs and kwargs['view'] is None:
                    raise AttributeError("'NoneType' object has no attribute 'timeout'")
            ctx=SimpleNamespace(author=SimpleNamespace(id=999,roles=[],guild_permissions=SimpleNamespace(manage_guild=True)),
                guild_id=runtime['guild_id'],channel_id=runtime['channels']['debug'],respond=AsyncMock(),
                defer=AsyncMock(),followup=SimpleNamespace(send=AsyncMock(side_effect=strict_send)))
            commands={c.name:c for c in bot.pending_application_commands}
            for name,args,expected in [('pending',(),'No pending requests.'),
                                       ('unstrike',(SimpleNamespace(id=123),'Reviewed request'),'no active season strikes'),
                                       ('corrections',(),'No outstanding correction')]:
                await commands[name].callback(ctx,*args)
                call=ctx.followup.send.call_args
                self.assertIn(expected,call.args[0]);self.assertTrue(call.kwargs['ephemeral'])
                self.assertNotIn('view',call.kwargs)
            ctx.respond.assert_not_awaited()
        await self.construct('production',exercise)

    async def test_pending_requests_still_offer_the_authorized_choice_view(self):
        async def exercise(bot,runtime):
            command=next(c for c in bot.pending_application_commands if c.name=='pending')
            closure=dict(zip(command.callback.__code__.co_freevars,(c.cell_contents for c in command.callback.__closure__)))
            request=closure['store'].request_verify(123,'https://mkcentral.com/registry/players/profile?id=123')
            ctx=SimpleNamespace(author=SimpleNamespace(id=999,roles=[],guild_permissions=SimpleNamespace(manage_guild=True)),
                guild_id=runtime['guild_id'],channel_id=runtime['channels']['debug'],respond=AsyncMock(),
                defer=AsyncMock(),followup=SimpleNamespace(send=AsyncMock()))
            await command.callback(ctx)
            view=ctx.followup.send.call_args.kwargs['view']
            self.assertEqual(view.actor_id,999);self.assertEqual(view.select.options[0].value,str(request))
            self.assertTrue(ctx.followup.send.call_args.kwargs['ephemeral'])
            view.stop()
        await self.construct('production',exercise)

    async def test_reduce_loss_reporter_confirmation_cancel_and_retry(self):
        from v2.lab_ratings import LabRatings
        from v2.member_sync import MemberSync
        async def exercise(bot,runtime):
            command=next(c for c in bot.pending_application_commands if c.name=='reduce_loss')
            closure=dict(zip(command.callback.__code__.co_freevars,(c.cell_contents for c in command.callback.__closure__)))
            store=closure['reductions'].store
            ratings=LabRatings(store)
            with store.connect() as db:
                for uid in range(1,13):
                    db.execute('INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES (?,?,?,999)',(uid,uid,f'P{uid}'))
                    db.execute("INSERT INTO season_ratings(season,user_id,mmr) VALUES ('S9',?,6000)",(uid,))
            table=store.submit_table(999,2,' '.join(f'P{i} {s}' for i,s in enumerate([100,100,90,90,85,85,80,80,70,70,67,67],1)))
            ratings.apply(table,999,ratings.preview(table,True)['digest'],True)
            actor=SimpleNamespace(id=999,roles=[],guild_permissions=SimpleNamespace(manage_guild=False))
            ctx=SimpleNamespace(author=actor,guild_id=runtime['guild_id'],channel_id=runtime['channels']['debug'],
                respond=AsyncMock(),defer=AsyncMock(),followup=SimpleNamespace(send=AsyncMock()))
            member=SimpleNamespace(id=12)
            await command.callback(ctx,member,table,'1/2','Disconnected')
            ctx.respond.assert_awaited_once();ctx.defer.assert_not_awaited()
            actor.roles=[SimpleNamespace(id=runtime['roles']['reporter'])]
            await command.callback(ctx,member,table,'1/2','Disconnected')
            view=ctx.followup.send.call_args.kwargs['view']
            interaction=SimpleNamespace(user=actor,guild_id=runtime['guild_id'],response=SimpleNamespace(send_message=AsyncMock(),defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()),edit_original_response=AsyncMock())
            actor.roles=[]
            self.assertFalse(await view.interaction_check(interaction))
            actor.roles=[SimpleNamespace(id=runtime['roles']['reporter'])]
            interaction.user=SimpleNamespace(id=998)
            self.assertFalse(await view.interaction_check(interaction))
            interaction.user=actor
            self.assertTrue(await view.interaction_check(interaction))
            await view.children[1].callback(interaction)  # Cancel
            self.assertEqual(ratings.player_statistics(12)['refunds'],0)
            await command.callback(ctx,member,table,'1/2','Disconnected')
            view=ctx.followup.send.call_args.kwargs['view']
            # Simulate an API role failure: durable work remains; credit cannot repeat.
            with patch.object(MemberSync,'drain',new=AsyncMock(return_value=[12])), patch.object(bot,'fetch_channel',new=AsyncMock(return_value=SimpleNamespace(send=AsyncMock()))):
                await view.children[0].callback(interaction)
                await view.children[0].callback(interaction)
            self.assertTrue(view.finished)
            self.assertEqual(ratings.player_statistics(12)['refunds'],100)
            with store.connect() as db:self.assertEqual(db.execute('SELECT pending FROM member_sync_jobs WHERE user_id=12').fetchone()[0],1)
            self.assertIn('synchronization pending',interaction.edit_original_response.call_args.kwargs['content'])
            # A transport/queue exception after commit must not report a failed refund.
            await command.callback(ctx,SimpleNamespace(id=11),table,'1/2','Disconnected too')
            view=ctx.followup.send.call_args.kwargs['view']
            with patch.object(MemberSync,'queue',side_effect=RuntimeError('transport failure')), patch.object(bot,'fetch_channel',new=AsyncMock(return_value=SimpleNamespace(send=AsyncMock()))):
                await view.children[0].callback(interaction)
            self.assertTrue(view.finished)
            self.assertEqual(ratings.player_statistics(11)['refunds'],100)
            self.assertIn('recorded once',interaction.edit_original_response.call_args.kwargs['content'])
            self.assertIn('already recorded',interaction.edit_original_response.call_args.kwargs['content'])
        await self.construct('production',exercise)

    async def test_sync_feedback_excludes_unrelated_jobs_and_detects_new_pending_work(self):
        from v2.member_sync import MemberSync
        async def exercise(bot,runtime):
            command=next(c for c in bot.pending_application_commands if c.name=='reduce_loss')
            closure=dict(zip(command.callback.__code__.co_freevars,(c.cell_contents for c in command.callback.__closure__)))
            store=closure['reductions'].store
            sync=closure['sync_members']
            with store.connect() as db:
                db.execute('INSERT INTO member_sync_jobs(user_id) VALUES (101),(102)')
            async def unrelated_failure():
                with store.connect() as db:db.execute('UPDATE member_sync_jobs SET pending=0 WHERE user_id=101')
                return [102]
            with patch.object(MemberSync,'drain',side_effect=unrelated_failure):
                self.assertEqual(await sync([101]),'Discord member roles/names synchronized.')
                fix=next(c for c in bot.pending_application_commands if c.name=='fix_roles')
                ctx=SimpleNamespace(author=SimpleNamespace(id=999,roles=[],guild_permissions=SimpleNamespace(manage_guild=True)),
                    guild_id=runtime['guild_id'],channel_id=runtime['channels']['debug'],respond=AsyncMock(),
                    defer=AsyncMock(),followup=SimpleNamespace(send=AsyncMock()))
                await fix.callback(ctx,SimpleNamespace(id=101))
                self.assertEqual(ctx.followup.send.call_args.args[0],'Discord member roles/names synchronized.')
            # A newer generation may remain pending even when this drain reported no errors.
            with patch.object(MemberSync,'drain',new=AsyncMock(return_value=[])):
                self.assertIn('1 requested member(s)',await sync([101]))
        await self.construct('production',exercise)

    async def test_country_requires_staff_and_records_source(self):
        async def exercise(bot,runtime):
            command=next(c for c in bot.pending_application_commands if c.name=='set_country')
            closure=dict(zip(command.callback.__code__.co_freevars,(c.cell_contents for c in command.callback.__closure__)))
            store=closure['store']
            with store.connect() as db:db.execute("INSERT INTO players(user_id,name,verified_by) VALUES (123,'Player',999)")
            actor=SimpleNamespace(id=999,roles=[SimpleNamespace(id=runtime['roles']['reporter'])],guild_permissions=SimpleNamespace(manage_guild=False))
            ctx=SimpleNamespace(author=actor,guild_id=runtime['guild_id'],channel_id=runtime['channels']['debug'],respond=AsyncMock(),defer=AsyncMock(),followup=SimpleNamespace(send=AsyncMock()))
            await command.callback(ctx,SimpleNamespace(id=123),'JP','Verified MKC profile')
            ctx.respond.assert_awaited_once();ctx.defer.assert_not_awaited()
            actor.roles=[SimpleNamespace(id=runtime['roles']['staff'][0])]
            await command.callback(ctx,SimpleNamespace(id=123),'JP','Verified MKC profile')
            with store.connect() as db:
                self.assertEqual(tuple(db.execute('SELECT country_code,actor_id,reason FROM player_countries').fetchone()),('JP',999,'Verified MKC profile'))
            await command.callback(ctx,SimpleNamespace(id=123),'ZZ','Invalid input')
            self.assertIn('ISO two-letter',ctx.followup.send.call_args.args[0])
        await self.construct('production',exercise)

    async def test_stats_attaches_mmr_graph_and_empty_filter_has_no_graph(self):
        from v2.lab_ratings import LabRatings
        async def exercise(bot,runtime):
            command=next(c for c in bot.pending_application_commands if c.name=='stats')
            closure=dict(zip(command.callback.__code__.co_freevars,(c.cell_contents for c in command.callback.__closure__)))
            store=closure['store'];ratings=LabRatings(store)
            with store.connect() as db:
                for uid in range(1,13):
                    db.execute('INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES (?,?,?,999)',(uid,uid,f'P{uid}'))
                    db.execute("INSERT INTO season_ratings(season,user_id,mmr) VALUES ('S9',?,6000)",(uid,))
            raw=' '.join(f'P{i} {score}' for i,score in enumerate([100,100,90,90,85,85,80,80,70,70,67,67],1))
            table=store.submit_table(999,2,raw)
            ratings.apply(table,999,ratings.preview(table,True)['digest'],True)
            actor=SimpleNamespace(id=12,roles=[],guild_permissions=SimpleNamespace(manage_guild=False))
            ctx=SimpleNamespace(author=actor,guild_id=runtime['guild_id'],channel_id=runtime['channels']['debug'],respond=AsyncMock(),defer=AsyncMock(),followup=SimpleNamespace(send=AsyncMock()))
            await command.callback(ctx)
            message=ctx.followup.send.call_args.kwargs
            self.assertEqual(message['embed'].image.url,'attachment://stats.png')
            self.assertEqual(message['file'].filename,'stats.png')
            self.assertTrue(message['file'].fp.read().startswith(b'\x89PNG'))
            message['file'].close()
            await command.callback(ctx,tier='c')
            self.assertNotIn('file',ctx.followup.send.call_args.kwargs)
            self.assertIn('No matching tables',[f.name for f in ctx.followup.send.call_args.kwargs['embed'].fields])
        await self.construct('production',exercise)

    async def test_stats_default_self_filters_empty_and_access(self):
        async def exercise(bot,runtime):
            command=next(c for c in bot.pending_application_commands if c.name=='stats')
            closure=dict(zip(command.callback.__code__.co_freevars,(c.cell_contents for c in command.callback.__closure__)))
            store=closure['store']
            with store.connect() as db:
                db.execute("INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES (123,123,'国際 ★',999)")
            actor=SimpleNamespace(id=123,roles=[],guild_permissions=SimpleNamespace(manage_guild=False))
            ctx=SimpleNamespace(author=actor,guild_id=runtime['guild_id'],channel_id=runtime['channels']['debug'],
                respond=AsyncMock(),defer=AsyncMock(),followup=SimpleNamespace(send=AsyncMock()))
            await command.callback(ctx)
            embed=ctx.followup.send.call_args.kwargs['embed']
            self.assertEqual(embed.title,'S9 Player Statistics')
            self.assertIn('No matching tables',[field.name for field in embed.fields])
            self.assertIn('Unplaced',embed.fields[0].value)
            await command.callback(ctx,player='国際 ★',tier='c',last=10)
            self.assertIn('tier-c',ctx.followup.send.call_args.kwargs['embed'].footer.text)
            await command.callback(ctx,last=0)
            self.assertIn('between 1 and 1000',ctx.followup.send.call_args.args[0])
            await command.callback(ctx,member=SimpleNamespace(id=123),player='国際 ★')
            self.assertIn('not both',ctx.followup.send.call_args.args[0])
            actor.roles=[SimpleNamespace(id=runtime['roles']['loungeless'])]
            await command.callback(ctx)
            ctx.respond.assert_awaited_once()
        await self.construct('production',exercise)
    async def test_lab_commands_construct_without_connection(self):
        commands=await self.construct('lab')
        self.assertTrue({'v2table','v2verify','v2unstrike','v2mmrdeduct','v2fixroles'}<=commands)
    async def test_production_command_names_construct_without_connection(self):
        commands=await self.construct('production')
        self.assertTrue({'table','verify','unstrike','mmr_penalty','reduce_loss','stats','flag','set_country','fix_roles','strikes','mmr'}<=commands)
        self.assertFalse(any(name.startswith('v2') for name in commands))
    async def test_real_member_options_and_new_ui_components(self):
        import discord
        await self.construct('lab')
        commands={c.name:c for c in self.built_bot.pending_application_commands}
        for name in ('v2strike','v2mmrdeduct','v2stats','v2reduceloss','v2unstrike','v2setname','v2fixroles','v2strikes'):
            option=next(o for o in commands[name].options if o.name=='member')
            self.assertEqual(option.input_type,discord.SlashCommandOptionType.user)
        formats=next(o for o in commands['v2table'].options if o.name=='format_size')
        self.assertEqual([c.value for c in formats.choices],[1,2,3,4])
        self.assertIn('Correct table',commands)
        self.assertIn('Report table issue',commands)
        from v2.review_ui import TextModal,ChoiceView,ActionView
        async def noop(*args):pass
        modal=TextModal('Scores',[dict(label='Scores',value='Player 82',style=discord.InputTextStyle.long,max_length=1000)],lambda i:True,noop)
        self.assertEqual(len(modal.children),1)
        self.assertEqual(len(ChoiceView(1,lambda i:True,[discord.SelectOption(label='Review',value='1')],noop).children),1)
        self.assertEqual(len(ActionView('Enter scores',lambda i:True,noop).children),1)
