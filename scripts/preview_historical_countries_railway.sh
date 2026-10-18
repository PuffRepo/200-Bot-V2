#!/bin/sh
# Read-only preview only. Paste this whole block into the existing Railway SSH shell.
cd /app || exit 1
/app/.venv/bin/python - <<'PY'
import hashlib, json, re, sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from v2.countries import country_code, player_countries

root = Path('/app')
database = Path('/app/private/production.sqlite3')
archives = {
    5: ('13aafead7e4650693f902051e05a45bde7247c42708beaf7773736a56f447a03', 1709),
    6: ('bddadf70fb48e75d04024580e8cb61a468318d883d04b3e823588ae5f2251bc3', 2627),
    7: ('d6151658f1adb4ccb9c159c44ea8b583de7e918ad7f68c6d0f21d65c0f6f374a', 3786),
}
seen = defaultdict(set)
for season, (expected, count) in archives.items():
    raw = (root / 'sql' / f's{season}200lounge-export.sql').read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise SystemExit('Archive differs from reviewed source; stopped without changes.')
    inserts = '\n'.join(line for line in raw.decode('utf-8-sig').splitlines() if line.startswith('INSERT INTO `player` VALUES '))
    pairs = re.findall(r"\((\d+),'(?:\\.|''|[^'\\])*',(?:NULL|\d+),'([^']*)',", inserts)
    if len(pairs) != count:
        raise SystemExit('Archive row count differs; stopped without changes.')
    for uid, code in pairs:
        seen[uid].add(country_code(code))
candidates = {uid: next(iter(codes)) for uid, codes in seen.items() if len(codes) == 1 and '' not in codes}
with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as db:
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    players = {str(uid): name for uid, name in db.execute('SELECT user_id,name FROM players')}
    protected = {str(row[0]) for row in db.execute('SELECT user_id FROM player_countries')}
    current = player_countries(db)
    rows = []
    for uid, code in sorted(candidates.items(), key=lambda item: int(item[0])):
        before = current.get(int(uid), '')
        status = ('unknown_player' if uid not in players else 'protected_staff_record' if uid in protected else 'unchanged' if before == code else 'conflicting_country' if before else 'fill_missing')
        rows.append(dict(discord_id=uid, name=players.get(uid), before=before, country_code=code, status=status))
    plan = dict(schema_version=1, rows=rows)
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    print(json.dumps(dict(mode='read_only_database_preview', current_players=len(players), candidate_status_counts=dict(Counter(row['status'] for row in rows)), digest=digest), indent=2))
PY
