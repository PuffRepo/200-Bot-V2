"""Explicit per-guild configuration; no tokens or fixture data in deployment JSON."""
import json
from pathlib import Path
from v2.lab_store import LabError
from v2.season_policy import load_policy
from v2.lab_ratings import POLICY_PATH

from v2.launch_scope import ACTIVE_TIERS

LAB_GUILD=1553806194257432726

def snowflake(value,label):
    if isinstance(value,bool) or not str(value).isdecimal() or int(value)<1:
        raise LabError(f'{label} must contain a positive Discord ID.')
    return int(value)

def load_runtime(path,mode='production',require_enabled=True):
    if mode not in ('lab','production'):raise LabError('Unknown runtime mode.')
    config=json.loads(Path(path).read_text(encoding='utf-8'))
    if require_enabled and config.get('enabled') is not True:raise LabError('Configuration is disabled; set enabled true only for the intended test/deployment.')
    config['guild_id']=snowflake(config.get('guild_id'),'guild_id')
    config['application_id']=snowflake(config.get('application_id'),'application_id')
    if mode=='lab' and config['guild_id']!=LAB_GUILD:raise LabError('Lab member configuration must target the lab guild.')
    if mode=='production' and config['guild_id']==LAB_GUILD:raise LabError('Production configuration cannot target the lab guild.')
    if config.get('season')!='S9':raise LabError('This release requires the approved S9 policy.')
    if config.get('policy_file')!='settings/s9-policy.json':raise LabError('Use the approved settings/s9-policy.json policy.')
    policy=load_policy(POLICY_PATH)
    roles=config.get('roles',{})
    config['roles']=roles
    config.setdefault('channels',{})
    for key in ('reporter','placement','loungeless','chat_restricted'):
        roles[key]=snowflake(roles.get(key),f'roles.{key}')
    roles['staff']=[snowflake(v,'roles.staff') for v in roles.get('staff',[])]
    if not roles['staff']:raise LabError('At least one staff role must be configured.')
    roles['verified_optional']=snowflake(roles['verified_optional'],'roles.verified_optional') if roles.get('verified_optional') else None
    ranks=roles.get('ranks',{})
    if set(ranks)!={r['name'] for r in policy['ranks']}:raise LabError('Configure exactly the approved ten rank roles.')
    roles['ranks']={k:snowflake(v,f'roles.ranks.{k}') for k,v in ranks.items()}
    managed=[roles['placement'],roles['loungeless'],roles['chat_restricted'],*roles['ranks'].values()]
    if roles['verified_optional']:managed.append(roles['verified_optional'])
    if len(managed)!=len(set(managed)):raise LabError('Managed rank/restriction role IDs must be distinct.')
    if any(r in managed for r in [roles['reporter'],*roles['staff']]):raise LabError('Staff/Reporter roles cannot double as managed rank/restriction roles.')
    for key in ('debug','verification_requests','verification_log','name_requests','strikes_log','support'):
        config['channels'][key]=snowflake(config.get('channels',{}).get(key),f'channels.{key}')
    tier_names=set(config.get('tiers',{}))
    if (mode=='production' and tier_names!=set(ACTIVE_TIERS)) or (mode=='lab' and (not tier_names or not tier_names<=set(ACTIVE_TIERS))):
        raise LabError('Production requires All/C/B/A; lab accepts a nonempty subset. Tier S and squad queue are disabled.')
    submissions=[]
    for name,entry in config['tiers'].items():
        for key in ('submission_channel_id','results_channel_id'):entry[key]=snowflake(entry.get(key),f'tiers.{name}.{key}')
        submissions.append(entry['submission_channel_id'])
    if mode=='production' and len(submissions)!=len(set(submissions)):raise LabError('Production tier submission channels must be distinct.')
    if mode=='production':
        results=[v['results_channel_id'] for v in config['tiers'].values()]
        if len(results)!=len(set(results)) or set(results)&set(submissions):
            raise LabError('Production results channels must be distinct and separate from submission channels.')
    database=Path(config.get('database_path',''))
    if not str(database) or (mode=='production' and database.name=='lab-v2.sqlite3') or database.suffix not in ('.sqlite3','.sqlite'):
        raise LabError('Use a dedicated .sqlite3/.sqlite database, not lab-v2.sqlite3.')
    config['database_path']=str(database)
    # Restore the inherited defaults; exceptions belong in a separately approved policy change.
    config['legacy_policy']={'strike_expiry_days':30,'strike_limit':3,'restriction_days_per_repeat_offense':7,'unstrike_hours':24,'name_change_cooldown_days':60}
    config['mode']=mode
    return config
