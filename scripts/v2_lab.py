"""Run lab-only V2 commands, even if the archive bot remains in the live guild."""

import asyncio
import getpass
from io import BytesIO
import os
import sys
from contextlib import suppress
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import discord

from discord_smoke import APPLICATION_ID, DEBUG_CHANNEL_ID, LAB_GUILD_ID
from v2.lab_store import LabError, LabStore
from v2.rating_preview import preview_fixture_table
from v2.lab_ratings import LabRatings
from v2.lab_access import can_report
from v2.table_buttons import TableButtons
from v2.review_ui import ReviewButtons,ActionView,ChoiceView,TextModal,original_evidence,recorded_result
from types import SimpleNamespace
from v2.table_publication import TablePublisher
from v2.moderation import Moderation
from v2.loss_reductions import LossReductions
from v2.statistics import snapshot as statistics_snapshot, player_statistics
from v2.member_sync import MemberSync
from v2.runtime_config import load_runtime
from v2.lab_store import player_name
from v2.table_image import render_table, render_results, results_date

# The archive bot may still be installed in this one known live guild.
# Commands remain registered only in LAB_GUILD_ID and gated to #bot-debug.
LIVE_ARCHIVE_GUILD_ID = 461383953937596416


def valid_lab_guilds(guild_ids: set[int]) -> bool:
    return LAB_GUILD_ID in guild_ids and guild_ids <= {LAB_GUILD_ID, LIVE_ARCHIVE_GUILD_ID}


