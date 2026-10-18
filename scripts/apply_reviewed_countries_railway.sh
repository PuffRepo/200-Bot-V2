#!/bin/sh
# Apply only the approved country batch. Paste the whole block into the existing Railway SSH shell.
cd /app || exit 1
/app/.venv/bin/python - <<'PY'
import hashlib, json, re, sqlite3
from collections import defaultdict
from pathlib import Path
from v2.countries import country_code, player_countries

ACTOR_DISCORD_ID = '388071499770232833'  # Must be the approving staff member's real Discord ID.
REVIEWED_DIGEST = '6399307e5a458a1f5a9bbefac2fefd8a6355f5e3f5183fd53af6c52926ac3c78'
EXPECTED_UPDATES = 1685
REASON = 'Historical country restoration from reviewed S5/S6/S7 archives; exact Discord ID matches; conflicting sources excluded; batch ' + REVIEWED_DIGEST
if not ACTOR_DISCORD_ID.isascii() or not ACTOR_DISCORD_ID.isdecimal() or not 0 < int(ACTOR_DISCORD_ID) < 2**63:
    raise SystemExit('A real staff Discord user ID is required; no changes made.')
actor_id = int(ACTOR_DISCORD_ID)
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
with sqlite3.connect(database.resolve().as_uri() + '?mode=rw', uri=True, timeout=10) as db:
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('BEGIN IMMEDIATE')
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
    if digest != REVIEWED_DIGEST:
        raise SystemExit('Country preview changed; no changes made. Run a fresh read-only preview.')
    eligible = [row for row in rows if row['status'] == 'fill_missing']
    if len(eligible) != EXPECTED_UPDATES:
        raise SystemExit('Eligible batch size differs from approval; no changes made.')
    for row in eligible:
        uid, code = int(row['discord_id']), row['country_code']
        db.execute('INSERT INTO player_countries(user_id,country_code,actor_id,reason) VALUES (?,?,?,?)',
                   (uid, code, actor_id, REASON))
        db.execute('INSERT INTO player_country_audit(user_id,before_code,after_code,actor_id,reason) VALUES (?,?,?,?,?)',
                   (uid, row['before'], code, actor_id, REASON))
    updated = len(eligible)
    registered = len(players)
    known = sum(bool(code) for uid, code in player_countries(db).items() if str(uid) in players)
# Print success only after the transaction context has committed.
print(json.dumps(dict(mode='country_batch_applied', countries_updated=updated,
                      current_players=registered, with_country=known,
                      missing_country=registered-known, actor_id=str(actor_id),
                      reviewed_digest=REVIEWED_DIGEST), indent=2))
PY
