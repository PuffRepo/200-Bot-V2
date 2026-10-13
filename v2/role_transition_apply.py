"""Guards and single-member operations for the reviewed S9 rank transition."""
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def validate_snapshot(plan, config, players, expected_digest):
    if digest(plan)!=expected_digest:
        raise ValueError('Preview is not the reviewed snapshot. Keep the original file unchanged.')
    if plan['guild_id']!=config['guild_id'] or plan['mode']!='preview_only' or plan['discord_changes_applied']:
        raise ValueError('Unexpected preview identity or mode.')
    rows=plan['rows'];by_id={r['discord_id']:r for r in rows}
    if len(by_id)!=len(rows) or set(by_id)!={str(p['discord_id']) for p in players}:
        raise ValueError('Preview and imported registry do not contain the same unique accounts.')
    if any(by_id[str(p['discord_id'])]['registry_name']!=p['name'] for p in players):
        raise ValueError('Registry names changed since preview.')
    if plan['summary']['review'] or plan['outside_registry_role_holders']:
        raise ValueError('Unresolved preview review items.')
    ranks=set(config['rank_role_ids']);placement=config['placement_role_id']
    if placement in ranks or ranks&set(config['restriction_role_ids']):raise ValueError('Overlapping role configuration.')
    for row in rows:
        if row['status'] not in ('change','unchanged','absent'):raise ValueError('Unresolved player status.')
        if not set(row['remove_role_ids'])<=ranks or not set(row['add_role_ids'])<={placement}:
            raise ValueError('Preview proposes changes outside rank/Placement scope.')
        if row['status']=='absent' and any(row[k] for k in ('add_role_ids','remove_role_ids','preserve_role_ids')):
            raise ValueError('Absent player has proposed role edits.')
    return by_id


def check_member(row, role_ids, config, complete=False):
    """Accept only the original state or safe partial progress toward the approved result."""
    ids=set(role_ids);ranks=set(config['rank_role_ids']);placement=config['placement_role_id']
    if ids&set(config['restriction_role_ids']):raise ValueError(f"New restriction for {row['discord_id']}; staff review required.")
    if row['status']=='absent':raise ValueError(f"Previously absent player {row['discord_id']} returned; fresh preview required.")
    original=set(row['remove_role_ids'])&ranks
    if not (ids&ranks)<=original:raise ValueError(f"Unexpected rank role for {row['discord_id']}; fresh preview required.")
    if complete:
        if ids&ranks or placement not in ids:raise ValueError(f"Transition incomplete for {row['discord_id']}.")
    else:
        was_placed=placement in row['preserve_role_ids']
        if (was_placed or (ids&ranks)!=original) and placement not in ids:
            raise ValueError(f"Placement/role state changed unexpectedly for {row['discord_id']}.")


def check_population(plan, members, config, complete=False):
    rows={r['discord_id']:r for r in plan['rows']}
    active=set(config['rank_role_ids'])|{config['placement_role_id']}
    for uid,m in members.items():
        if uid not in rows and not m.get('bot') and set(m['role_ids'])&active:
            raise ValueError(f'Rank/Placement holder {uid} is outside the registry; fresh preview required.')
    for uid,row in rows.items():
        member=members.get(uid)
        if row['status']=='absent' and member is None:continue
        if member is None:raise ValueError(f'Previously present member {uid} is absent; fresh preview required.')
        if member.get('bot'):raise ValueError('Registered account is now a bot.')
        check_member(row,member['role_ids'],config,complete)


async def apply_member(row, config, fetch, roles, write):
    uid=int(row['discord_id']);ranks=set(config['rank_role_ids']);placement=config['placement_role_id']
    async def current():
        m=await fetch(uid)
        if m.id!=uid or str(m.guild.id)!=config['guild_id'] or m.bot:raise ValueError('Unexpected member identity.')
        ids={str(r.id) for r in m.roles}
        check_member(row,ids,config)
        return m,ids
    member,before=await current()
    preserve=before-ranks-{placement}
    write({'event':'member_before','discord_id':str(uid),'role_ids':sorted(before,key=int)})
    added=0;removed=0
    if placement not in before:
        if placement not in row['add_role_ids']:raise ValueError('Placement addition was not in the reviewed plan.')
        write({'event':'add_requested','discord_id':str(uid),'role_id':placement})
        await member.add_roles(roles[placement],reason='Approved S9 full rank reset: Placement',atomic=True)
        added=1
        write({'event':'add_acknowledged','discord_id':str(uid),'role_id':placement})
        member,ids=await current()
        if placement not in ids:raise ValueError('Placement addition not observed; old ranks were not removed.')
    else:ids=before
    for rid in sorted(ids&ranks,key=int):
        write({'event':'remove_requested','discord_id':str(uid),'role_id':rid})
        await member.remove_roles(roles[rid],reason='Approved S9 full rank reset: remove current rank',atomic=True)
        removed+=1
        write({'event':'remove_acknowledged','discord_id':str(uid),'role_id':rid})
    member,after=await current()
    check_member(row,after,config,complete=True)
    if not preserve<=after:raise ValueError(f'Unrelated roles changed during update for {uid}; inspect audit before continuing.')
    write({'event':'member_verified','discord_id':str(uid),'role_ids':sorted(after,key=int)})
    return added,removed


def finish_database(db, plan_digest, summary):
    """Caller holds the write transaction; audit and launch marker commit together."""
    if db.execute('SELECT COUNT(*) FROM tables').fetchone()[0]:raise ValueError('Tables appeared during transition.')
    db.execute('CREATE TABLE IF NOT EXISTS s9_role_transition_audit (id INTEGER PRIMARY KEY CHECK(id=1),plan_digest TEXT NOT NULL,summary_json TEXT NOT NULL,completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
    db.execute('INSERT INTO s9_role_transition_audit(id,plan_digest,summary_json) VALUES (1,?,?)',(plan_digest,json.dumps(summary,ensure_ascii=False)))
    changed=db.execute('UPDATE registry_import_manifest SET role_transition_applied=1 WHERE id=1 AND role_transition_applied=0').rowcount
    if changed!=1:raise ValueError('Unexpected transition marker state.')
    if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Final database integrity check failed.')
