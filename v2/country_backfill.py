"""Fill missing flags for existing, exact MKC links; never resolve identities."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import time
from urllib.error import HTTPError, URLError

from v2.countries import country_code, player_countries
from v2.mkc_country import profile_country, read_profile


def connect(database, mode):
    db = sqlite3.connect(Path(database).resolve().as_uri()+f'?mode={mode}', uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def candidates(database, limit=38):
    with closing(connect(database, 'ro')) as db:
        db.execute('BEGIN')
        countries = player_countries(db)
        protected = {r[0] for r in db.execute('SELECT user_id FROM player_countries')}
        rows = [dict(user_id=str(r['user_id']), mkc_id=r['mkc_id'])
                for r in db.execute('SELECT user_id,mkc_id FROM players ORDER BY user_id')
                if r['user_id'] > 0 and type(r['mkc_id']) is int and r['mkc_id'] > 0
                and not countries.get(r['user_id']) and r['user_id'] not in protected]
    if len(rows) > limit:
        raise ValueError(f'{len(rows)} candidates exceeds approved limit {limit}; inspect the scope first.')
    return rows


def make_plan(rows, fetch=read_profile, pause=time.sleep):
    updates, skipped = [], []
    for index, row in enumerate(rows):
        if index:
            pause(1)
        try:
            profile = fetch(row['mkc_id'])
            code = profile_country(row['mkc_id'], lambda _:profile)
        except HTTPError as error:
            if error.code == 429:
                skipped.extend(dict(**pending, reason='rate_limited') for pending in rows[index:])
                break
            code = ''
        except (URLError, OSError, ValueError, TypeError):
            code = ''
        if code:
            updates.append(dict(**row, country_code=code))
        else:
            skipped.append(dict(**row, reason='country_unavailable'))
    return dict(created_at=datetime.now(timezone.utc).isoformat(), candidates=len(rows),
                updates=updates, skipped=skipped)


def apply_plan(database, plan):
    updates = plan['updates']
    ids = [r['user_id'] for r in updates]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate players in backfill plan.')
    for r in updates:
        if (not isinstance(r['user_id'], str) or not r['user_id'].isdecimal()
                or not 0 < int(r['user_id']) < 2**63
                or type(r['mkc_id']) is not int or r['mkc_id'] < 1
                or not country_code(r['country_code'])):
            raise ValueError('Invalid player, MKC link or country in backfill plan.')
    changed, skipped = [], []
    with closing(connect(database, 'rw')) as db:
        with db:
            db.execute('BEGIN IMMEDIATE')
            countries = player_countries(db)
            protected = {r[0] for r in db.execute('SELECT user_id FROM player_countries')}
            for row in updates:
                uid, mkc, code = int(row['user_id']), row['mkc_id'], country_code(row['country_code'])
                player = db.execute('SELECT mkc_id FROM players WHERE user_id=?', (uid,)).fetchone()
                if not player or player['mkc_id'] != mkc or countries.get(uid) or uid in protected:
                    skipped.append(row['user_id'])
                    continue
                reason = f'MKC missing-flag backfill: https://mkcentral.com/registry/players/profile?id={mkc}'
                db.execute('INSERT INTO player_countries(user_id,country_code,actor_id,reason) VALUES (?,?,0,?)', (uid,code,reason))
                db.execute('INSERT INTO player_country_audit(user_id,before_code,after_code,actor_id,reason) VALUES (?,\'\',?,0,?)', (uid,code,reason))
                changed.append(row['user_id'])
    return dict(flags_added=len(changed), changed_players=changed, skipped_changed_since_lookup=skipped)
