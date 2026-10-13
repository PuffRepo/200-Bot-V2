"""Offline first-production roster import. Never connects to Discord."""
import hashlib
import json
import os
import tempfile
from pathlib import Path
from v2.lab_store import LabStore, LabError, player_name

GROUPS={'rank_carry_forward','verified_placement_carry_forward','approved_exception_carry_forward'}

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def make_plan(source, overrides=None):
    overrides=overrides or {}
    records=[r for r in source['members'] if r['migration_group'] in GROUPS]
    ids=[str(r['discord_id']) for r in records]
    if len(ids)!=len(set(ids)) or any(not i.isdecimal() or not 0<int(i)<2**63 for i in ids):
        raise LabError('Roster contains duplicate or invalid Discord IDs.')
    if set(overrides)-set(ids):raise LabError('Name overrides contain IDs outside the approved roster.')
    if len(ids)!=source['summary']['approved_carry_forward_total']:raise LabError('Approved roster count does not match source.')
    counts={}
    for r in records:
        n=r['display_name'];counts[n]=counts.get(n,0)+1
    # No automatic spelling changes, transliteration, trimming or case folding.
    for uid, name in overrides.items():
        original=next(r['display_name'] for r in records if str(r['discord_id'])==uid)
        if name != original and counts[original] < 2:
            raise LabError('Only exact duplicate names may be overridden during migration.')
    chosen={str(r['discord_id']):player_name(overrides.get(str(r['discord_id']),r['display_name'])) for r in records}
    by_name={}
    for uid,name in chosen.items():by_name.setdefault(name,[]).append(uid)
    duplicates=[{'name':name,'discord_ids':sorted(ids,key=int)} for name,ids in sorted(by_name.items()) if len(ids)>1]
    rows=[]
    for r in sorted(records,key=lambda x:int(x['discord_id'])):
        uid=str(r['discord_id']);original=r['display_name']
        rows.append({'discord_id':uid,'name':chosen[uid],'current_display_name':original,'name_changed':chosen[uid]!=original,
                     'mkc_id':None,'registration_basis':r['migration_group'],
                     'source_role_ids':r['all_role_ids'],'historical_registration_records':r['historical_registration_records'],
                     'staff_decision':r.get('staff_decision'), 'season':'S9','status':'unplaced'})
    plan={'schema_version':2,'guild_id':'461383953937596416','source_digest':digest(source),'name_overrides':overrides,
          'players':rows,'summary':{'players':len(rows),'exact_duplicate_groups':len(duplicates),'name_changes':sum(x['name_changed'] for x in rows),'unresolved_mkc_links':len(rows)},
          'exact_duplicates':duplicates,'discord_changes_applied':False}
    plan['digest']=digest(plan)
    return plan

def apply_plan(source, plan, target, accept_name_changes=False):
    target=Path(target)
    if plan!=make_plan(source,plan.get('name_overrides')):raise LabError('Plan is stale or edited; regenerate the preview.')
    if plan['exact_duplicates']:raise LabError('Exact duplicate names require a decision; inspect name-review.json before importing.')
    if plan['summary']['name_changes'] and not accept_name_changes:raise LabError('Review proposed names, then explicitly accept name changes.')
    if target.exists():raise LabError('Destination already exists. This importer only creates a fresh database; it never merges or overwrites.')
    if target.suffix not in ('.sqlite','.sqlite3') or 'lab' in target.name.lower():raise LabError('Choose a dedicated production .sqlite3 path, not a lab database.')
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.registry-import-',suffix='.sqlite3',dir=target.parent);os.close(fd)
    temp=Path(tmp)
    try:
        store=LabStore(temp)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE registry_import_audit (user_id INTEGER PRIMARY KEY,source_json TEXT NOT NULL,plan_digest TEXT NOT NULL,imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
            db.execute('CREATE TABLE registry_import_manifest (id INTEGER PRIMARY KEY CHECK(id=1),guild_id TEXT NOT NULL,source_digest TEXT NOT NULL,plan_digest TEXT NOT NULL,player_count INTEGER NOT NULL,role_transition_applied INTEGER NOT NULL DEFAULT 0)')
            for row in plan['players']:
                db.execute('INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES (?,NULL,?,0)',(int(row['discord_id']),row['name']))
                db.execute('INSERT INTO registry_import_audit(user_id,source_json,plan_digest) VALUES (?,?,?)',(int(row['discord_id']),json.dumps(row,ensure_ascii=False),plan['digest']))
            db.execute("INSERT OR IGNORE INTO lab_seasons(name,started_by) VALUES ('S9',0)")
            db.execute("UPDATE lab_settings SET value='S9' WHERE key='current_season'")
            db.execute('INSERT INTO registry_import_manifest(id,guild_id,source_digest,plan_digest,player_count) VALUES (1,?,?,?,?)',(plan['guild_id'],plan['source_digest'],plan['digest'],len(plan['players'])))
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise LabError('Database integrity check failed.')
            if db.execute('SELECT COUNT(*) FROM players').fetchone()[0]!=len(plan['players']):raise LabError('Import count mismatch.')
        # Atomic publication that refuses to overwrite even if another process created the destination.
        os.link(temp,target)
    finally:
        temp.unlink(missing_ok=True)
    return plan['summary']

def require_completed_transition(db, guild_id):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='registry_import_manifest'").fetchone():
        row=db.execute('SELECT guild_id,role_transition_applied FROM registry_import_manifest WHERE id=1').fetchone()
        if not row or str(row[0])!=str(guild_id):raise LabError('Imported registry belongs to a different guild or has no manifest.')
        if row[1]!=1:raise LabError('Registry imported, but the S9 role transition is pending. Do not launch production yet.')
