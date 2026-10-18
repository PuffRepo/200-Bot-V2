"""Read-only S9 statistics and an explicitly public website data contract."""
from collections import defaultdict
from datetime import datetime, timezone
import json
from statistics import mean

from v2.lab_store import LabError, parse_scores
from v2.season_policy import load_policy, rank_for_mmr
from v2.lab_ratings import POLICY_PATH


def snapshot(db):
    """Caller owns a read transaction. Never expose raw rows or audit metadata."""
    season = db.execute("SELECT value FROM lab_settings WHERE key='current_season'").fetchone()[0]
    if season != 'S9':
        raise LabError('S9 statistics require the active S9 season.')
    if db.execute('''SELECT 1 FROM tables t
        LEFT JOIN rating_applications a ON a.table_id=t.id
        LEFT JOIN application_sequence s ON s.table_id=t.id
        WHERE t.status='applied' AND t.season=? AND (a.table_id IS NULL OR s.table_id IS NULL)''', (season,)).fetchone():
        raise LabError('Applied table accounting is incomplete; statistics cannot safely be displayed.')
    policy = load_policy(POLICY_PATH)
    roster = db.execute('''SELECT p.user_id,p.name,p.mkc_id,r.mmr FROM players p
        LEFT JOIN season_ratings r ON r.user_id=p.user_id AND r.season=? ORDER BY p.user_id''', (season,)).fetchall()
    names = {r['user_id']: r['name'] for r in roster}
    histories = defaultdict(list)
    peaks = defaultdict(list)
    tables = []
    # Use the immutable application sequence, not submission timestamps or names.
    for table in db.execute('''SELECT t.id,t.scores,t.participant_ids,t.format_size,t.tier,a.created_at,s.sequence
        FROM tables t JOIN rating_applications a ON a.table_id=t.id
        JOIN application_sequence s ON s.table_id=t.id
        WHERE t.status='applied' AND t.season=? ORDER BY s.sequence''', (season,)).fetchall():
        pairs = parse_scores(table['scores'], table['format_size'])
        ids = json.loads(table['participant_ids'])
        events = {r['user_id']: r for r in db.execute('''SELECT * FROM effective_rating_events
            WHERE table_id=?''', (table['id'],))}
        if len(ids) != 12 or len(set(ids)) != 12 or set(events) != set(ids):
            raise LabError('Applied table participant ledger is inconsistent.')
        size = table['format_size']
        teams = [sum(score for _, score in pairs[i:i+size]) for i in range(0,12,size)]
        rows = []
        for index, (uid, (played_as, score)) in enumerate(zip(ids, pairs)):
            e = events[uid]
            team = index // size
            row = dict(user_id=str(uid), name=names[uid], played_as=played_as, score=score,
                       team=team, place=1+sum(s > teams[team] for s in teams),
                       before=e['before_mmr'], after=e['after_mmr'], kind=e['kind'],
                       original_delta=e['applied_delta'], delta=e['effective_delta'], refund=e['refund'])
            rows.append(row)
            partners = [s for j, (_, s) in enumerate(pairs) if j != index and j//size == team]
            histories[uid].append(dict(table_id=table['id'], sequence=table['sequence'],
                tier=table['tier'], format_size=size, applied_at=table['created_at'],
                partner_scores=partners, **row))
            peaks[uid].extend(v for v in (e['before_mmr'], e['after_mmr']) if v is not None)
        tables.append(dict(id=table['id'], sequence=table['sequence'], tier=table['tier'],
                           format_size=size, applied_at=table['created_at'], rows=rows))
    # Adjustment values are public accounting; reasons, actors and strike data are not.
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='balance_adjustments'").fetchone():
        for row in db.execute('SELECT user_id,before_mmr,after_mmr FROM balance_adjustments WHERE season=?', (season,)):
            peaks[row['user_id']].extend(v for v in (row['before_mmr'],row['after_mmr']) if v is not None)
    ranked = sorted((r['mmr'] for r in roster if r['mmr'] is not None), reverse=True)
    positions = {}
    for index, mmr in enumerate(ranked, 1):
        positions.setdefault(mmr, index)
    players = []
    for row in roster:
        uid, mmr = row['user_id'], row['mmr']
        player = dict(user_id=str(uid), name=row['name'], mkc_id=row['mkc_id'],
                      mmr=mmr, rank=rank_for_mmr(policy,mmr), position=positions.get(mmr),
                      peak_mmr=max([mmr]+peaks[uid]) if mmr is not None else None,
                      history=list(reversed(histories[uid])))
        player['summary'] = summarize(player['history'])
        players.append(player)
    return dict(schema_version=1, season=season,
                generated_at=datetime.now(timezone.utc).isoformat(),
                rank_policy=policy['ranks'], players=players, tables=tables)


def summarize(history):
    deltas = [r['delta'] for r in history if r['delta'] is not None]
    scores = [r['score'] for r in history]
    partners = [s for r in history for s in r['partner_scores']]
    recent = [r['delta'] for r in history[:10] if r['delta'] is not None]
    gains = sum(d > 0 for d in deltas)
    return dict(tables=len(history), rated_tables=len(deltas), placements=len(history)-len(deltas),
        gains=gains, losses=sum(d < 0 for d in deltas), ties=sum(d == 0 for d in deltas),
        gain_rate=100*gains/len(deltas) if deltas else None,
        original_delta=sum(r['original_delta'] or 0 for r in history),
        refunds=sum(r['refund'] for r in history), net_table_delta=sum(deltas),
        average_delta=mean(deltas) if deltas else None,
        average_score=mean(scores) if scores else None, top_score=max(scores) if scores else None,
        partner_average=mean(partners) if partners else None,
        largest_gain=max([0]+deltas), largest_loss=min([0]+deltas),
        last_ten=dict(gains=sum(d>0 for d in recent),losses=sum(d<0 for d in recent),
                      ties=sum(d==0 for d in recent),delta=sum(recent)))


def player_statistics(data, user_id, tier=None, format_size=None, last=None):
    if tier is not None and tier not in ('tier-all','tier-c','tier-b','tier-a'):
        raise LabError('Choose All, C, B or A. Squad queue and tier S are disabled for S9.')
    if format_size is not None and (type(format_size) is not int or format_size not in (1,2,3,4)):
        raise LabError('Choose FFA, 2v2, 3v3 or 4v4.')
    if last is not None and (type(last) is not int or not 1 <= last <= 1000):
        raise LabError('Last must be between 1 and 1000 tables.')
    player = next((p for p in data['players'] if p['user_id']==str(user_id)), None)
    if player is None:
        raise LabError('Player is not registered.')
    history = [r for r in player['history'] if (tier is None or r['tier']==tier)
               and (format_size is None or r['format_size']==format_size)]
    if last is not None:
        history = history[:last]
    return dict(player=player, history=history, summary=summarize(history), season=data['season'])


def read_player_statistics(store, user_id, **filters):
    with store.connect() as db:
        db.execute('BEGIN')
        return player_statistics(snapshot(db),user_id,**filters)
