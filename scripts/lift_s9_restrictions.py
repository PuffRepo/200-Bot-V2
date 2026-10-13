"""Lift the S9-approved Loungeless/Restricted roles for the ten reviewed accounts."""
import argparse,asyncio,getpass,json,os,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.preview_s9_role_transition import read_registry

GUILD=461383953937596416
APPLICATION=1553807544013684807
ROLE_IDS={463868896743522304,845084987417559040}
APPROVED_IDS={282967594091151360,354290608585834506,769181274526056469,
              882918918547722251,989927800360292352,1109580294777995355,
              1192868149478035497,1277251108942647388,1292714004216549448,
              1401993550454984829}
REASON='S9 restriction reset approved by server operator, 2026-10-13'


def record(stream,entry):
    stream.write(json.dumps({'at':datetime.now(timezone.utc).isoformat(),**entry},ensure_ascii=False)+'\n')
    stream.flush();os.fsync(stream.fileno())


async def clear_member(member,roles,write,apply):
    if member.id not in APPROVED_IDS or member.guild.id!=GUILD or member.bot:
        raise ValueError('Account is outside the approved player scope.')
    if set(roles)!=ROLE_IDS:raise ValueError('Unexpected restriction role map.')
    before={r.id for r in member.roles}
    removals=sorted(before&ROLE_IDS)
    write({'event':'before','discord_id':str(member.id),'role_ids':[str(i) for i in sorted(before)],'remove_role_ids':[str(i) for i in removals]})
    if apply:
        for rid in removals:
            write({'event':'remove_requested','discord_id':str(member.id),'role_id':str(rid)})
            # One-role DELETE operations never replace the member's whole role list.
            await member.remove_roles(roles[rid],reason=REASON,atomic=True)
            write({'event':'remove_acknowledged','discord_id':str(member.id),'role_id':str(rid)})
    return removals


async def run(token,registry,apply,out):
    import discord
    client=discord.Client(intents=discord.Intents.none())
    with (out/'audit.jsonl').open('x',encoding='utf-8') as log:
        write=lambda entry:record(log,entry)
        write({'event':'start','mode':'apply' if apply else 'preview','reason':REASON,'approved_discord_ids':[str(i) for i in sorted(APPROVED_IDS)]})
        try:
            await client.login(token)
            app=await client.application_info()
            if app.id!=APPLICATION:raise ValueError('Token belongs to a different application.')
            guild=await client.fetch_guild(GUILD)
            all_roles={r.id:r for r in await guild.fetch_roles()}
            me=await guild.fetch_member(client.user.id)
            me_ids={r.id for r in me.roles}|{GUILD}
            top=max(all_roles[i] for i in me_ids if i in all_roles)
            bits=0
            for rid in me_ids:
                if rid in all_roles:bits|=all_roles[rid].permissions.value
            permissions=discord.Permissions(bits)
            if not (permissions.manage_roles or permissions.administrator):raise ValueError('Bot lacks Manage Roles.')
            roles={rid:all_roles[rid] for rid in ROLE_IDS if rid in all_roles}
            if set(roles)!=ROLE_IDS:raise ValueError('Restriction role missing; review server configuration.')
            if any(r.managed or r.is_default() or r>=top for r in roles.values()):raise ValueError('Restriction role cannot be managed by this bot.')
            summary={'mode':'apply' if apply else 'preview','approved_players':10,'members_changed':0,'roles_removed':0,'already_clear':0,'absent':0,'proposed_role_removals':0}
            for uid in sorted(APPROVED_IDS):
                try:member=await guild.fetch_member(uid)
                except discord.NotFound:
                    summary['absent']+=1;write({'event':'absent','discord_id':str(uid)});continue
                removals=await clear_member(member,roles,write,apply)
                summary['proposed_role_removals']+=len(removals)
                if apply:
                    fresh=await guild.fetch_member(uid)
                    remaining={r.id for r in fresh.roles}&ROLE_IDS
                    if remaining:raise ValueError(f'Restriction remains for {uid}; stop and review audit log.')
                    write({'event':'verified_clear','discord_id':str(uid),'role_ids':[str(r.id) for r in fresh.roles]})
                    summary['members_changed']+=bool(removals);summary['roles_removed']+=len(removals)
                if not removals:summary['already_clear']+=1
            write({'event':'completed','summary':summary})
            (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
            print(json.dumps(summary,indent=2))
        except Exception as error:
            write({'event':'stopped','error_type':type(error).__name__})
            raise
        finally:await client.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply',action='store_true',help='Remove the approved restriction roles live; otherwise preview only.')
    p.add_argument('--database',type=Path,default=Path('private/production.sqlite3'))
    a=p.parse_args()
    try:
        config=json.loads((ROOT/'settings/s9-role-transition.json').read_text())
        if int(config['guild_id'])!=GUILD or int(config['application_id'])!=APPLICATION:raise ValueError('Unexpected guild or application configuration.')
        registry=read_registry(a.database,config)
        if not APPROVED_IDS<={int(r['discord_id']) for r in registry}:raise ValueError('Approved account missing from imported registry.')
        out=Path('private')/('s9-restriction-reset-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        out.mkdir(parents=True,exist_ok=False)
        print(f'Audit folder: {out}')
        token=os.environ.get('DISCORD_BOT_TOKEN') or getpass.getpass('Bot token (hidden; never paste into chat): ')
        asyncio.run(run(token.strip(),registry,a.apply,out))
        print('Done. Database and rank roles unchanged. Keep production stopped until the rank transition is completed.')
        return 0
    except Exception as error:
        print(f'Stopped: {type(error).__name__}'+(f': {error}' if isinstance(error,ValueError) else '. Check access and the audit log.'))
        print('If applying, earlier removals may already have succeeded. Rerunning checks current roles and skips roles already absent.')
        return 2
if __name__=='__main__':raise SystemExit(main())
