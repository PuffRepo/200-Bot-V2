"""Read-only current-member inventory and conservative MKCentral log candidates.
No commands registered, messages sent, roles edited or database imported.
"""
import argparse
import asyncio
import csv
import getpass
import json
import re
from collections import defaultdict
from contextlib import suppress
from pathlib import Path

RANKS={'iron','bronze','silver','gold','platinum','emerald','ruby','diamond','master','grandmaster'}
MENTION=re.compile(r'<@!?(\d{15,22})>')
PROFILE=re.compile(r'https?://(?:www\.)?mkcentral\.com/registry/players/profile\?id=(\d+)(?!\d)',re.I)


def candidate(record):
    # Never treat a log author, display-name match or unlabelled number as the player.
    text=record['content']+'\n'+'\n'.join(json.dumps(e,ensure_ascii=False) for e in record['embeds'])
    users=set(MENTION.findall(text));profiles=set(PROFILE.findall(text))
    if len(users)==1 and len(profiles)==1:
        return {'discord_id':next(iter(users)),'mkc_id':next(iter(profiles)),
                'source':record['url'],'status':'candidate_requires_staff_review'}
    return None


def reference(members,links):
    by_user=defaultdict(set);by_mkc=defaultdict(set)
    for link in links:
        by_user[link['discord_id']].add(link['mkc_id'])
        by_mkc[link['mkc_id']].add(link['discord_id'])
    rows=[]
    for member in members:
        ids=sorted(by_user[member['discord_id']]);issues=[]
        if len(member['rank_roles'])>1:issues.append('multiple_rank_roles')
        if member['rank_roles'] and member['placement_roles']:issues.append('rank_and_placement_roles')
        if len(ids)>1:issues.append('multiple_mkc_candidates')
        if any(len(by_mkc[i])>1 for i in ids):issues.append('mkc_linked_to_multiple_discord_accounts')
        rows.append({**member,'mkc_candidates':ids,'registration_reference':
                     'rank_role_existing_player' if member['rank_roles'] else
                     'placement_member_needs_review' if member['placement_roles'] else 'no_registration_role',
                     'link_status':'missing' if not ids else 'conflict' if issues else 'candidate_requires_staff_review',
                     'issues':issues})
    return rows


def write_csv(path,rows,fields):
    # Prevent spreadsheet formula execution from user-controlled names/log data.
    def cell(value):
        if isinstance(value,(list,dict)):value=json.dumps(value,ensure_ascii=False)
        text=str(value)
        return "'"+text if text.startswith(('=','+','-','@','\t','\r','\n')) else text
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for row in rows:writer.writerow({k:cell(row.get(k,'')) for k in fields})