async def serve_lab(token: str, runtime=None) -> int:
    if runtime is None and os.environ.get('V2_LAB_MEMBER_CONFIG'):
        runtime=load_runtime(os.environ['V2_LAB_MEMBER_CONFIG'],'lab')
    production=bool(runtime and runtime['mode']=='production')
    LAB_GUILD_ID=runtime['guild_id'] if runtime else globals()['LAB_GUILD_ID']
    APPLICATION_ID=runtime['application_id'] if runtime else globals()['APPLICATION_ID']
    DEBUG_CHANNEL_ID=runtime['channels']['debug'] if runtime else globals()['DEBUG_CHANNEL_ID']
    database=Path(runtime['database_path']) if runtime else Path(os.environ.get('V2_LAB_DB','lab-v2.sqlite3'))
    if production and database.exists():
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            from v2.registry_import import require_completed_transition
            require_completed_transition(db,LAB_GUILD_ID)
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='players'").fetchone():
                if db.execute('SELECT 1 FROM players WHERE user_id<1 OR mkc_id<1').fetchone():
                    raise LabError('Production database contains fixtures; use a fresh production database.')
                current=db.execute("SELECT value FROM lab_settings WHERE key='current_season'").fetchone()[0]
                if current!='S9':raise LabError('Existing production database must already be S9; never import a lab database.')
    store = LabStore(database)
    ratings = LabRatings(store)
    moderation=Moderation(store)
    if production:
        with store.connect() as db:
            current=db.execute("SELECT value FROM lab_settings WHERE key='current_season'").fetchone()[0]
            if current=='LAB-INITIAL':
                db.execute("INSERT OR IGNORE INTO lab_seasons(name,started_by) VALUES ('S9',0)")
                db.execute("UPDATE lab_settings SET value='S9' WHERE key='current_season'")
    expired=store.reset_button_previews()
    if expired:
        print(f'Canceled {expired} unconfirmed previews from the previous bot session; ratings unchanged.')
    reporter_role = str(runtime['roles']['reporter']) if runtime else os.environ.get("V2_LAB_REPORTER_ROLE_ID", "")
    if reporter_role and (not reporter_role.isdecimal() or int(reporter_role)<1):
        raise ValueError("V2_LAB_REPORTER_ROLE_ID must be a positive numeric role ID.")
    intents = discord.Intents.none()
    intents.guilds = True
    bot = discord.Bot(
        intents=intents,
        debug_guilds=[LAB_GUILD_ID],
        auto_sync_commands=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )

    from v2.launch_scope import submission_tier
    results_channels={}
    for tier,key in (('tier-all','V2_LAB_TIER_ALL_RESULTS_CHANNEL_ID'),('tier-c','V2_LAB_TIER_C_RESULTS_CHANNEL_ID'),('tier-b','V2_LAB_TIER_B_RESULTS_CHANNEL_ID'),('tier-a','V2_LAB_TIER_A_RESULTS_CHANNEL_ID')):
        raw=os.environ.get(key,'')
        if raw and (not raw.isdecimal() or int(raw)<1):raise ValueError(f'{key} must be a positive channel ID.')
        if raw:results_channels[tier]=int(raw)
    if runtime:results_channels={tier:item['results_channel_id'] for tier,item in runtime['tiers'].items()}
    publisher=TablePublisher(store,bot,LAB_GUILD_ID,results_channels)
    members=MemberSync(store,ratings,moderation,bot,runtime) if runtime else None
    reductions=LossReductions(store)
    def command_name(name):
        if not production:return name
        return {'v2country':'set_country','v2reduceloss':'reduce_loss','v2strikes':'strike_summary','v2mystrikes':'strikes','v2mmrdeduct':'mmr_penalty','v2setname':'set_player_name','v2fixroles':'fix_roles'}.get(name,name.removeprefix('v2'))
    async def sync_members(ids):
        if not members:return 'Discord member effects are not configured in this lab session.'
        ids=set(ids)
        members.queue(ids)
        await members.drain()
        with store.connect() as db:
            pending={row[0] for row in db.execute('SELECT user_id FROM member_sync_jobs WHERE pending=1')}&ids
        return f'Member synchronization pending for {len(pending)} requested member(s); inspect /fix_roles or /v2fixroles.' if pending else 'Discord member roles/names synchronized.'
    async def finish_table(table_id):
        with store.connect() as db:
            ids=[r[0] for r in db.execute('SELECT user_id FROM rating_events WHERE table_id=?',(table_id,))]
        result=await sync_members(ids)
        await publisher.publish(table_id)
        return 'Results and MMR embeds published. '+result
    def workflow_channel(ctx,kind):
        return ctx.guild_id==LAB_GUILD_ID and (ctx.channel_id==runtime['channels'][kind] if production else ctx.channel_id==DEBUG_CHANNEL_ID)


    def ui_staff(interaction):
        return staff(SimpleNamespace(guild_id=interaction.guild_id,channel_id=DEBUG_CHANNEL_ID,author=interaction.user))
    def ui_reporter(interaction):
        return reporter(SimpleNamespace(guild_id=interaction.guild_id,channel_id=DEBUG_CHANNEL_ID,author=interaction.user))

    async def reversal_panel(request_id,reviewer_id):
        result=await asyncio.to_thread(ratings.reversal_preview,request_id)
        from v2.correction_policy import assert_reversal_reviewer
        with store.connect() as db:
            request=db.execute('SELECT * FROM table_reversal_requests WHERE id=?',(request_id,)).fetchone()
            assert_reversal_reviewer(db,request,reviewer_id)
        lines='\n'.join(f"{r['name']}: {r['from']} → {r['to'] if r['to'] is not None else 'unplaced'}" for r in result['rows'])
        policy_note='Disputed correction: independent staff required.' if result['requires_independent'] else 'Routine correction: one authorized staff member can complete it.'
        result['content']=policy_note+'\n'+f"Reverse table #{result['table_id']}? Reason: {result['reason']}\nOriginal result: {result['evidence']}\n{lines}\nNothing changed. Approve restores these balances and removes the old result posts."
        async def refresh():return (await reversal_panel(request_id,reviewer_id))[0]
        async def approve(interaction,preview):
            rows=await asyncio.to_thread(ratings.approve_reversal,request_id,interaction.user.id,preview['digest'])
            effects=await sync_members([r['user_id'] for r in rows])
            try:await publisher.retract(preview['table_id']);delivery='Old result posts removed.'
            except Exception:delivery='Old result deletion pending; retry publication recovery after fixing access.'
            if preview['requires_independent']:
                await interaction.followup.send('The original Reporter/correction requester opens /'+command_name('v2corrections')+' to enter corrected scores. This dispute still requires independent approval.',ephemeral=True)
            else:
                await interaction.followup.send('Enter corrected scores, then approve the corrected preview.',view=ActionView('Enter corrected scores',lambda i:i.user.id==interaction.user.id and ui_staff(i),lambda i:open_replacement(i,preview['table_id'])),ephemeral=True)
            return 'Reversal approved; original balances restored. '+delivery+' '+effects
        async def reject(interaction):
            await asyncio.to_thread(ratings.reject_reversal,request_id,interaction.user.id)
            return 'Correction rejected. Ratings unchanged.'
        return result,ReviewButtons(reviewer_id,ui_staff,result,refresh,approve,reject)

    async def replacement_panel(table_id,reviewer_id):
        result=await asyncio.to_thread(ratings.review_preview,table_id)
        with store.connect() as db:table=dict(db.execute('SELECT * FROM tables WHERE id=?',(table_id,)).fetchone())
        from v2.correction_policy import assert_replacement_reviewer
        with store.connect() as db:assert_replacement_reviewer(db,table_id,reviewer_id)
        result['content']=f"Corrected table #{table_id}, replaces #{table['replaces_table_id']}, {table['tier']}, format {table['format_size']}\nScores: {table['scores']}\n"+rating_lines(result['rows'])+'\nApprove validates and applies this exact preview once.'
        async def refresh():return (await replacement_panel(table_id,reviewer_id))[0]
        async def approve(interaction,preview):
            await asyncio.to_thread(ratings.apply,table_id,interaction.user.id,preview['digest'],False,preview['review_pending'])
            try:effects=await finish_table(table_id)
            except Exception:effects='Ratings applied; publication recovery pending. Do not apply again.'
            return f'Corrected table #{table_id} approved and applied once. '+effects
        async def reject(interaction):
            await asyncio.to_thread(store.review_table,table_id,interaction.user.id,False)
            return 'Corrected table rejected; no ratings applied.'
        view=ReviewButtons(reviewer_id,ui_staff,result,refresh,approve,reject)
        if not result['review_pending']:
            for child in view.children:
                if child.label=='Reject':child.label='Already validated';child.disabled=True
        return result,view

    async def send_panel(interaction,panel):
        preview,view=panel
        view.message=await interaction.followup.send(preview['content'],view=view,ephemeral=True,wait=True)

    async def open_replacement(interaction,original_id):
        with store.connect() as db:
            original=dict(db.execute('SELECT * FROM tables WHERE id=?',(original_id,)).fetchone())
            request=db.execute("SELECT requester_id FROM table_reversal_requests WHERE table_id=? AND status='approved' ORDER BY id DESC LIMIT 1",(original_id,)).fetchone()
        if not ui_staff(interaction) and interaction.user.id not in (original['reporter_id'],request['requester_id'] if request else None):return await interaction.response.send_message('An authorized staff member or the original Reporter/correction requester can submit corrected scores.',ephemeral=True)
        async def submit(i,values):
            evidence=original_evidence(store,LAB_GUILD_ID,original_id)
            await publisher.destination(original['tier'])
            table_id=store.submit_table(i.user.id,original['format_size'],values[0],original['tier'],evidence,original_id)
            await publisher.prepare(table_id,original['tier'])
            from v2.correction_policy import replacement_independent
            with store.connect() as db:independent=replacement_independent(db,table_id)
            if ui_staff(i) and not independent:
                return await send_panel(i,await replacement_panel(table_id,i.user.id))
            await i.followup.send(f'Corrected table #{table_id} submitted. Authorized staff opens /'+command_name('v2corrections')+' and chooses it to approve.',ephemeral=True)
        modal=TextModal('Corrected table scores',[dict(label='Player/score pairs (12 players; total 984)',value=original['scores'],style=discord.InputTextStyle.long,max_length=4000)],ui_reporter,submit)
        await interaction.response.send_modal(modal)

    async def request_panel(request_id,reviewer_id,verified_name=''):
        with store.connect() as db:row=db.execute("SELECT * FROM requests WHERE id=? AND status='pending'",(request_id,)).fetchone()
        if not row:raise LabError('Request is missing or already reviewed.')
        row=dict(row)
        display_value=f"https://mkcentral.com/registry/players/profile?id={row['value']}" if row['kind']=='verify' else row['value']
        if reviewer_id==row['user_id']:raise LabError('A different staff member must review this request.')
        # Fixed request contents are re-read by the store before its atomic approval.
        preview={'digest':str(request_id)+':'+row['value']+':'+verified_name,'content':f"{row['kind'].title()} request from <@{row['user_id']}>: {display_value}"+(f"\nVerified Lounge name: {verified_name}" if verified_name else '')}
        async def refresh():return (await request_panel(request_id,reviewer_id,verified_name))[0]
        async def approve(interaction,unused):
            await asyncio.to_thread(store.review,request_id,interaction.user.id,True,verified_name)
            return 'Request approved. '+await sync_members([row['user_id']])
        async def reject(interaction):
            await asyncio.to_thread(store.review,request_id,interaction.user.id,False)
            return 'Request rejected. Member registration/name unchanged.'
        return preview,ReviewButtons(reviewer_id,ui_staff,preview,refresh,approve,reject)

    async def choose_request(interaction,value):
        request_id=int(value)
        with store.connect() as db:row=db.execute("SELECT kind,user_id FROM requests WHERE id=? AND status='pending'",(request_id,)).fetchone()
        if not row:raise LabError('Request is already resolved.')
        if row['kind']=='verify':
            if row['user_id']==interaction.user.id:raise LabError('A different staff member must verify you.')
            async def open_name(i):
                async def submit(j,values):await send_panel(j,await request_panel(request_id,j.user.id,values[0]))
                await i.response.send_modal(TextModal('Verified Lounge name',[dict(label='Name checked by staff',max_length=32)],ui_staff,submit))
            view=ActionView('Enter verified name',ui_staff,open_name)
            reject_button=discord.ui.Button(label='Reject request',style=discord.ButtonStyle.red)
            async def reject_now(i):
                if not ui_staff(i) or i.user.id!=interaction.user.id:return await i.response.send_message('Reopen your own authorized request queue.',ephemeral=True)
                await i.response.defer()
                try:store.review(request_id,i.user.id,False)
                except LabError as error:return await i.followup.send(str(error),ephemeral=True)
                for child in view.children:child.disabled=True
                view.stop();await i.edit_original_response(content='Verification request rejected. No member registration changed.',view=view)
            reject_button.callback=reject_now;view.add_item(reject_button)
            with store.connect() as db:profile_id=db.execute('SELECT value FROM requests WHERE id=?',(request_id,)).fetchone()[0]
            return await interaction.followup.send(f'Check https://mkcentral.com/registry/players/profile?id={profile_id}, then enter the approved Lounge name or reject the request.',view=view,ephemeral=True)
        await send_panel(interaction,await request_panel(request_id,interaction.user.id))

    @bot.message_command(name='Correct table',guild_ids=[LAB_GUILD_ID])
    async def correct_table_message(ctx,message:discord.Message):
        if not ui_reporter(SimpleNamespace(guild_id=ctx.guild_id,user=ctx.author)) or ctx.channel_id not in results_channels.values():
            return await ctx.respond('Use this on a bot result post in a configured results channel; Reporter/staff required.',ephemeral=True)
        try:table_id=recorded_result(store,LAB_GUILD_ID,ctx.guild_id,ctx.channel_id,message.id,message.author.id,bot.user.id,results_channels)
        except LabError as error:return await ctx.respond(str(error),ephemeral=True)
        async def submit(interaction,values):
            evidence=f'https://discord.com/channels/{LAB_GUILD_ID}/{ctx.channel_id}/{message.id}'
            request_id=ratings.request_reversal(table_id,interaction.user.id,values[0],evidence)
            await interaction.followup.send('Correction requested. Authorized staff opens /'+command_name('v2corrections')+' to review. Ratings unchanged.',ephemeral=True)
        await ctx.send_modal(TextModal('Request table correction',[dict(label='What needs correcting?',max_length=200)],ui_reporter,submit))

    @bot.message_command(name='Report table issue',guild_ids=[LAB_GUILD_ID])
    async def report_table_message(ctx,message:discord.Message):
        if ctx.guild_id!=LAB_GUILD_ID or ctx.channel_id not in results_channels.values():return await ctx.respond('Use this on a V2 post in a configured results channel.',ephemeral=True)
        try:table_id=recorded_result(store,LAB_GUILD_ID,ctx.guild_id,ctx.channel_id,message.id,message.author.id,bot.user.id,results_channels)
        except LabError as error:return await ctx.respond(str(error),ephemeral=True)
        async def submit(interaction,values):
            flag_id=store.flag_table(table_id,interaction.user.id,values[0])
            await interaction.followup.send(f'Issue #{flag_id} reported. Staff will review; no MMR changed.',ephemeral=True)
        authorized=lambda i:i.guild_id==LAB_GUILD_ID and i.channel_id==ctx.channel_id
        await ctx.send_modal(TextModal('Report table issue',[dict(label='Describe the issue',max_length=200)],authorized,submit))

    async def flag_panel(flag_id,reviewer_id,resolution_reason):
        with store.connect() as db:row=db.execute("SELECT f.*,t.reporter_id FROM table_flags f JOIN tables t ON t.id=f.table_id WHERE f.id=? AND f.status='open'",(flag_id,)).fetchone()
        if not row:raise LabError('Issue already resolved or missing.')
        if reviewer_id in (row['actor_id'],row['reporter_id']):raise LabError('A reviewer independent of the reporter and flagger must resolve this issue.')
        preview={'digest':str(flag_id)+':'+row['status']+':'+resolution_reason,'content':f"Issue on table #{row['table_id']}: {row['reason']}\nResolution reason: {resolution_reason}\nChoose Uphold issue or Clear issue. Neither changes MMR. Upheld applied results still require a correction."}
        async def refresh():return (await flag_panel(flag_id,reviewer_id,resolution_reason))[0]
        async def approve(interaction,unused):
            store.resolve_flag(flag_id,interaction.user.id,True,resolution_reason)
            return 'Issue upheld. Ratings unchanged; an applied result still requires an audited correction.'
        async def reject(interaction):
            store.resolve_flag(flag_id,interaction.user.id,False,resolution_reason)
            return 'Issue cleared. Ratings unchanged.'
        return preview,ReviewButtons(reviewer_id,ui_staff,preview,refresh,approve,reject,approve_label="Uphold issue",reject_label="Clear issue")

    @bot.slash_command(
        name=command_name("v2status"),
        description="Check the V2 lab bot",
        guild_ids=[LAB_GUILD_ID],
    )
    async def v2status(ctx: discord.ApplicationContext) -> None:
        if ctx.guild_id != LAB_GUILD_ID or ctx.channel_id != DEBUG_CHANNEL_ID:
            await ctx.respond("Use this command in the lab #bot-debug channel.", ephemeral=True)
            return
        await ctx.respond("V2 lab bot is online.", ephemeral=True)

    def lab_channel(ctx: discord.ApplicationContext) -> bool:
        if not production:return ctx.guild_id==LAB_GUILD_ID and ctx.channel_id==DEBUG_CHANNEL_ID
        allowed=set(runtime['channels'].values())|{v[k] for v in runtime['tiers'].values() for k in ('submission_channel_id','results_channel_id')}
        return ctx.guild_id==LAB_GUILD_ID and ctx.channel_id in allowed

    def staff(ctx: discord.ApplicationContext) -> bool:
        return lab_channel(ctx) and (ctx.author.guild_permissions.manage_guild or bool(runtime and any(r.id in runtime['roles']['staff'] for r in ctx.author.roles)))

    def restricted(member):
        return bool(runtime and {r.id for r in member.roles}&{runtime['roles']['loungeless'],runtime['roles']['chat_restricted']})

    def reporter(ctx: discord.ApplicationContext) -> bool:
        if not lab_channel(ctx) or restricted(ctx.author):
            return False
        return can_report(ctx.guild_id,DEBUG_CHANNEL_ID,staff(ctx),
                          [role.id for role in ctx.author.roles],int(reporter_role) if reporter_role else None,
                          LAB_GUILD_ID,DEBUG_CHANNEL_ID)

    async def staff_log(kind,text):
        if not runtime:return
        try:
            channel=bot.get_channel(runtime['channels'][kind]) or await bot.fetch_channel(runtime['channels'][kind])
            await channel.send(text,allowed_mentions=discord.AllowedMentions.none())
        except Exception as error:print(f'Staff log delivery failed ({type(error).__name__}); inspect durable request/moderation records.')

    async def reply_error(ctx: discord.ApplicationContext, error: LabError) -> None:
        await ctx.followup.send(str(error), ephemeral=True)

    @bot.slash_command(name=command_name("v2verify"), description="Request lab verification", guild_ids=[LAB_GUILD_ID])
    async def v2verify(ctx: discord.ApplicationContext, profile: str):
        if not workflow_channel(ctx,"verification_requests"):
            return await ctx.respond("Use the configured verification channel.", ephemeral=True)
        await ctx.defer(ephemeral=True)
        try:
            request_id = store.request_verify(ctx.author.id, profile)
        except LabError as error:
            return await reply_error(ctx, error)
        await staff_log("verification_log",f"Verification request #{request_id} from <@{ctx.author.id}>: {profile}. Staff: open /{command_name('v2pending')} and choose the request.")
        await ctx.followup.send(f"Lab verification request #{request_id} recorded for staff review.", ephemeral=True)

    @bot.slash_command(name=command_name("v2name"), description="Request a lab player name", guild_ids=[LAB_GUILD_ID])
    async def v2name(ctx: discord.ApplicationContext, name: str):
        if not workflow_channel(ctx,"name_requests"):
            return await ctx.respond("Use the configured name-request channel.", ephemeral=True)
        await ctx.defer(ephemeral=True)
        try:
            if runtime:
                with store.connect() as db:
                    recent=db.execute("SELECT 1 FROM requests WHERE user_id=? AND kind='name' AND status='approved' AND reviewed_at>datetime('now','-60 days')",(ctx.author.id,)).fetchone()
                if recent:raise LabError('Original name-change cooldown is 60 days; staff can use the name override for corrections.')
            request_id = store.request_name(ctx.author.id, name)
        except LabError as error:
            return await reply_error(ctx, error)
        await staff_log("verification_log",f"Name request #{request_id} from <@{ctx.author.id}>: {name}. Staff: open /{command_name('v2pending')} and choose the request.")
        await ctx.followup.send(f"Lab name request #{request_id} recorded for staff review.", ephemeral=True)

    @bot.slash_command(name=command_name("v2review"), description="Review a lab verification or name request", guild_ids=[LAB_GUILD_ID])
    async def v2review(ctx: discord.ApplicationContext, request_id: int, approve: bool,
                       verified_name: str = ""):
        if not staff(ctx):
            return await ctx.respond("Staff permission required in a configured channel.", ephemeral=True)
        await ctx.defer(ephemeral=True)
        try:
            status = store.review(request_id, ctx.author.id, approve, verified_name)
        except LabError as error:
            return await reply_error(ctx, error)
        effects=''
        if approve:
            with store.connect() as db:target=db.execute('SELECT user_id FROM requests WHERE id=?',(request_id,)).fetchone()[0]
            effects=' '+await sync_members([target])
        await staff_log("verification_log",f"Request #{request_id}: {status} by <@{ctx.author.id}>.{effects}")
        await ctx.followup.send(f"Request #{request_id}: {status}."+effects, ephemeral=True)

    @bot.slash_command(name=command_name("v2pending"), description="List pending lab requests", guild_ids=[LAB_GUILD_ID])
    async def v2pending(ctx: discord.ApplicationContext):
        if not staff(ctx):
            return await ctx.respond("Staff permission required in a configured channel.", ephemeral=True)
        await ctx.defer(ephemeral=True)
        with store.connect() as db:
            requests = db.execute("SELECT id,kind,user_id,value FROM requests WHERE status='pending' ORDER BY id LIMIT 15").fetchall()
        listing = "\n".join(f"#{r['id']} {r['kind']} <@{r['user_id']}>: {r['value']}" for r in requests)
        options=[discord.SelectOption(label=f"{r['kind']} request #{r['id']}",description=f"Member {r['user_id']}: {r['value']}"[:100],value=str(r['id'])) for r in requests]
        view_options={'view':ChoiceView(ctx.author.id,ui_staff,options,choose_request)} if options else {}
        await ctx.followup.send(listing or 'No pending requests.',ephemeral=True,**view_options)

    @bot.slash_command(name=command_name("v2strike"), description="Issue a strike and apply its MMR penalty", guild_ids=[LAB_GUILD_ID])
    async def v2strike(ctx: discord.ApplicationContext, member: discord.Member, reason: str, mmr_penalty: int = 0):
        if not (reporter(ctx) if runtime else staff(ctx)):
            return await ctx.respond("Reporter/staff permission required in the configured channel.", ephemeral=True)
        user_id=str(member.id)
        await ctx.defer(ephemeral=True)
        try:
            if not user_id.isdecimal():
                raise LabError("Enter a numeric Discord user ID.")
            result=moderation.strike(ctx.author.id,int(user_id),mmr_penalty,reason,f'strike:{ctx.interaction.id}')
        except LabError as error:
            return await reply_error(ctx, error)
        effects=await sync_members([int(user_id)])
        await staff_log("strikes_log",f"Strike #{result['strike_id']} for <@{user_id}> by <@{ctx.author.id}>: {reason}; applied MMR {result['delta']:+d}.")
        await ctx.followup.send(f"Strike #{result['strike_id']}: applied MMR change {result['delta']:+d}; active strikes {result['active_count']}. {effects}",ephemeral=True)

    @bot.slash_command(name=command_name("v2strikes"), description="Inspect season counts and preserved lab strike history", guild_ids=[LAB_GUILD_ID])
    async def v2strikes(ctx: discord.ApplicationContext, member: discord.Member = None):
        user_id=str(member.id) if member else ''
        if not staff(ctx):
            return await ctx.respond("Staff permission required in a configured channel.", ephemeral=True)
        await ctx.defer(ephemeral=True)
        if user_id and not user_id.isdecimal():
            return await ctx.followup.send("Enter a numeric Discord user ID or leave it blank.", ephemeral=True)
        summary = store.strike_summary(int(user_id) if user_id else None)
        await ctx.followup.send(
            f"Lab season: {summary['season']} | season strikes: {summary['season_count']} | "
            f"unexpired this season: {summary['active_count']} | all-season history: {summary['history_count']}",
            ephemeral=True)

    @bot.slash_command(name=command_name("v2seasonstart"), description="Start a new lab strike season; preserve all history", guild_ids=[LAB_GUILD_ID])
    async def v2seasonstart(ctx: discord.ApplicationContext, season: str, confirmation: str):
        if production:return await ctx.respond('Season reset is unavailable in production.',ephemeral=True)
        if not staff(ctx):
            return await ctx.respond("Staff permission required in a configured channel.", ephemeral=True)
        await ctx.defer(ephemeral=True)
        if confirmation != "LAB ONLY":
            return await ctx.followup.send('Enter confirmation: LAB ONLY.', ephemeral=True)
        try:
            changed = store.start_lab_season(season, ctx.author.id)
        except LabError as error:
            return await reply_error(ctx, error)
        if changed and members:await members.reconcile()
        await ctx.followup.send(
            f"Lab strike season {season}: {'started' if changed else 'already current; unchanged'}. "
            "History preserved. New-season lab ratings start unplaced. Configured lab member roles are reconciled.", ephemeral=True)

    @bot.slash_command(name=command_name("v2table"), description="Generate a lab table and confirm with Yes or No", guild_ids=[LAB_GUILD_ID])
    async def v2table(ctx: discord.ApplicationContext, format_size: discord.Option(int, "Team size: 1=FFA, 2=2v2, 3=3v3, 4=4v4", choices=[1,2,3,4]), scores: discord.Option(str, "12 name/score pairs; spaced names: one per line, or JSON pairs", max_length=4000), tier: str = "tier-all",
                      evidence: str = "", replaces_table_id: int = 0):
        if not reporter(ctx):
            return await ctx.respond("Reporter/staff permission required in a configured channel.", ephemeral=True)
        await ctx.defer()
        table_id=None
        try:
            if production:
                tier=submission_tier(runtime['tiers'],ctx.channel_id)
            await publisher.destination(tier)
            if replaces_table_id:evidence=original_evidence(store,LAB_GUILD_ID,replaces_table_id,evidence)
            table_id = store.submit_table(ctx.author.id, format_size, scores, tier, evidence, replaces_table_id or None)
            if replaces_table_id:
                await publisher.prepare(table_id,tier)
                return await ctx.followup.send(f"Replacement table #{table_id} recorded. Independent staff review is required for corrections.",ephemeral=True)
            result=ratings.preview(table_id,routine=True)
            await publisher.prepare(table_id,tier)
            with store.connect() as db:
                created_at=db.execute('SELECT created_at FROM tables WHERE id=?',(table_id,)).fetchone()[0]
            results_png=await asyncio.to_thread(render_results,table_id,format_size,tier,scores,result['rows'],results_date(created_at))
        except LabError as error:
            if table_id is not None:
                store.cancel_table(table_id,ctx.author.id,'Preview blocked; no confirmation offered')
            return await reply_error(ctx,error)
        except Exception:
            if table_id is not None:
                store.cancel_table(table_id,ctx.author.id,'Preview could not be generated')
            raise
        def authorized(interaction):
            return not restricted(interaction.user) and interaction.guild_id==LAB_GUILD_ID and interaction.channel_id==ctx.channel_id and can_report(
                interaction.guild_id,DEBUG_CHANNEL_ID,interaction.user.guild_permissions.manage_guild or bool(runtime and any(r.id in runtime['roles']['staff'] for r in interaction.user.roles)),
                [role.id for role in interaction.user.roles],int(reporter_role) if reporter_role else None,LAB_GUILD_ID,DEBUG_CHANNEL_ID)
        view=TableButtons(store,ratings,table_id,ctx.author.id,result['digest'],authorized,finish_table)
        try:
            view.message=await ctx.followup.send(f"Table #{table_id}: is this table correct? Choose Yes to apply, or No to cancel. Expires in five minutes."+(f"\nEvidence: {evidence}" if evidence else ''),
                                                 files=[discord.File(BytesIO(results_png),filename='results.png')],view=view,wait=True)
        except discord.HTTPException:
            store.cancel_table(table_id,ctx.author.id,'Preview delivery failed')
            raise

    @bot.slash_command(name=command_name("v2publishretry"), description="Retry applied table result delivery only", guild_ids=[LAB_GUILD_ID])
    async def v2publishretry(ctx: discord.ApplicationContext, table_id: int):
        if not staff(ctx):return await ctx.respond('Lab staff only in #bot-debug.',ephemeral=True)
        await ctx.defer(ephemeral=True)
        try:
            with store.connect() as db:state=db.execute('SELECT status FROM tables WHERE id=?',(table_id,)).fetchone()
            if state and state[0]=='reversed':await publisher.retract(table_id)
            else:await publisher.publish(table_id)
        except Exception as error:
            return await ctx.followup.send(f'Publication not completed: {error}. Ratings were not reapplied.',ephemeral=True)
        await ctx.followup.send(f'Table #{table_id} delivery complete. Ratings were not reapplied.',ephemeral=True)

    @bot.slash_command(name=command_name('v2country'),description='Record a verified player country for flags',guild_ids=[LAB_GUILD_ID])
    async def v2country(ctx:discord.ApplicationContext,member:discord.Member,code:str,reason:str):
        if not staff(ctx):return await ctx.respond('Staff permission required in a configured channel.',ephemeral=True)
        await ctx.defer(ephemeral=True)
        from v2.countries import set_country
        try:
            saved=await asyncio.to_thread(set_country,store,member.id,code,ctx.author.id,reason)
        except LabError as error:return await reply_error(ctx,error)
        await ctx.followup.send(f'Country {saved or "cleared"} recorded for <@{member.id}>. Ratings unchanged. New images and the next public feed refresh will use it.',ephemeral=True)

    @bot.slash_command(name=command_name('v2reduceloss'),description='Reduce one player’s applied S9 table loss once',guild_ids=[LAB_GUILD_ID])
    async def v2reduceloss(ctx:discord.ApplicationContext,member:discord.Member,table_id:int,fraction:str,reason:str):
        if not reporter(ctx):return await ctx.respond('Reporter permission required in a configured channel.',ephemeral=True)
        await ctx.defer(ephemeral=True)
        actor_id=ctx.author.id
        async def preview():
            result=await asyncio.to_thread(reductions.preview,table_id,member.id,actor_id,fraction,reason)
            result['content']=(f"Table #{table_id}, <@{member.id}>: retain {result['numerator']}/{result['denominator']} of the original loss.\n"
                f"Loss {result['original_delta']} → {result['retained_delta']}; credit +{result['refund']}.\n"
                f"Current MMR {result['before']} → {result['after']}. Reason: {result['reason']}\n"
                'One reduction per player/table. Later applied results stay unchanged; future tables use the credited balance. '
                'Positive credits block reversals across this accounting boundary. Confirm to record the reduction.')
            return result
        try:result=await preview()
        except LabError as error:return await reply_error(ctx,error)
        async def approve(interaction,shown):
            result=await asyncio.to_thread(reductions.apply,table_id,member.id,interaction.user.id,fraction,reason,shown['digest'])
            try:effects=await sync_members([member.id])
            except Exception as error:
                print(f'Loss credit recorded; member synchronization retry needed ({type(error).__name__}).')
                effects='Member synchronization pending; use /fix_roles. The credit is already recorded; do not refund again.'
            await staff_log('strikes_log',f"Loss reduction table #{table_id}, <@{member.id}>, by <@{interaction.user.id}>: {result['original_delta']} → {result['retained_delta']}; credit +{result['refund']}. Reason: {result['reason']}")
            return f"Loss reduction recorded once. MMR {result['before']} → {result['after']}. {effects}"
        async def cancel(interaction):return 'Loss reduction canceled. Ratings unchanged.'
        view=ReviewButtons(actor_id,ui_reporter,result,preview,approve,cancel,approve_label='Confirm reduction',reject_label='Cancel')
        view.message=await ctx.followup.send(result['content'],view=view,ephemeral=True)

    @bot.slash_command(name=command_name("v2mmrdeduct"), description="Apply an MMR deduction without a strike", guild_ids=[LAB_GUILD_ID])
    async def v2mmrdeduct(ctx: discord.ApplicationContext, member: discord.Member, points: int, reason: str):
        if not (reporter(ctx) if runtime else staff(ctx)):
            return await ctx.respond("Reporter/staff permission required.", ephemeral=True)
        user_id=str(member.id)
        await ctx.defer(ephemeral=True)
        try:
            if not user_id.isdecimal():
                raise LabError("Enter a numeric Discord user ID.")
            result=moderation.deduct(ctx.author.id,int(user_id),points,reason,f'deduction:{ctx.interaction.id}')
        except LabError as error:
            return await reply_error(ctx, error)
        effects=await sync_members([int(user_id)])
        await staff_log('strikes_log',f"MMR penalty for <@{user_id}> by <@{ctx.author.id}>: {reason}; {result['before']} → {result['after']}.")
        await ctx.followup.send(f"MMR-only deduction applied: {result['before']} → {result['after']} ({result['delta']:+d}). No strike issued. {effects}",ephemeral=True)

    @bot.slash_command(name=command_name("v2unstrike"),description="Remove a recent strike and refund its applied penalty",guild_ids=[LAB_GUILD_ID])
    async def v2unstrike(ctx:discord.ApplicationContext,member:discord.Member,reason:str):
        if not (reporter(ctx) if runtime else staff(ctx)):return await ctx.respond('Reporter/staff permission required.',ephemeral=True)
        await ctx.defer(ephemeral=True)
        rows=moderation.player_strikes(member.id)
        options=[discord.SelectOption(label=f"Remove strike #{r['id']}",description=r['reason'][:100],value=str(r['id'])) for r in rows[:25]]
        async def choose(interaction,value):
            result=moderation.unstrike(interaction.user.id,int(value),reason,f'unstrike:{interaction.id}')
            effects=await sync_members([result['user_id']])
            await staff_log('strikes_log',f"Strike #{value} removed by <@{interaction.user.id}>: {reason}; actual refund {result['delta']:+d}.")
            await interaction.followup.send(f"Strike #{value} removed; refund {result['delta']:+d}. History retained. {effects}",ephemeral=True)
        view_options={'view':ChoiceView(ctx.author.id,ui_reporter if runtime else ui_staff,options,choose)} if options else {}
        await ctx.followup.send('Choose the strike to remove. The 24-hour and no-self-removal rules still apply.' if options else 'This member has no active season strikes.',ephemeral=True,**view_options)

    @bot.slash_command(name=command_name('v2stats'),description='S9 player statistics from applied V2 tables',guild_ids=[LAB_GUILD_ID])
    async def v2stats(ctx:discord.ApplicationContext,member:discord.Member=None,player:str=None,
                      tier:discord.Option(str,choices=['all','c','b','a'])=None,
                      mogi_format:discord.Option(int,choices=[1,2,3,4])=None,last:int=None):
        if not lab_channel(ctx) or restricted(ctx.author):
            return await ctx.respond('Use a configured Lounge channel with unrestricted access.',ephemeral=True)
        await ctx.defer(ephemeral=True)
        try:
            if member is not None and player is not None:raise LabError('Choose a member or an exact player name, not both.')
            def load():
                with store.connect() as db:
                    db.execute('BEGIN')
                    data=statistics_snapshot(db)
                    uid=member.id if member else ctx.author.id
                    if player is not None:
                        found=next((p for p in data['players'] if p['name']==player),None)
                        if found is None:raise LabError('Exact player name not found; try the member picker.')
                        uid=found['user_id']
                    return player_statistics(data,uid,tier='tier-'+tier if tier else None,format_size=mogi_format,last=last)
            result=await asyncio.to_thread(load)
        except LabError as error:return await reply_error(ctx,error)
        from v2.stats_graph import render_stats_graph,format_score_average
        p,s=result['player'],result['summary']
        def value(number):return '—' if number is None else f'{number:,.2f}'.rstrip('0').rstrip('.')
        embed=discord.Embed(title='S9 Player Statistics',description=discord.utils.escape_markdown(p['name']),color=discord.Color.blurple())
        for label,text in (
            ('Current MMR / Rank',f"{p['mmr'] if p['mmr'] is not None else 'Unplaced'} / {p['rank']}"),
            ('Leaderboard position',str(p['position']) if p['position'] is not None else 'Unplaced'),
            ('Recorded peak MMR',value(p['peak_mmr'])),
            ('Tables / Placements',f"{s['tables']} / {s['placements']}"),
            ('MMR gains / losses / ties',f"{s['gains']} / {s['losses']} / {s['ties']}"),
            ('MMR gain rate',value(s['gain_rate'])+'%' if s['gain_rate'] is not None else '—'),
            ('Average / Top score',f"{format_score_average(s['average_score'])} / {value(s['top_score'])}"),
            ('Partner score average',format_score_average(s['partner_average'])),
            ('Largest gain / loss',f"{s['largest_gain']:+d} / {s['largest_loss']:+d}"),
            ('Table MMR / Refunds included',f"{s['net_table_delta']:+d} / +{s['refunds']}"),
            ('Last 10 selected tables',f"{s['last_ten']['gains']}-{s['last_ten']['losses']}-{s['last_ten']['ties']} | {s['last_ten']['delta']:+d} MMR")):
            embed.add_field(name=label,value=text,inline=True)
        embed.set_footer(text=f"Applied S9 tables only | {'tier-'+tier if tier else 'all tiers'} | {str(mogi_format)+'-player teams' if mogi_format else 'all formats'} | {str(last)+' latest' if last else 'all tables'} | Placements excluded from gain rate; penalties excluded from table totals.")
        if not s['tables']:embed.add_field(name='No matching tables',value='No applied S9 tables match these filters. Current MMR and rank are still shown.',inline=False)
        graph=await asyncio.to_thread(render_stats_graph,result)
        if graph is None:
            await ctx.followup.send(embed=embed,ephemeral=True)
        else:
            embed.set_image(url='attachment://stats.png')
            await ctx.followup.send(embed=embed,file=discord.File(BytesIO(graph),filename='stats.png'),ephemeral=True)

    @bot.slash_command(name=command_name("v2mmr"),description="See your MMR and rank",guild_ids=[LAB_GUILD_ID])
    async def v2mmr(ctx:discord.ApplicationContext):
        if not lab_channel(ctx):return await ctx.respond('Use a configured Lounge channel.',ephemeral=True)
        await ctx.defer(ephemeral=True)
        season,rows=ratings.ratings();row=next((r for r in rows if r['user_id']==ctx.author.id),None)
        await ctx.followup.send(f"{season}: MMR {row['mmr'] if row and row['mmr'] is not None else 'unplaced'} | {row['rank'] if row else 'not registered'}",ephemeral=True)

    @bot.slash_command(name=command_name("v2mystrikes"),description="See your active strikes and expiry dates",guild_ids=[LAB_GUILD_ID])
    async def v2mystrikes(ctx:discord.ApplicationContext):
        if not lab_channel(ctx):return await ctx.respond('Use a configured Lounge channel.',ephemeral=True)
        await ctx.defer(ephemeral=True)
        rows=moderation.player_strikes(ctx.author.id)
        await ctx.followup.send('\n'.join(f"Strike #{r['id']}: {r['reason']} | Expires {r['expires_at']} UTC" for r in rows) or 'You have no active season strikes.',ephemeral=True)

    @bot.slash_command(name=command_name("v2fixroles"),description="Reconcile your configured Lounge rank and nickname",guild_ids=[LAB_GUILD_ID])
    async def v2fixroles(ctx:discord.ApplicationContext,member:discord.Member=None):
        user_id=str(member.id) if member else ''
        if not lab_channel(ctx):return await ctx.respond('Use a configured Lounge channel.',ephemeral=True)
        if user_id and not staff(ctx):return await ctx.respond('Staff permission is required to reconcile another member.',ephemeral=True)
        if user_id and not user_id.isdecimal():return await ctx.respond('Enter a numeric Discord user ID.',ephemeral=True)
        await ctx.defer(ephemeral=True)
        await ctx.followup.send(await sync_members([int(user_id) if user_id else ctx.author.id]),ephemeral=True)

    @bot.slash_command(name=command_name("v2setname"),description="Staff override for a registered player name correction",guild_ids=[LAB_GUILD_ID])
    async def v2setname(ctx:discord.ApplicationContext,member:discord.Member,name:str):
        if not staff(ctx):return await ctx.respond('Staff permission required.',ephemeral=True)
        user_id=str(member.id)
        await ctx.defer(ephemeral=True)
        try:
            if not user_id.isdecimal():raise LabError('Enter a numeric Discord user ID.')
            name=player_name(name)
            with store.connect() as db:
                if db.execute('UPDATE players SET name=? WHERE user_id=?',(name,int(user_id))).rowcount!=1:raise LabError('Registered player not found.')
                db.execute("INSERT INTO requests(kind,user_id,value,status,reviewer_id,reviewed_at) VALUES ('name',?,?,'approved',?,CURRENT_TIMESTAMP)",(int(user_id),name,ctx.author.id))
        except Exception as error:return await ctx.followup.send(f'Name override failed: {type(error).__name__}: {error}',ephemeral=True)
        await ctx.followup.send('Name corrected. '+await sync_members([int(user_id)]),ephemeral=True)

    @bot.slash_command(name=command_name("v2tables"), description="List pending lab result tables", guild_ids=[LAB_GUILD_ID])
    async def v2tables(ctx: discord.ApplicationContext):
        if not reporter(ctx):
            return await ctx.respond("Staff permission required in a configured channel.", ephemeral=True)
        await ctx.defer(ephemeral=True)
        with store.connect() as db:
            rows = db.execute(
                "SELECT id,reporter_id,format_size FROM tables WHERE status='pending' AND season=(SELECT value FROM lab_settings WHERE key='current_season') ORDER BY id LIMIT 15"
            ).fetchall()
        listing = "\n".join(f"Table #{r['id']}: format {r['format_size']}, submitted by <@{r['reporter_id']}>" for r in rows)
        await ctx.followup.send(listing or "No pending lab tables.", ephemeral=True)

    @bot.slash_command(name=command_name("v2tablereview"), description="Validate or reject a lab table without applying MMR", guild_ids=[LAB_GUILD_ID])
    async def v2tablereview(ctx: discord.ApplicationContext, table_id: int, accept: bool):
        if not staff(ctx):
            return await ctx.respond("Staff permission required in a configured channel.", ephemeral=True)
        await ctx.defer(ephemeral=True)
        try:
            decision = store.review_table(table_id, ctx.author.id, accept)
        except LabError as error:
            return await reply_error(ctx, error)
        await ctx.followup.send(
            f"Lab table #{table_id}: {decision}. No MMR changed; use /v2ratingpreview next if validated.", ephemeral=True
        )

    @bot.slash_command(name=command_name("v2tableview"), description="Inspect a lab table before review", guild_ids=[LAB_GUILD_ID])
    async def v2tableview(ctx: discord.ApplicationContext, table_id: int):
        if not reporter(ctx):
            return await ctx.respond("Staff permission required in a configured channel.", ephemeral=True)
        await ctx.defer(ephemeral=True)
        with store.connect() as db:
            row = db.execute(
                "SELECT id,reporter_id,format_size,scores,status,season,tier,evidence,replaces_table_id FROM tables WHERE id=?", (table_id,)
            ).fetchone()
        if row is None:
            return await ctx.followup.send("Lab table not found.", ephemeral=True)
        await ctx.followup.send(
            f"Table #{row['id']} ({row['status']}), {row['season']}, {row['tier']}, format {row['format_size']}, "
            f"submitted by <@{row['reporter_id']}>:\n`{row['scores']}`\nEvidence: {row['evidence'] or 'not provided'}\nReplaces: {row['replaces_table_id'] or 'none'}", ephemeral=True
        )

    @bot.slash_command(name=command_name("v2mmrpreview"), description="Preview inherited MMR math for a validated fixture table", guild_ids=[LAB_GUILD_ID])
    async def v2mmrpreview(ctx: discord.ApplicationContext, table_id: int, starting_mmr: int = 6000):
        if not staff(ctx):
            return await ctx.respond("Staff permission required in a configured channel.", ephemeral=True)
        await ctx.defer(ephemeral=True)
        with store.connect() as db:
            row = db.execute("SELECT format_size,scores,status FROM tables WHERE id=?",(table_id,)).fetchone()
        if row is None or row['status'] != 'validated':
            return await ctx.followup.send("Validate a lab table before previewing its MMR.", ephemeral=True)
        try:
            teams = await preview_fixture_table(row['scores'],row['format_size'],starting_mmr)
        except LabError as error:
            return await reply_error(ctx,error)
        lines = [f"#{index}: {', '.join(team['players'])} | score {team['score']} | "
                 f"{team['before']} {team['delta']:+d} → {team['after']}"
                 for index,team in enumerate(teams,1)]
        await ctx.followup.send(
            f"Legacy-formula preview for table #{table_id}, synthetic starting MMR {starting_mmr}:\n" +
            "\n".join(lines) + "\nPreview only. No MMR or ranks changed.", ephemeral=True
        )

    def rating_lines(rows):
        return "\n".join(
            f"{r['name']}: {'Placement' if r['before'] is None else r['before']} → "
            f"{r['after']} {r['rank']} " +
            ("(initial placement)" if r['kind']=='placement' else f"({r['applied_delta']:+d})")
            for r in rows)

    @bot.slash_command(name=command_name("v2ratings"), description="Inspect current lab season MMR and ranks", guild_ids=[LAB_GUILD_ID])
    async def v2ratings(ctx: discord.ApplicationContext):
        if not reporter(ctx):
            return await ctx.respond("Staff permission required in a configured channel.", ephemeral=True)
        await ctx.defer(ephemeral=True)
        season, rows=ratings.ratings()
        lines=[f"{r['name']}: {r['mmr'] if r['mmr'] is not None else 'unplaced'} | {r['rank']}" for r in rows]
        for start in range(0,len(lines),20):
            await ctx.followup.send(f"Lab season {season}:\n"+"\n".join(lines[start:start+20]),ephemeral=True)
        if not lines:
            await ctx.followup.send(f"Lab season {season}: no registered players.",ephemeral=True)

    @bot.slash_command(name=command_name("v2ratingpreview"), description="Preview S9 placement and MMR for a validated lab table", guild_ids=[LAB_GUILD_ID])
    async def v2ratingpreview(ctx: discord.ApplicationContext, table_id: int):
        if not staff(ctx):
            return await ctx.respond("Staff permission required in a configured channel.", ephemeral=True)
        await ctx.defer(ephemeral=True)
        try:
            preview,view=await replacement_panel(table_id,ctx.author.id)
            view.message=await ctx.followup.send(preview['content'],view=view,ephemeral=True,wait=True)
        except LabError as error:await reply_error(ctx,error)

    @bot.slash_command(name=command_name("v2ratingapply"), description="Apply a reviewed lab table once using its preview confirmation", guild_ids=[LAB_GUILD_ID])
    async def v2ratingapply(ctx: discord.ApplicationContext, table_id: int, confirmation: str):
        if not staff(ctx):
            return await ctx.respond("Staff permission required in a configured channel.", ephemeral=True)
        await ctx.defer(ephemeral=True)
        try:
            rows=ratings.apply(table_id,ctx.author.id,confirmation)
        except LabError as error:
            return await reply_error(ctx,error)
        try:effects=await finish_table(table_id)
        except Exception:effects='Ratings applied; publication/member recovery is pending. Do not apply this table again.'
        await ctx.followup.send(f"Applied table #{table_id}:\n"+rating_lines(rows)+'\n'+effects,ephemeral=True)

    @bot.slash_command(name=command_name("v2tableflag"), description="Hold a disputed lab table for independent staff review", guild_ids=[LAB_GUILD_ID])
    async def v2tableflag(ctx: discord.ApplicationContext, table_id: int, reason: str):
        if not lab_channel(ctx):
            return await ctx.respond("Use lab #bot-debug.",ephemeral=True)
        await ctx.defer(ephemeral=True)
        try:
            flag_id=store.flag_table(table_id,ctx.author.id,reason)
            await ctx.followup.send(f"Dispute #{flag_id} opened for table #{table_id}. Applied-table disputes pause season rating application; existing ratings are not changed.",ephemeral=True)
        except LabError as error:
            await reply_error(ctx,error)

    @bot.slash_command(name=command_name("v2flagresolve"), description="Independently clear or uphold a lab dispute", guild_ids=[LAB_GUILD_ID])
    async def v2flagresolve(ctx: discord.ApplicationContext, flag_id: int, uphold: bool, reason: str):
        if not staff(ctx):
            return await ctx.respond("Staff permission required in a configured channel.",ephemeral=True)
        await ctx.defer(ephemeral=True)
        try:
            status=store.resolve_flag(flag_id,ctx.author.id,uphold,reason)
            await ctx.followup.send(f"Dispute #{flag_id}: {status}. No ratings changed. Upheld applied results still require audited reversal.",ephemeral=True)
        except LabError as error:
            await reply_error(ctx,error)

    @bot.slash_command(name=command_name("v2reversalrequest"), description="Request correction of the latest applied lab table", guild_ids=[LAB_GUILD_ID])
    async def v2reversalrequest(ctx: discord.ApplicationContext, table_id: int, reason: str, evidence: str = ""):
        if not reporter(ctx):
            return await ctx.respond("Reporter/staff permission required in a configured channel.",ephemeral=True)
        await ctx.defer(ephemeral=True)
        try:
            evidence=original_evidence(store,LAB_GUILD_ID,table_id,evidence)
            request_id=ratings.request_reversal(table_id,ctx.author.id,reason,evidence)
            await ctx.followup.send(f"Correction request #{request_id} opened. Ratings unchanged; season applications paused until staff approval/rejection.",ephemeral=True)
        except LabError as error:
            await reply_error(ctx,error)

    @bot.slash_command(name=command_name("v2reversalpreview"), description="Inspect original balances and proposed lab reversal", guild_ids=[LAB_GUILD_ID])
    async def v2reversalpreview(ctx: discord.ApplicationContext, request_id: int):
        if not staff(ctx):
            return await ctx.respond("Staff permission required in a configured channel.",ephemeral=True)
        await ctx.defer(ephemeral=True)
        try:
            preview,view=await reversal_panel(request_id,ctx.author.id)
            view.message=await ctx.followup.send(preview['content'],view=view,ephemeral=True,wait=True)
        except LabError as error:await reply_error(ctx,error)

    @bot.slash_command(name=command_name("v2reversalreview"), description="Independently approve or reject an audited lab reversal", guild_ids=[LAB_GUILD_ID])
    async def v2reversalreview(ctx: discord.ApplicationContext, request_id: int, approve: bool, confirmation: str = ""):
        if not staff(ctx):
            return await ctx.respond("Staff permission required in a configured channel.",ephemeral=True)
        await ctx.defer(ephemeral=True)
        try:
            if approve:
                rows=ratings.approve_reversal(request_id,ctx.author.id,confirmation)
                await sync_members([r['user_id'] for r in rows])
                with store.connect() as db:original=db.execute('SELECT table_id FROM table_reversal_requests WHERE id=?',(request_id,)).fetchone()[0]
                try:await publisher.retract(original)
                except Exception:print(f'Reversed table #{original} public retraction pending; retry publication recovery.')
                message="Approved; original table marked reversed and prior balances restored. History retained. Submit a linked replacement for staff review."
            else:
                ratings.reject_reversal(request_id,ctx.author.id)
                message="Rejected; ratings unchanged and correction application pause removed. Open disputes may still hold results."
            await ctx.followup.send(f"Correction #{request_id}: {message}",ephemeral=True)
        except LabError as error:
            await reply_error(ctx,error)

    @bot.slash_command(name=command_name("v2corrections"), description="List unresolved lab disputes and reversal requests", guild_ids=[LAB_GUILD_ID])
    async def v2corrections(ctx: discord.ApplicationContext):
        if not reporter(ctx):return await ctx.respond('Reporter/staff permission required.',ephemeral=True)
        await ctx.defer(ephemeral=True)
        with store.connect() as db:
            requests=db.execute("SELECT r.id,r.table_id,r.reason FROM table_reversal_requests r JOIN tables t ON t.id=r.table_id WHERE r.status='pending' AND t.season=(SELECT value FROM lab_settings WHERE key='current_season') ORDER BY r.id LIMIT 6").fetchall()
            pending=db.execute("SELECT id,replaces_table_id,status FROM tables WHERE replaces_table_id IS NOT NULL AND status IN ('pending','validated') AND season=(SELECT value FROM lab_settings WHERE key='current_season') ORDER BY id LIMIT 6").fetchall()
            reversed_rows=db.execute("SELECT t.id FROM tables t WHERE t.status='reversed' AND t.season=(SELECT value FROM lab_settings WHERE key='current_season') AND NOT EXISTS (SELECT 1 FROM tables c WHERE c.replaces_table_id=t.id AND c.status IN ('pending','validated','applied','reversed')) ORDER BY t.id LIMIT 6").fetchall()
            flags=db.execute("SELECT f.id,f.table_id FROM table_flags f JOIN tables t ON t.id=f.table_id WHERE f.status='open' AND t.season=(SELECT value FROM lab_settings WHERE key='current_season') ORDER BY f.id LIMIT 6").fetchall()
        options=[discord.SelectOption(label=f"Review reversal of table #{r['table_id']}",value=f"reverse:{r['id']}") for r in requests]
        options += [discord.SelectOption(label=f"Approve corrected table #{r['id']}",value=f"replacement:{r['id']}") for r in pending]
        options += [discord.SelectOption(label=f"Enter corrected scores for #{r['id']}",value=f"scores:{r['id']}") for r in reversed_rows]
        options += [discord.SelectOption(label=f"Review issue on table #{r['table_id']}",value=f"flag:{r['id']}") for r in flags]
        async def choose(interaction,value):
            kind,record=value.split(':');record=int(record)
            if kind=='scores':
                return await interaction.followup.send('Enter the corrected player/score pairs.',view=ActionView('Enter corrected scores',ui_reporter,lambda i:open_replacement(i,record)),ephemeral=True)
            if not ui_staff(interaction):raise LabError('Staff permission required to approve corrections.')
            if kind=='flag':
                async def open_reason(i):
                    async def submit(j,values):await send_panel(j,await flag_panel(record,j.user.id,values[0]))
                    await i.response.send_modal(TextModal('Staff issue resolution',[dict(label='Resolution reason',max_length=200)],ui_staff,submit))
                return await interaction.followup.send('Enter your resolution reason, then choose Uphold issue or Clear issue.',view=ActionView('Enter resolution reason',ui_staff,open_reason),ephemeral=True)
            await send_panel(interaction,await (reversal_panel(record,interaction.user.id) if kind=='reverse' else replacement_panel(record,interaction.user.id)))
        view_options={'view':ChoiceView(ctx.author.id,ui_reporter,options,choose)} if options else {}
        await ctx.followup.send('Choose a correction. IDs and confirmation codes are carried automatically.' if options else 'No outstanding correction approvals/replacements. Dispute holds still use the flag commands.',ephemeral=True,**view_options)

    @bot.slash_command(name=command_name("v2fixturecase"), description="Start a NEW lab season with a reserved fixture scenario", guild_ids=[LAB_GUILD_ID])
    async def v2fixturecase(ctx: discord.ApplicationContext, case: str, season: str, confirmation: str):
        if production:return await ctx.respond('Fixtures are unavailable in production.',ephemeral=True)
        if not staff(ctx):
            return await ctx.respond("Staff permission required in a configured channel.", ephemeral=True)
        await ctx.defer(ephemeral=True)
        if confirmation!='LAB ONLY':
            return await ctx.followup.send('Enter confirmation: LAB ONLY.',ephemeral=True)
        try:
            ratings.fixture_case(case,season,ctx.author.id)
        except LabError as error:
            return await reply_error(ctx,error)
        if members:await members.reconcile()
        await ctx.followup.send(
            f"Fixture case {case} started in {season}. Reserved fixtures have scenario ratings. "
            "Other registered lab players start unplaced in this new season; old ratings and strikes are preserved. "
            "Use /v2ratings to inspect.",ephemeral=True)

    connection = asyncio.create_task(bot.start(token, reconnect=False))
    ready = asyncio.create_task(bot.wait_until_ready())
    try:
        done, _ = await asyncio.wait(
            (connection, ready), timeout=60, return_when=asyncio.FIRST_COMPLETED
        )
        if connection in done:
            connection.result()
            print("Disconnected before the lab bot was ready.")
            return 1
        if ready not in done:
            print("Timed out waiting for the lab bot to become ready.")
            return 1

        try:
            application = await bot.application_info()
        except discord.HTTPException as error:
            print(f"Could not read bot application information (HTTP {error.status}).")
            return 1
        if application.id != APPLICATION_ID:
            print(f"Wrong bot application: expected {APPLICATION_ID}, got {application.id}.")
            return 1
        if (production and not (LAB_GUILD_ID in {g.id for g in bot.guilds} and {g.id for g in bot.guilds}<={LAB_GUILD_ID,globals()['LAB_GUILD_ID']})) or (not production and not valid_lab_guilds({guild.id for guild in bot.guilds})):
            print("Lab isolation check failed: the bot must see the lab and at most the approved live archive server.")
            return 1

        guild = bot.get_guild(LAB_GUILD_ID)
        channel = guild.get_channel(DEBUG_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            print(f"Lab debug text channel {DEBUG_CHANNEL_ID} was not found.")
            return 1

        if members:
            await members.preflight()
            for cid in set(runtime['channels'].values()):
                destination=bot.get_channel(cid) or await bot.fetch_channel(cid)
                if not isinstance(destination,discord.TextChannel) or destination.guild.id!=LAB_GUILD_ID:raise LabError('Configured workflow channel must be a text channel in the target guild.')
                permission=destination.permissions_for(destination.guild.me)
                if not permission.view_channel or not permission.send_messages:raise LabError('Bot cannot view/post in a configured workflow channel.')
            for tier in runtime['tiers']:await publisher.destination(tier)
            await members.reconcile()
        try:
            await bot.sync_commands(
                commands=[v2country, v2status, v2verify, v2name, v2pending, v2review, v2strike,
                          v2table, v2mmrdeduct, v2reduceloss, v2stats, v2tables, v2tableview,
                          v2mmrpreview, v2strikes, *([] if production else [v2seasonstart]), v2ratings,
                          v2ratingpreview, *([] if production else [v2fixturecase]),
                          v2tableflag, v2flagresolve, v2reversalrequest, v2reversalpreview,
                          v2corrections, correct_table_message, report_table_message, v2publishretry, v2unstrike, v2mmr, v2mystrikes, v2fixroles, v2setname],
                method="individual",
                guild_ids=[LAB_GUILD_ID],
                delete_existing=True,
            )
        except discord.HTTPException as error:
            print(f"Could not register /v2status in the lab (HTTP {error.status}).")
            return 1

        for table_id in publisher.pending_retractions():
            try:await publisher.retract(table_id)
            except Exception as error:print(f'Table #{table_id} retraction pending ({type(error).__name__}).')
        for table_id in publisher.pending():
            try:await publisher.publish(table_id)
            except Exception as error:print(f'Table #{table_id} publication pending ({type(error).__name__}); staff can use /v2publishretry.')

        print(f"{'Production' if production else 'Lab'} bot ready as {bot.user}. Try /{command_name('v2status')} in #{channel.name}; Ctrl+C stops it.")
        async def maintenance():
            while True:
                await asyncio.sleep(60)
                if members:
                    try:await members.refresh_restrictions()
                    except Exception as error:print(f'Member synchronization pending ({type(error).__name__}).')
        maintenance_task=asyncio.create_task(maintenance())
        try:await connection
        finally:
            maintenance_task.cancel()
            with suppress(asyncio.CancelledError):await maintenance_task
        return 0
    finally:
        ready.cancel()
        with suppress(asyncio.CancelledError):
            await ready
        if not connection.done():
            connection.cancel()
            with suppress(asyncio.CancelledError):
                await connection
        await bot.close()


def main() -> int:
    token = getpass.getpass("Development bot token (hidden): ")
    if not token:
        print("A development bot token is required.")
        return 2

    try:
        return asyncio.run(serve_lab(token))
    except discord.LoginFailure:
        print("Discord rejected the development bot token.")
        return 2
    except KeyboardInterrupt:
        print("Lab bot stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
