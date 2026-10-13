"""Export live Discord channels, roles and bot permissions through read-only REST calls."""
import argparse
import asyncio
import getpass
import json
from datetime import datetime, timezone
from pathlib import Path

PERMISSIONS=('view_channel','read_message_history','send_messages','embed_links','attach_files',
             'manage_roles','manage_nicknames','manage_channels','manage_guild','administrator')

async def export(args,token):
    import discord
    client=discord.Client(intents=discord.Intents.none())
    try:
        await client.login(token)
        application=await client.application_info()
        if application.id!=args.application_id:
            raise ValueError(f'Wrong application: expected {args.application_id}, got {application.id}')
        guild=await client.fetch_guild(args.guild_id)
        bot_member=await guild.fetch_member(client.user.id)
        channels=await guild.fetch_channels()
        def permissions(p):return {name:bool(getattr(p,name,False)) for name in PERMISSIONS}
        roles=[{'id':str(r.id),'name':r.name,'position':r.position,'color':str(r.color),
                'managed':r.managed,'bot_has_role':r in bot_member.roles,
                'below_bot_top_role':r<bot_member.top_role,
                'permissions':permissions(r.permissions)} for r in sorted(guild.roles,reverse=True)]
        channel_rows=[]
        for c in channels:
            overwrites=[]
            for target,overwrite in c.overwrites.items():
                allow,deny=overwrite.pair()
                overwrites.append({'target_id':str(target.id),
                                   'target_type':'role' if isinstance(target,discord.Role) else 'member',
                                   'allow':str(allow.value),'deny':str(deny.value)})
            channel_rows.append({'id':str(c.id),'name':c.name,'type':str(c.type),
                                 'category_id':str(c.category_id) if getattr(c,'category_id',None) else None,
                                 'position':c.position,'bot_permissions':permissions(c.permissions_for(bot_member)),
                                 'overwrites':overwrites})
        report={'complete':True,'exported_at':datetime.now(timezone.utc).isoformat(),
                'guild':{'id':str(guild.id),'name':guild.name},
                'application':{'id':str(application.id),'name':application.name},
                'bot':{'id':str(client.user.id),'name':str(client.user),
                       'top_role_id':str(bot_member.top_role.id),'top_role_name':bot_member.top_role.name,
                       'guild_permissions':permissions(bot_member.guild_permissions)},
                'roles':roles,'channels':channel_rows,
                'limitations':['Channel inventory excludes threads and message history.',
                               'Names/permissions do not establish which channels or staff roles should be used for S9.',
                               'Permissions are a snapshot; no message delivery or role-edit actions were tested.']}
        args.output.mkdir(parents=True,exist_ok=False)
        (args.output/'live-inventory.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        def clean(s):return str(s).replace('|','\\|').replace('\n',' ').replace('\r',' ')
        names={c['id']:c['name'] for c in channel_rows}
        lines=[f"# Live inventory: {clean(guild.name)}",'',f"Server: `{guild.id}` · Application: `{application.id}`",'',
               f"Bot top role: {clean(bot_member.top_role.name)} (`{bot_member.top_role.id}`)",'',
               '## Roles','', '| Name | ID | Position | Below bot | Managed |','|---|---|---:|---|---|']
        for r in roles:lines.append(f"| {clean(r['name'])} | `{r['id']}` | {r['position']} | {r['below_bot_top_role']} | {r['managed']} |")
        lines+=['','## Channels','', '| Name | ID | Type | Category | View | Read history | Send | Embeds | Files |','|---|---|---|---|---|---|---|---|---|']
        for c in channel_rows:
            p=c['bot_permissions'];lines.append(f"| {clean(c['name'])} | `{c['id']}` | {c['type']} | {clean(names.get(c['category_id'],''))} | {p['view_channel']} | {p['read_message_history']} | {p['send_messages']} | {p['embed_links']} | {p['attach_files']} |")
        lines+=['','This is an inventory, not an approved deployment config. Threads and message contents are excluded.']
        (args.output/'live-inventory.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
        return len(roles),len(channel_rows)
    finally:
        await client.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--guild-id',type=int,default=461383953937596416)
    parser.add_argument('--application-id',type=int,default=1553807544013684807)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('Choose a new output folder; existing exports are preserved')
    try:
        token=getpass.getpass('V2 Dev bot token (hidden): ')
        if not token:raise ValueError('Token is required')
        roles,channels=asyncio.run(export(args,token))
    except Exception as error:
        print(f'Inventory failed: {error}')
        return 2
    print(f'Complete: {roles} roles, {channels} channels. Saved to {args.output}')
    return 0

if __name__=='__main__':raise SystemExit(main())
