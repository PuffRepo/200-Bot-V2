"""Configurable season rules and a read-only lab transition inventory."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def load_policy(path: Path) -> dict:
    policy = json.loads(Path(path).read_text(encoding='utf-8'))
    if policy.get('rating_reset') not in ('pending', 'full', 'median'):
        raise ValueError('Rating reset must be pending, full, or median.')
    if type(policy.get('strike_reset')) is not bool:
        raise ValueError('Strike reset must be a boolean.')
    if 'unplaced_mmr' in policy:
        if type(policy['unplaced_mmr']) is not int or not 1<=policy['unplaced_mmr']<=6000:
            raise ValueError('Temporary unplaced MMR must be 1–6000.')
        if policy.get('rating_scale')!=10999 or policy.get('clamp_negative_base') is not True:
            raise ValueError('This lab build requires the 10999 scale and negative-base clamp.')
        if policy.get('placement_table_delta') is not False or policy.get('mmr_floor')!=1:
            raise ValueError('This lab build requires placement-only first tables and floor 1.')
        for key in ('default','tier-c'):
            brackets=policy.get('placements',{}).get(key,[])
            if len(brackets)!=5 or brackets[0].get('minimum_score')!=0:
                raise ValueError('Five placement brackets starting at zero are required.')
            thresholds=[b.get('minimum_score') for b in brackets]
            values=[b.get('mmr') for b in brackets]
            if any(type(v) is not int or not 0<=v<=180 for v in thresholds) or thresholds!=sorted(set(thresholds)):
                raise ValueError('Placement cutoffs must increase within 0–180.')
            if values!=[1000,2250,3750,5250,6000]:
                raise ValueError('Placement starting values must follow the approved schema.')
    ranks = policy.get('ranks', [])
    if not ranks or ranks[0].get('minimum') != 0:
        raise ValueError('Rank ranges must start at zero.')
    names = set()
    previous = -1
    for rank in ranks:
        minimum, name = rank.get('minimum'), rank.get('name')
        if type(minimum) is not int or minimum <= previous:
            raise ValueError('Rank minimums must be increasing integers.')
        if not isinstance(name, str) or not name.strip() or name.casefold() in names:
            raise ValueError('Rank names must be nonempty and unique.')
        previous = minimum
        names.add(name.casefold())
    return policy


def rank_for_mmr(policy: dict, mmr: int | None) -> str:
    if mmr is None:
        return 'Placement'
    if type(mmr) is not int or mmr < 0:
        raise ValueError('MMR must be a nonnegative integer or None.')
    return next(rank['name'] for rank in reversed(policy['ranks']) if mmr >= rank['minimum'])


def preview_lab_transition(path: Path, policy: dict) -> dict:
    # Do not instantiate LabStore: its constructor creates/migrates schema.
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        players = db.execute('SELECT COUNT(*) FROM players').fetchone()[0]
        fixtures = db.execute('SELECT COUNT(*) FROM players WHERE user_id < 0').fetchone()[0]
        strikes = db.execute('SELECT COUNT(*) FROM strikes').fetchone()[0]
        columns = {row[1] for row in db.execute('PRAGMA table_info(strikes)')}
        season = 'LAB-INITIAL'
        if 'season' in columns:
            season = db.execute("SELECT value FROM lab_settings WHERE key='current_season'").fetchone()[0]
            active = db.execute("SELECT COUNT(*) FROM strikes WHERE season=? AND expires_at > datetime('now')", (season,)).fetchone()[0]
        else:
            active = db.execute("SELECT COUNT(*) FROM strikes WHERE expires_at > datetime('now')").fetchone()[0]
    return {
        'season': policy['season'], 'current_lab_strike_season': season, 'preview_only': True,
        'rating_reset': policy['rating_reset'],
        'placement_policy': policy['placement_policy'],
        'rank_status': policy['rank_status'],
        'registered_players': players, 'fixture_players': fixtures,
        'strike_history_rows_to_preserve': strikes,
        'currently_unexpired_strikes': active,
        'proposed_new_season_strikes': 0 if policy['strike_reset'] else active,
        'apply_ready': False,
        'limitations': [
            'Lab inventory only; no live member roster or authoritative live ratings.',
            'No database, Discord role, ban, or restriction changes are made.',
            'Lab strike seasons are supported; a production season transition is not implemented.',
            'S9 reset, placements, and Emerald ranks are approved; production migration and staff testing remain launch dependencies.'
        ],
    }
