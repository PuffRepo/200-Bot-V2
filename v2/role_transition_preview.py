"""Pure S9 role-transition preview. No Discord writes or database mutations."""

def build_preview(players, members, config):
    ranks=set(config['rank_role_ids']);placement=config['placement_role_id']
    restricted=set(config['restriction_role_ids']);corbin=config['reinstated_user_id']
    rows=[]
    for player in players:
        uid=str(player['discord_id']);member=members.get(uid)
        row={'discord_id':uid,'registry_name':player['name'],'add_role_ids':[], 'remove_role_ids':[], 'preserve_role_ids':[], 'notes':[]}
        if member is None:
            row['status']='absent';row['notes'].append('Not in current member list; no changes proposed.')
        else:
            current=set(member['role_ids']);remove=current&ranks
            if member.get('bot'):raise ValueError('Registered player is a bot; review registry.')
            if uid==corbin:
                approved=set((player.get('staff_decision') or {}).get('remove_role_ids_on_transition',[]))
                if approved!=restricted:raise ValueError('Corbin reinstatement audit does not match configured restriction roles.')
                remove|=current&approved
            remaining_restrictions=(current&restricted)-remove
            row['preserve_role_ids']=sorted(current-remove,key=int)
            if remaining_restrictions:
                row['status']='review';row['notes'].append('Restriction remains: staff review required; no changes proposed.')
                row['preserve_role_ids']=sorted(current,key=int)
            else:
                row['remove_role_ids']=sorted(remove,key=int)
                row['add_role_ids']=[] if placement in current else [placement]
                row['status']='change' if remove or row['add_role_ids'] else 'unchanged'
            if (player.get('staff_decision') or {}).get('name_resolution'):
                row['notes'].append('Temporary registry name; staff to request formal name change on return. No nickname edit in this transition.')
        rows.append(row)
    ids={r['discord_id'] for r in rows}
    outside=[uid for uid,m in members.items() if uid not in ids and not m.get('bot') and set(m['role_ids'])&(ranks|{placement})]
    return {'mode':'preview_only','guild_id':config['guild_id'],'rows':rows,
            'outside_registry_role_holders':sorted(outside,key=int),
            'summary':{**{s:sum(r['status']==s for r in rows) for s in ('change','unchanged','absent','review')},
                       'registered_players':len(rows),'outside_registry_role_holders':len(outside),
                       'placement_additions':sum(bool(r['add_role_ids']) for r in rows),
                       'rank_removals':sum(len(set(r['remove_role_ids'])&ranks) for r in rows)},
            'discord_changes_applied':False,'production_launch_unlocked':False}
