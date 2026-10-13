"""Small, isolated SQLite store for V2 lab workflow trials.

No legacy database is imported. All mutations are confined to the supplied file.
"""

import re
import unicodedata
import sqlite3
from contextlib import closing
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse


from v2.launch_scope import ACTIVE_TIERS, ACTIVE_FORMATS

class LabError(ValueError):
    pass


class ClosingConnection(sqlite3.Connection):
    """Commit/rollback on context exit, then release the database handle."""

    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def mkc_id(profile: str) -> int:
    url = urlparse(profile.strip())
    if (url.scheme != "https" or url.hostname != "mkcentral.com"
            or not url.path.rstrip("/").endswith("/registry/players/profile")):
        raise LabError("Use an HTTPS MKCentral registry player profile URL.")
    values = parse_qs(url.query).get("id", [])
    if len(values) != 1 or not values[0].isdigit() or int(values[0]) < 1:
        raise LabError("The MKCentral profile URL needs a numeric id.")
    return int(values[0])


def player_name(name: str) -> str:
    # Preserve spelling, case, combining marks, emoji joiners and spaces verbatim.
    if not isinstance(name, str) or not name.strip() or len(name) > 32:
        raise LabError("Use a nonblank name of at most 32 Unicode characters.")
    if any(unicodedata.category(c) in ('Cc', 'Cs', 'Zl', 'Zp') for c in name):
        raise LabError("Names cannot contain line breaks, tabs or control characters.")
    return name


def evidence_link(value: str) -> str:
    value = value.strip()
    url = urlparse(value)
    if len(value) > 500 or url.scheme != 'https' or not url.hostname or any(c.isspace() for c in value):
        raise LabError('Supply an HTTPS link to the result evidence (maximum 500 characters).')
    return value


def parse_scores(raw: str, format_size: int) -> list[tuple[str, int]]:
    if format_size not in (1, 2, 3, 4, 6):
        raise LabError("Format must be 1, 2, 3, 4, or 6.")
    # JSON pairs preserve even leading/trailing spaces and literal quotes.
    # For ordinary spaced names, use twelve lines: exact name<TAB>score.
    # Twelve name + final-space + score lines are also accepted.
    if re.match(r'^\s*\[\s*\[', raw):
        try:
            pairs = json.loads(raw)
        except (ValueError, TypeError) as error:
            raise LabError("Invalid JSON name/score pairs.") from error
    elif len(raw.splitlines()) == 12:
        pairs = []
        for line in raw.splitlines():
            separator = '\t' if '\t' in line else ' '
            pair = line.rsplit(separator, 1)
            pairs.append(pair)
    else:
        parts = raw.split()
        pairs = list(zip(parts[::2], parts[1::2])) if len(parts) == 24 else []
    if not isinstance(pairs, list) or len(pairs) != 12:
        raise LabError("Enter 12 name/score pairs. For spaced names, use one player per line: exact name, then a tab and score.")
    players = []
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise LabError("Each entry must contain a name and score.")
        name = player_name(pair[0])
        value = pair[1]
        if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
            raise LabError("Scores must be nonnegative whole numbers.")
        score = int(value)
        if score > 180:
            raise LabError("Individual scores may not exceed 180.")
        players.append((name, score))
    if len({name for name, _ in players}) != 12:
        raise LabError("Every player must appear exactly once.")
    if sum(score for _, score in players) != 984:
        raise LabError("The 12 scores must add up to 984.")
    return players


class LabStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            with closing(sqlite3.connect(self.path)) as source:
                columns = {row[1] for row in source.execute('PRAGMA table_info(strikes)')}
                if columns and 'season' not in columns:
                    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                    backup = self.path.with_name(self.path.stem + f'-before-seasons-{stamp}.sqlite3')
                    with closing(sqlite3.connect(backup)) as destination:
                        source.backup(destination)
        self._migrate_exact_names()
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS players (
                    user_id INTEGER PRIMARY KEY, mkc_id INTEGER UNIQUE,
                    name TEXT NOT NULL COLLATE BINARY UNIQUE,
                    verified_by INTEGER NOT NULL, verified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL CHECK (kind IN ('verify', 'name')),
                    user_id INTEGER NOT NULL, value TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
                    reviewer_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    reviewed_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_request
                    ON requests(kind,user_id) WHERE status='pending';
                CREATE TABLE IF NOT EXISTS strikes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                    issuer_id INTEGER NOT NULL, reason TEXT NOT NULL, mmr_penalty INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    expires_at TEXT NOT NULL DEFAULT (datetime('now','+30 days'))
                );
                CREATE TABLE IF NOT EXISTS tables (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, reporter_id INTEGER NOT NULL,
                    format_size INTEGER NOT NULL, scores TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS mmr_adjustments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL, issuer_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount < 0),
                    reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposed'
                        CHECK (status IN ('proposed','applied','void')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS table_reviews (
                    table_id INTEGER PRIMARY KEY REFERENCES tables(id),
                    reviewer_id INTEGER NOT NULL, decision TEXT NOT NULL
                        CHECK (decision IN ('validated','rejected')),
                    reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            if 'requires_independent' not in {r[1] for r in db.execute('PRAGMA table_info(table_reviews)')}:
                db.execute('ALTER TABLE table_reviews ADD COLUMN requires_independent INTEGER NOT NULL DEFAULT 1')
            # Existing test strikes belong to a lab season, not actual S8 data.
            db.execute("CREATE TABLE IF NOT EXISTS lab_seasons (name TEXT PRIMARY KEY, started_by INTEGER NOT NULL, started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            db.execute("CREATE TABLE IF NOT EXISTS lab_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO lab_seasons(name,started_by) VALUES ('LAB-INITIAL',0)")
            db.execute("INSERT OR IGNORE INTO lab_settings(key,value) VALUES ('current_season','LAB-INITIAL')")
            if 'season' not in {row[1] for row in db.execute('PRAGMA table_info(strikes)')}:
                db.execute("ALTER TABLE strikes ADD COLUMN season TEXT NOT NULL DEFAULT 'LAB-INITIAL'")
            columns = {row[1] for row in db.execute('PRAGMA table_info(tables)')}
            for name, declaration in [('season',"TEXT NOT NULL DEFAULT 'LAB-LEGACY'"),
                                      ('tier',"TEXT NOT NULL DEFAULT 'tier-all'"),
                                      ('participant_ids','TEXT'), ('evidence',"TEXT NOT NULL DEFAULT ''"),
                                      ('replaces_table_id','INTEGER REFERENCES tables(id)')]:
                if name not in columns:
                    db.execute(f'ALTER TABLE tables ADD COLUMN {name} {declaration}')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS table_confirmations (
                    table_id INTEGER PRIMARY KEY REFERENCES tables(id),
                    actor_id INTEGER NOT NULL, digest TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS table_cancellations (
                    table_id INTEGER PRIMARY KEY REFERENCES tables(id), actor_id INTEGER NOT NULL,
                    reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS table_button_previews (
                    table_id INTEGER PRIMARY KEY REFERENCES tables(id), reporter_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS table_flags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, table_id INTEGER NOT NULL REFERENCES tables(id),
                    actor_id INTEGER NOT NULL, reason TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','cleared','upheld')),
                    reviewer_id INTEGER, resolution TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_open_table_flag ON table_flags(table_id) WHERE status='open';
            ''')

    def _migrate_exact_names(self):
        if not self.path.exists():
            return
        with closing(sqlite3.connect(self.path)) as db:
            schema = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='players'").fetchone()
            if not schema or 'COLLATE NOCASE' not in schema[0].upper():
                return
            columns = [r[1] for r in db.execute('PRAGMA table_info(players)')]
            if columns != ['user_id', 'mkc_id', 'name', 'verified_by', 'verified_at']:
                raise LabError('Unknown players schema; exact-name migration stopped without changes.')
            extras = list(db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='players' AND type IN ('index','trigger') AND sql IS NOT NULL"))
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            backup = self.path.with_name(self.path.stem + f'-before-exact-names-{stamp}.sqlite3')
            with closing(sqlite3.connect(backup)) as destination:
                db.backup(destination)
            # This separate connection has foreign_keys OFF; references retain the name players.
            with db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("CREATE TABLE players_exact (user_id INTEGER PRIMARY KEY, mkc_id INTEGER UNIQUE, name TEXT NOT NULL COLLATE BINARY UNIQUE, verified_by INTEGER NOT NULL, verified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
                db.execute('INSERT INTO players_exact SELECT * FROM players')
                db.execute('DROP TABLE players')
                db.execute('ALTER TABLE players_exact RENAME TO players')
                for (sql,) in extras:
                    db.execute(sql)
                if db.execute('PRAGMA foreign_key_check').fetchone():
                    raise LabError('Exact-name migration failed foreign key validation; changes rolled back.')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def request_verify(self, user_id: int, profile: str) -> int:
        value = str(mkc_id(profile))
        with self.connect() as db:
            if db.execute("SELECT 1 FROM players WHERE user_id=?", (user_id,)).fetchone():
                raise LabError("This account is already verified.")
            if db.execute("SELECT 1 FROM players WHERE mkc_id=?", (int(value),)).fetchone():
                raise LabError("That MKCentral ID is already assigned.")
            try:
                return db.execute("INSERT INTO requests(kind,user_id,value) VALUES ('verify',?,?)", (user_id,value)).lastrowid
            except sqlite3.IntegrityError as error:
                raise LabError("You already have a pending verification request.") from error

    def request_name(self, user_id: int, name: str) -> int:
        name = player_name(name)
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM players WHERE user_id=?", (user_id,)).fetchone():
                raise LabError("Verify before requesting a name change.")
            if db.execute("SELECT 1 FROM players WHERE name=?", (name,)).fetchone():
                raise LabError("That name is already taken.")
            try:
                return db.execute("INSERT INTO requests(kind,user_id,value) VALUES ('name',?,?)", (user_id,name)).lastrowid
            except sqlite3.IntegrityError as error:
                raise LabError("You already have a pending name request.") from error

    def review(self, request_id: int, reviewer_id: int, approve: bool, verified_name: str = "") -> str:
        with self.connect() as db:
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if row is None or row['status'] != 'pending':
                raise LabError("Request is missing or already reviewed.")
            if row['user_id'] == reviewer_id:
                raise LabError("A staff member cannot approve their own request.")
            if approve:
                try:
                    if row['kind'] == 'verify':
                        name = player_name(verified_name)
                        db.execute("INSERT INTO players(user_id,mkc_id,name,verified_by) VALUES (?,?,?,?)",
                                   (row['user_id'], int(row['value']), name, reviewer_id))
                    else:
                        if db.execute("UPDATE players SET name=? WHERE user_id=?", (row['value'],row['user_id'])).rowcount != 1:
                            raise LabError("Player no longer exists.")
                except sqlite3.IntegrityError as error:
                    raise LabError("Name or MKCentral ID is already assigned; review unchanged.") from error
            status = 'approved' if approve else 'rejected'
            db.execute("UPDATE requests SET status=?,reviewer_id=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=?",
                       (status,reviewer_id,request_id))
            return status

    def strike(self, issuer_id: int, user_id: int, reason: str, penalty: int) -> int:
        reason = reason.strip()
        if not reason or len(reason)>128 or penalty<0 or penalty>10000:
            raise LabError("Reason must be 1–128 characters and penalty 0–10000.")
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("SELECT 1 FROM players WHERE user_id=?", (user_id,)).fetchone():
                raise LabError("Player is not verified in the lab.")
            return db.execute("INSERT INTO strikes(user_id,issuer_id,reason,mmr_penalty,season) VALUES (?,?,?,?,(SELECT value FROM lab_settings WHERE key='current_season'))",
                              (user_id,issuer_id,reason,penalty)).lastrowid

    def strike_summary(self, user_id: int | None = None) -> dict:
        with self.connect() as db:
            season = db.execute("SELECT value FROM lab_settings WHERE key='current_season'").fetchone()[0]
            removed_exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='strike_removals'").fetchone()
            rows=db.execute("SELECT season,expires_at > datetime('now') AS active,id FROM strikes s WHERE (? IS NULL OR user_id=?)",(user_id,user_id)).fetchall()
            removed={r[0] for r in db.execute('SELECT strike_id FROM strike_removals')} if removed_exists else set()
        return {'season': season, 'history_count': len(rows),
                'season_count': sum(row['season'] == season and row['id'] not in removed for row in rows),
                'active_count': sum(row['season'] == season and row['active'] and row['id'] not in removed for row in rows)}

    def start_lab_season(self, name: str, issuer_id: int) -> bool:
        if not re.fullmatch(r'LAB-S[0-9]{1,3}', name):
            raise LabError('Use a lab season name such as LAB-S9.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute("SELECT value FROM lab_settings WHERE key='current_season'").fetchone()[0]
            if current == name:
                return False
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='table_reversal_requests'").fetchone() and db.execute("SELECT 1 FROM table_reversal_requests WHERE status='pending'").fetchone():
                raise LabError('Finish pending corrections before switching the lab season.')
            if db.execute('SELECT 1 FROM lab_seasons WHERE name=?', (name,)).fetchone():
                raise LabError('Cannot reopen a previous lab season.')
            db.execute('INSERT INTO lab_seasons(name,started_by) VALUES (?,?)', (name, issuer_id))
            db.execute("UPDATE lab_settings SET value=? WHERE key='current_season'", (name,))
        return True

    def propose_mmr_deduction(self, issuer_id: int, user_id: int, points: int, reason: str) -> int:
        reason = reason.strip()
        if not isinstance(points, int) or not 1 <= points <= 10000:
            raise LabError("Deduction must be 1–10000 points.")
        if not reason or len(reason) > 128:
            raise LabError("Reason must be 1–128 characters.")
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM players WHERE user_id=?", (user_id,)).fetchone():
                raise LabError("Player is not verified in the lab.")
            return db.execute(
                "INSERT INTO mmr_adjustments(user_id,issuer_id,amount,reason) VALUES (?,?,?,?)",
                (user_id, issuer_id, -points, reason),
            ).lastrowid

    def submit_table(self, reporter_id: int, format_size: int, scores: str, tier: str = 'tier-all',
                     evidence: str = '', replaces_table_id: int | None = None) -> int:
        if format_size not in ACTIVE_FORMATS:
            raise LabError("S9 supports FFA, 2v2, 3v3 and 4v4. 6v6 is disabled.")
        players = parse_scores(scores,format_size)
        evidence = evidence_link(evidence) if evidence else ''
        if tier not in ACTIVE_TIERS:
            raise LabError('S9 tiers are All, C, B and A only.')
        def canonical(pairs):
            return sorted(sorted(pairs[start:start+format_size]) for start in range(0,12,format_size))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            season=db.execute("SELECT value FROM lab_settings WHERE key='current_season'").fetchone()[0]
            if replaces_table_id is not None:
                original=db.execute('SELECT season,status FROM tables WHERE id=?',(replaces_table_id,)).fetchone()
                if original is None or original['season']!=season or original['status']!='reversed':
                    raise LabError('Replacement must reference a reversed table in the current season.')
                if db.execute("SELECT 1 FROM tables WHERE replaces_table_id=? AND status IN ('pending','validated','applied')",(replaces_table_id,)).fetchone():
                    raise LabError('An active replacement already exists for this table.')
            identities=[]
            for name,_ in players:
                row=db.execute("SELECT user_id FROM players WHERE name=?", (name,)).fetchone()
                if not row:
                    raise LabError(f"Player {name!r} is not verified in the lab.")
                identities.append(row[0])
            fingerprint=canonical(list(zip(identities,[score for _,score in players])))
            for row in db.execute("SELECT id,format_size,scores,participant_ids FROM tables WHERE season=? AND status IN ('pending','validated','applied')",(season,)):
                if row['format_size']!=format_size: continue
                prior = parse_scores(row['scores'],row['format_size'])
                prior_ids=json.loads(row['participant_ids']) if row['participant_ids'] else []
                if prior_ids and canonical(list(zip(prior_ids,[score for _,score in prior]))) == fingerprint:
                    raise LabError(f"Identical table #{row['id']} already exists; review it instead of resubmitting.")
            return db.execute("INSERT INTO tables(reporter_id,format_size,scores,season,tier,participant_ids,evidence,replaces_table_id) VALUES (?,?,?,?,?,?,?,?)",
                              (reporter_id,format_size,scores,season,tier,json.dumps(identities),evidence,replaces_table_id)).lastrowid

    def review_table(self, table_id: int, reviewer_id: int, accept: bool) -> str:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT reporter_id,status,season,replaces_table_id FROM tables WHERE id=?",(table_id,)).fetchone()
            if row is None or row['status'] != 'pending':
                raise LabError("Table is missing or already reviewed.")
            current=db.execute("SELECT value FROM lab_settings WHERE key='current_season'").fetchone()[0]
            if row['season']!=current:
                raise LabError('This table belongs to an earlier lab season; submit a fresh table.')
            from v2.correction_policy import assert_replacement_reviewer
            independent=assert_replacement_reviewer(db,table_id,reviewer_id)
            if db.execute("SELECT 1 FROM table_flags WHERE table_id=? AND status='open'",(table_id,)).fetchone():
                raise LabError('Resolve the dispute before reviewing this table.')
            decision = 'validated' if accept else 'rejected'
            db.execute("INSERT INTO table_reviews(table_id,reviewer_id,decision,requires_independent) VALUES (?,?,?,?)",
                       (table_id,reviewer_id,decision,int(independent)))
            db.execute("UPDATE tables SET status=? WHERE id=?",(decision,table_id))
            return decision

    def flag_table(self, table_id: int, actor_id: int, reason: str) -> int:
        if not 1 <= len(reason.strip()) <= 200:
            raise LabError('A dispute reason must contain 1–200 characters.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            table=db.execute('SELECT status,season FROM tables WHERE id=?',(table_id,)).fetchone()
            current=db.execute("SELECT value FROM lab_settings WHERE key='current_season'").fetchone()[0]
            if table is None or table['season']!=current or table['status'] not in ('pending','validated','applied'):
                raise LabError('Only a current pending, validated, or applied table can be disputed.')
            if db.execute("SELECT 1 FROM table_flags WHERE table_id=? AND status='open'",(table_id,)).fetchone():
                raise LabError('This table already has an open dispute.')
            return db.execute('INSERT INTO table_flags(table_id,actor_id,reason) VALUES (?,?,?)',
                              (table_id,actor_id,reason.strip())).lastrowid

    def cancel_table(self, table_id: int, reporter_id: int, reason: str = 'Reporter selected No') -> None:
        if not 1 <= len(reason.strip()) <= 200:
            raise LabError('Cancellation reason must contain 1–200 characters.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            table=db.execute('SELECT reporter_id,status,replaces_table_id FROM tables WHERE id=?',(table_id,)).fetchone()
            if table is None or table['status']!='pending' or table['reporter_id']!=reporter_id or table['replaces_table_id'] is not None:
                raise LabError('Only the submitting Reporter can cancel their pending routine table.')
            db.execute('INSERT INTO table_cancellations(table_id,actor_id,reason) VALUES (?,?,?)',(table_id,reporter_id,reason.strip()))
            db.execute("UPDATE tables SET status='rejected' WHERE id=?",(table_id,))

    def register_button_preview(self, table_id: int, reporter_id: int) -> None:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            table=db.execute('SELECT status,reporter_id,replaces_table_id FROM tables WHERE id=?',(table_id,)).fetchone()
            if table is None or table['status']!='pending' or table['reporter_id']!=reporter_id or table['replaces_table_id'] is not None:
                raise LabError('Only a pending routine table can have confirmation buttons.')
            db.execute('INSERT OR IGNORE INTO table_button_previews(table_id,reporter_id) VALUES (?,?)',(table_id,reporter_id))

    def reset_button_previews(self) -> int:
        """At single-process startup, cancel previews whose callbacks no longer exist."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows=db.execute("SELECT t.id,t.reporter_id FROM tables t JOIN table_button_previews p ON p.table_id=t.id WHERE t.status='pending' AND t.replaces_table_id IS NULL").fetchall()
            for row in rows:
                db.execute('INSERT INTO table_cancellations(table_id,actor_id,reason) VALUES (?,?,?)',
                           (row['id'],row['reporter_id'],'Bot restarted before confirmation'))
                db.execute("UPDATE tables SET status='rejected' WHERE id=?",(row['id'],))
            return len(rows)

    def resolve_flag(self, flag_id: int, reviewer_id: int, uphold: bool, reason: str) -> str:
        if not 1 <= len(reason.strip()) <= 200:
            raise LabError('Record a resolution reason of 1–200 characters.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            flag=db.execute('SELECT f.*,t.reporter_id,t.status AS table_status,t.season FROM table_flags f JOIN tables t ON t.id=f.table_id WHERE f.id=?',(flag_id,)).fetchone()
            if flag is None or flag['status']!='open':
                raise LabError('Dispute is missing or already resolved.')
            current=db.execute("SELECT value FROM lab_settings WHERE key='current_season'").fetchone()[0]
            if flag['season']!=current or reviewer_id in (flag['actor_id'],flag['reporter_id']):
                raise LabError('A current-season dispute requires a reviewer independent of its reporter and flagger.')
            status='upheld' if uphold else 'cleared'
            db.execute('UPDATE table_flags SET status=?,reviewer_id=?,resolution=? WHERE id=?',
                       (status,reviewer_id,reason.strip(),flag_id))
            if uphold and flag['table_status'] in ('pending','validated'):
                db.execute("UPDATE tables SET status='rejected' WHERE id=?",(flag['table_id'],))
            return status
