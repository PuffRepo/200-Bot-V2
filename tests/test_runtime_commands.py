"""Build real Pycord commands but interrupt startup before any login/API call."""
import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
class StopBeforeLogin(Exception):pass

@unittest.skipUnless(importlib.util.find_spec('discord'),'Install requirements-v2-lab.txt to check real Pycord command construction')
class RuntimeCommandsTest(unittest.IsolatedAsyncioTestCase):
    async def construct(self,mode):
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
            return {c.name for c in bots[0].pending_application_commands}
    async def test_lab_commands_construct_without_connection(self):
        commands=await self.construct('lab')
        self.assertTrue({'v2table','v2verify','v2unstrike','v2mmrdeduct','v2fixroles'}<=commands)
    async def test_production_command_names_construct_without_connection(self):
        commands=await self.construct('production')
        self.assertTrue({'table','verify','unstrike','mmr_penalty','fix_roles','strikes','mmr'}<=commands)
        self.assertFalse(any(name.startswith('v2') for name in commands))
    async def test_real_member_options_and_new_ui_components(self):
        import discord
        await self.construct('lab')
        commands={c.name:c for c in self.built_bot.pending_application_commands}
        for name in ('v2strike','v2mmrdeduct','v2unstrike','v2setname','v2fixroles','v2strikes'):
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
