"""Pure S9 lab rating calculations. No Discord or database access."""
import math
from v2.launch_scope import ACTIVE_TIERS
from v2.lab_store import LabError
from v2.season_policy import rank_for_mmr

PARAMETERS = {1:(63,19,2.1),2:(142,39,3.0000001),3:(288,59,3.1),
              4:(402,79,3.35),6:(525,99,3.5)}

def placement_mmr(policy, score, tier):
    if tier not in ACTIVE_TIERS:
        raise LabError('S9 tiers are All, C, B and A only.')
    brackets=policy['placements']['tier-c' if tier=='tier-c' else 'default']
    return next(b['mmr'] for b in reversed(brackets) if score>=b['minimum_score'])

def calculate_table(policy, players, format_size, tier):
    """players contain stable user_id, name, score, and pre-table mmr (or None)."""
    if format_size not in PARAMETERS or len(players)!=12:
        raise LabError('A supported format and 12 players are required.')
    if len({p['user_id'] for p in players})!=12:
        raise LabError('Duplicate player identity.')
    if any(type(p['score']) is not int or not 0<=p['score']<=180 for p in players) or sum(p['score'] for p in players)!=984:
        raise LabError('Scores must be 0–180 and total 984.')
    if any(p['mmr'] is not None and (type(p['mmr']) is not int or p['mmr']<1) for p in players):
        raise LabError('Established MMR must be at least 1.')
    special, other, power=PARAMETERS[format_size]
    scale=policy['rating_scale']
    teams=[]
    for offset in range(0,12,format_size):
        members=players[offset:offset+format_size]
        teams.append({'players':members,'score':sum(p['score'] for p in members),
                      'mmr':sum(policy['unplaced_mmr'] if p['mmr'] is None else p['mmr'] for p in members)/format_size})
    rows=[]
    for team in teams:
        raw=0
        for opponent in teams:
            if opponent is team: continue
            own,theirs=team['mmr'],opponent['mmr']
            if team['score']==opponent['score']:
                magnitude=special*((((own-theirs)/scale)**2)**(1/3))**2
                raw+=-magnitude if own>=theirs else magnitude
            else:
                win=team['score']>opponent['score']
                base=max(0,1+((theirs-own) if win else (own-theirs))/scale)
                magnitude=1+other*base**power
                raw+=magnitude if win else -magnitude
        delta=math.floor(raw)
        for p in team['players']:
            before=p['mmr']
            after=placement_mmr(policy,p['score'],tier) if before is None else max(policy['mmr_floor'],before+delta)
            rows.append({**p,'before':before,'after':after,'raw_delta':delta,
                         'applied_delta':None if before is None else after-before,
                         'kind':'placement' if before is None else 'table',
                         'rank':rank_for_mmr(policy,after)})
    return rows
