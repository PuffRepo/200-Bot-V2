"""Read the imported registry and live Discord roles; write a preview only."""
import argparse,asyncio,getpass,json,os,sqlite3,sys
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from v2.role_transition_preview import build_preview


def read_registry(path,config):
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        manifest=db.execute('SELECT guild_id,player_count,role_transition_applied FROM registry_import_manifest WHERE id=1').fetchone()
        if not manifest or str(manifest[0])!=config['guild_id'] or manifest[2]!=0:raise ValueError('Wrong guild, missing manifest, or transition already marked complete.')
        if db.execute('SELECT COUNT(*) FROM tables').fetchone()[0]:raise ValueError('Database contains tables; review before season transition.')
        if db.execute("SELECT value FROM lab_settings WHERE key='current_season'").fetchone()[0]!='S9':raise ValueError('Database season is not S9.')
        players=[]
        for uid,name,source in db.execute('SELECT p.user_id,p.name,a.source_json FROM players p JOIN registry_import_audit a ON a.user_id=p.user_id ORDER BY p.user_id'):
            row=json.loads(source)
            if str(uid)!=row['discord_id'] or name!=row['name']:raise ValueError('Registry differs from import audit; review before transition.')
            players.append(row)
        count=db.execute('SELECT COUNT(*) FROM players').fetchone()[0]
        if len(players)!=count or count!=manifest[1] or count!=1890:raise ValueError('Registry count/audit mismatch.')
        return players


async def collect(config,token):
    import discord
    intents=discord.Intents.none();intents.members=True
    client=discord.Client(intents=intents)
    try:
        # REST login only: no gateway connection, command registration or runtime startup.
        await client.login(token)
        app=await client.application_info()
        if str(app.id)!=config['application_id']:raise ValueError('Token belongs to the wrong application.')
        guild=await client.fetch_guild(int(config['guild_id']))
        roles={str(r.id):r for r in await guild.fetch_roles()}
        me=await guild.fetch_member(client.user.id)
        me_ids={str(r.id) for r in me.roles}
        top=max(roles[i] for i in me_ids if i in roles)
        required=set(config['rank_role_ids'])|{config['placement_role_id']}|set(config['restriction_role_ids'])
        for rid in required:
            role=roles.get(rid)
            if role is None or role.managed or role.is_default() or role>=top:raise ValueError(f'Role {rid} is missing, managed or not below bot role.')
        permissions=0
        for rid in me_ids|{str(guild.id)}:
            if rid in roles:permissions|=roles[rid].permissions.value
        perms=discord.Permissions(permissions)
        if not (perms.manage_roles or perms.administrator):raise ValueError('Bot lacks Manage Roles.')
        members={}
        async for member in guild.fetch_members(limit=None):
            members[str(member.id)]={'role_ids':[str(r.id) for r in member.roles],'bot':member.bot}
        if not members or str(client.user.id) not in members:raise ValueError('Incomplete member collection.')
        return members,{rid:role.name for rid,role in roles.items()}
    finally:
        await client.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=Path('private/production.sqlite3'))
    parser.add_argument('--config',type=Path,default=ROOT/'settings/s9-role-transition.json')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    try:
        config=json.loads(args.config.read_text(encoding='utf-8'))
        players=read_registry(args.database,config)
        out=args.output or Path('private')/datetime.now().strftime('role-transition-preview-%Y%m%d-%H%M%S')
        if out.exists():raise ValueError('Output folder exists; choose a new --output folder.')
        token=os.environ.get('DISCORD_BOT_TOKEN') or getpass.getpass('Bot token (hidden; do not paste into chat): ')
        members,roles=asyncio.run(collect(config,token.strip()))
        plan=build_preview(players,members,config)
        plan['captured_at']=datetime.now(timezone.utc).isoformat();plan['role_names']=roles
        out.mkdir(parents=True,exist_ok=False)
        (out/'role-transition-preview.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
        (out/'summary.json').write_text(json.dumps({k:v for k,v in plan.items() if k not in ('rows','role_names')},ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(plan['summary'],indent=2));print(f'Saved to {out}. Preview only: no Discord or database changes.')
        return 0
    except Exception as error:
        # Never echo credentials or request headers.
        if isinstance(error,(ValueError,sqlite3.Error,FileNotFoundError)):print(f'Preview stopped: {error}')
        else:print(f'Preview stopped: {type(error).__name__}. Check token, server access, and Server Members Intent.')
        return 2
if __name__=='__main__':raise SystemExit(main())