async def export(args,token):
    import discord
    intents=discord.Intents.none();intents.guilds=True;intents.members=True;intents.message_content=True
    client=discord.Client(intents=intents)
    connection=asyncio.create_task(client.start(token,reconnect=False))
    ready=asyncio.create_task(client.wait_until_ready())
    try:
        done,_=await asyncio.wait((connection,ready),timeout=60,return_when=asyncio.FIRST_COMPLETED)
        if connection in done:connection.result();raise RuntimeError('Disconnected before ready')
        if ready not in done:raise RuntimeError('Timed out waiting for Discord')
        app=await client.application_info()
        if app.id!=args.application_id:raise RuntimeError('Token belongs to a different application')
        guild=client.get_guild(args.guild_id)
        if guild is None:raise RuntimeError('Bot is not installed in selected guild')
        channel=await client.fetch_channel(args.logs_channel_id)
        if not isinstance(channel,discord.TextChannel) or channel.guild.id!=guild.id:
            raise RuntimeError('Logs channel is not a text channel in selected guild')
        perms=channel.permissions_for(guild.me)
        if not perms.view_channel or not perms.read_message_history:
            raise RuntimeError('Bot needs View Channel and Read Message History in bot-logs')
        rank_ids=set(args.rank_role_id or [r.id for r in guild.roles if r.name.strip().casefold() in RANKS])
        placement_ids=set(args.placement_role_id or [r.id for r in guild.roles if r.name.strip().casefold() in {'placement','placements'}])
        if not rank_ids:raise RuntimeError('No rank roles recognized; supply --rank-role-id for actual rank roles')
        if any(guild.get_role(i) is None for i in rank_ids|placement_ids):raise RuntimeError('Configured role ID missing in guild')
        if rank_ids&placement_ids:raise RuntimeError('Rank and Placement role IDs must differ')
        members=[]
        async for m in guild.fetch_members(limit=None):
            if m.bot:continue
            members.append({'discord_id':str(m.id),'username':m.name,'display_name':m.display_name,
                            'rank_roles':[r.name for r in m.roles if r.id in rank_ids],
                            'placement_roles':[r.name for r in m.roles if r.id in placement_ids],
                            'all_role_ids':[str(r.id) for r in m.roles if r.id!=guild.id]})
        if not members:raise RuntimeError('No human members returned; check Server Members Intent')
        args.output.mkdir(parents=True,exist_ok=False)
        links=[];count=0;blank=0
        # Raw log archive permits later parser improvements without fetching again.
        with (args.output/'bot-logs.jsonl').open('w',encoding='utf-8') as stream:
            async for msg in channel.history(limit=None,oldest_first=True):
                record={'message_id':str(msg.id),'author_id':str(msg.author.id),
                        'created_at':msg.created_at.isoformat(),'content':msg.content,
                        'embeds':[e.to_dict() for e in msg.embeds],
                        'attachment_urls':[a.url for a in msg.attachments],
                        'url':f'https://discord.com/channels/{guild.id}/{channel.id}/{msg.id}'}
                stream.write(json.dumps(record,ensure_ascii=False)+'\n');count+=1
                if not msg.content and not msg.embeds:blank+=1
                link=candidate(record)
                if link:links.append(link)
                if count%1000==0:print(f'Read {count} log messages...',flush=True)
        rows=reference(members,links)
        (args.output/'members-reference.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
        write_csv(args.output/'members-reference.csv',rows,list(rows[0]))
        write_csv(args.output/'mkc-link-candidates.csv',links,['discord_id','mkc_id','source','status'])
        summary={'complete':True,'guild_id':str(guild.id),'channel_id':str(channel.id),
                 'human_members':len(rows),'ranked_members':sum(bool(r['rank_roles']) for r in rows),
                 'placement_members':sum(bool(r['placement_roles']) for r in rows),
                 'members_with_mkc_candidates':sum(bool(r['mkc_candidates']) for r in rows),
                 'log_messages':count,'messages_without_text_or_embeds':blank,
                 'recognized_roles':[{'id':str(i),'name':guild.get_role(i).name,'kind':'rank' if i in rank_ids else 'placement'} for i in sorted(rank_ids|placement_ids)],
                 'all_server_roles':[{'id':str(r.id),'name':r.name} for r in guild.roles],
                 'limitations':['Rank-role eligibility requires staff acceptance.','All MKC links are candidates, not automatically approved registrations.',
                                'Logs may omit earlier registrations; names are not matched and attachments are not OCRed.',
                                'Former members can appear in link candidates but are excluded from the current roster.']}
        (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        return summary
    finally:
        ready.cancel();connection.cancel()
        for task in (ready,connection):
            with suppress(asyncio.CancelledError,Exception):await task
        await client.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--application-id',type=int,default=1553807544013684807)
    parser.add_argument('--guild-id',type=int,default=461383953937596416)
    parser.add_argument('--logs-channel-id',type=int,default=1451957717072744629)
    parser.add_argument('--rank-role-id',type=int,action='append')
    parser.add_argument('--placement-role-id',type=int,action='append')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('Use a new output directory to preserve earlier exports')
    try:
        token=getpass.getpass('V2 Dev bot token (hidden): ')
        if not token:raise ValueError('Token is required')
        summary=asyncio.run(export(args,token))
    except Exception as error:
        print(f'Export failed: {error}. Any partial output is incomplete; use a new folder on retry.')
        return 2
    print(f"Export complete: {summary['human_members']} members; {summary['ranked_members']} ranked; {summary['members_with_mkc_candidates']} with candidate links. Output: {args.output}")
    return 0

if __name__=='__main__':raise SystemExit(main())
