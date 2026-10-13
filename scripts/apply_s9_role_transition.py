"""Apply only the reviewed S9 rank transition, with live checks and resumable role operations."""
import argparse,asyncio,getpass,json,os,sqlite3,sys
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.preview_s9_role_transition import read_registry
from v2.role_transition_apply import digest,validate_snapshot,check_population,apply_member,finish_database

PLAN_DIGEST='bd0537e546c0be7d7972a896cf3c1dd3cdd696741ce46ab45311d5ab00ffcae3'
CONFIG_DIGEST='8e8cad884a009ae747c2befba6628d1912becb7a2b5a5a3eb4210c3b98da672e'


def record(stream,entry):
    stream.write(json.dumps({'at':datetime.now(timezone.utc).isoformat(),**entry},ensure_ascii=False)+'\n')
    stream.flush();os.fsync(stream.fileno())


async def execute(token,plan,config,apply,write):
    import discord
    intents=discord.Intents.none();intents.members=True
    client=discord.Client(intents=intents)
    try:
        await client.login(token)
        app=await client.application_info()
        if str(app.id)!=config['application_id']:raise ValueError('Token belongs to a different application.')
        guild=await client.fetch_guild(int(config['guild_id']))
        roles={str(r.id):r for r in await guild.fetch_roles()}
        me=await guild.fetch_member(client.user.id)
        me_ids={str(r.id) for r in me.roles}|{str(guild.id)}
        top=max(roles[i] for i in me_ids if i in roles)
        bits=0
        for rid in me_ids:
            if rid in roles:bits|=roles[rid].permissions.value
        perms=discord.Permissions(bits)
        if not (perms.manage_roles or perms.administrator):raise ValueError('Bot lacks Manage Roles.')
        required=set(config['rank_role_ids'])|{config['placement_role_id']}
        for rid in required:
            role=roles.get(rid)
            if role is None or role.managed or role.is_default() or role>=top:raise ValueError(f'Role {rid} cannot be managed by this bot.')
        async def capture():
            members={}
            async for m in guild.fetch_members(limit=None):
                members[str(m.id)]={'role_ids':[str(r.id) for r in m.roles],'bot':m.bot}
            if str(client.user.id) not in members:raise ValueError('Incomplete member collection.')
            return members
        print('Reading current server membership and checking reviewed scope...',flush=True)
        members=await capture();check_population(plan,members,config)
        write({'event':'preflight_passed','member_count':len(members)})
        if not apply:
            result={'mode':'check_only','reviewed_summary':plan['summary'],'live_preflight_passed':True}
            print('Live checks passed. No roles or database records changed.',flush=True)
            return result
        targets=[r for r in plan['rows'] if r['status']=='change']
        added=removed=0
        for n,row in enumerate(targets,1):
            a,r=await apply_member(row,config,guild.fetch_member,roles,write)
            added+=a;removed+=r
            if n==1 or n%25==0 or n==len(targets):
                print(f'Checked {n}/{len(targets)} planned changes; added {added} Placement roles, removed {removed} current ranks.',flush=True)
        print('Verifying the complete server roster again...',flush=True)
        final=await capture();check_population(plan,final,config,complete=True)
        absent=[r['discord_id'] for r in plan['rows'] if r['status']=='absent']
        summary={'mode':'apply','registered_players':len(plan['rows']),
                 'verified_present_on_placement':len(plan['rows'])-len(absent),
                 'absent_registration_preserved':absent,'planned_change_members_verified':len(targets),
                 'placement_added_this_run':added,'ranks_removed_this_run':removed,
                 'final_live_verification_passed':True,'plan_digest':PLAN_DIGEST}
        write({'event':'final_live_verification_passed','summary':summary})
        return summary
    finally:await client.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply',action='store_true',help='Apply the reviewed role changes; default is a read-only live check.')
    p.add_argument('--database',type=Path,default=Path('private/production.sqlite3'))
    a=p.parse_args()
    try:
        config=json.loads((ROOT/'settings/s9-role-transition.json').read_text(encoding='utf-8'))
        plan=json.loads((ROOT/'private/s9-reviewed-role-transition.json').read_text(encoding='utf-8'))
        if digest(config)!=CONFIG_DIGEST or digest(plan)!=PLAN_DIGEST:raise ValueError('Reviewed plan/configuration changed. Use the original package unchanged.')
        # A repeated completed invocation never resets roles after play begins.
        with closing(sqlite3.connect(a.database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            marker=db.execute('SELECT role_transition_applied FROM registry_import_manifest WHERE id=1').fetchone()
            if marker and marker[0]==1:
                done=db.execute('SELECT plan_digest FROM s9_role_transition_audit WHERE id=1').fetchone()
                if not done or done[0]!=PLAN_DIGEST:raise ValueError('A different transition has been marked complete.')
                print('This reviewed transition is already complete. No changes made.');return 0
        players=read_registry(a.database,config);validate_snapshot(plan,config,players,PLAN_DIGEST)
        token=os.environ.get('DISCORD_BOT_TOKEN') or getpass.getpass('Bot token (hidden; never paste into chat): ')
        out=Path('private')/('s9-role-transition-run-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        out.mkdir(parents=True,exist_ok=False);print(f'Audit folder: {out}',flush=True)
        with (out/'audit.jsonl').open('x',encoding='utf-8') as log:
            write=lambda entry:record(log,entry)
            write({'event':'start','mode':'apply' if a.apply else 'check_only','plan_digest':PLAN_DIGEST})
            try:
                if a.apply:
                    # Hold the SQLite write reservation to exclude another writer/apply process.
                    # Until the final commit, startup remains blocked by the existing import marker.
                    with closing(sqlite3.connect(a.database.resolve().as_uri()+'?mode=rw',uri=True,timeout=1)) as db:
                        with db:
                            db.execute('BEGIN IMMEDIATE')
                            players=read_registry(a.database,config);validate_snapshot(plan,config,players,PLAN_DIGEST)
                            backup=out/'production-before-transition.sqlite3'
                            with closing(sqlite3.connect(a.database.resolve().as_uri()+'?mode=ro',uri=True)) as source:
                                with closing(sqlite3.connect(backup)) as destination:
                                    source.backup(destination)
                                    if destination.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Automatic backup integrity failed.')
                            write({'event':'backup_verified','path':str(backup)})
                            summary=asyncio.run(execute(token.strip(),plan,config,True,write))
                            finish_database(db,PLAN_DIGEST,summary)
                        summary['transition_recorded_complete']=True
                        write({'event':'database_committed','summary':summary})
                else:summary=asyncio.run(execute(token.strip(),plan,config,False,write))
                (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
                print(json.dumps(summary,indent=2));print('Keep production stopped; remaining launch checks are separate.')
            except BaseException as error:
                write({'event':'stopped','error_type':type(error).__name__});raise
        return 0
    except KeyboardInterrupt:
        print('Interrupted. Keep the audit folder; rerun the same command to recheck and resume.');return 2
    except Exception as error:
        detail=f': {error}' if isinstance(error,(ValueError,sqlite3.Error)) else '. Inspect the audit folder and report this error type.'
        print(f'Stopped: {type(error).__name__}{detail}')
        print('Earlier Discord changes may have succeeded. Do not restore an old database or manually set the completion marker.');return 2
if __name__=='__main__':raise SystemExit(main())
