"""Public country metadata only; never infer a country from a name or locale."""
import json
from pathlib import Path

CODES = frozenset(json.loads(Path(__file__).with_name('country_codes.json').read_text()))


def country_code(value):
    value = value.strip().upper() if isinstance(value, str) else ''
    return value if value in CODES else ''


def player_countries(db):
    """Read current overrides, then unambiguous imported country_code fields.

    No schema writes: also works on the public feed's read-only connection and
    on older databases during rolling deployment. Raw audit JSON stays private.
    """
    result = {}
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'registry_import_audit' in tables:
        for uid, source in db.execute('SELECT user_id,source_json FROM registry_import_audit'):
            try:
                row = json.loads(source)
                records = row.get('historical_registration_records', [])
                values = {country_code(r.get('country_code')) for r in records if isinstance(r, dict)}
                values.add(country_code(row.get('country_code')))
                values.discard('')
                if len(values) == 1:
                    result[uid] = values.pop()
            except (ValueError, TypeError, AttributeError):
                continue
    if 'player_countries' in tables:
        for uid, code in db.execute('SELECT user_id,country_code FROM player_countries'):
            result[uid] = country_code(code)  # Explicit blank overrides old data too.
    return result


def set_country(store, user_id, code, actor_id, reason):
    from v2.lab_store import LabError
    if not isinstance(code, str) or (code != '-' and not country_code(code)):
        raise LabError('Use an ISO two-letter country code (for example JP), or - to clear it.')
    code = '' if code == '-' else country_code(code)
    reason = reason.strip()
    if not 3 <= len(reason) <= 500:
        raise LabError('Record a country source/reason of 3–500 characters.')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute('SELECT 1 FROM players WHERE user_id=?', (user_id,)).fetchone():
            raise LabError('The target must be a registered player.')
        before = player_countries(db).get(user_id, '')
        db.execute('''INSERT INTO player_countries(user_id,country_code,actor_id,reason) VALUES (?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET country_code=excluded.country_code,
            actor_id=excluded.actor_id,reason=excluded.reason,updated_at=CURRENT_TIMESTAMP''',
            (user_id,code,actor_id,reason))
        db.execute('INSERT INTO player_country_audit(user_id,before_code,after_code,actor_id,reason) VALUES (?,?,?,?,?)',
                   (user_id,before,code,actor_id,reason))
    return code
