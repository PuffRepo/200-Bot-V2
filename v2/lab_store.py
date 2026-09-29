"""Small, isolated SQLite store for V2 lab workflow trials.

No legacy database is imported. All mutations are confined to the supplied file.
"""

import re
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlparse


class LabError(ValueError):
    pass


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
    name = name.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,16}", name):
        raise LabError("Use 1–16 letters, digits, underscores, periods, or hyphens.")
    return name


def parse_scores(raw: str, format_size: int) -> list[tuple[str, int]]:
    if format_size not in (1, 2, 3, 4, 6):
        raise LabError("Format must be 1, 2, 3, 4, or 6.")
    parts = raw.split()
    if len(parts) != 24:
        raise LabError("Enter exactly 12 name and score pairs.")
    players = []
    for i in range(0, 24, 2):
        name = player_name(parts[i])
        if not parts[i + 1].isdigit():
            raise LabError("Scores must be nonnegative whole numbers.")
        score = int(parts[i + 1])
        if score > 180:
            raise LabError("Individual scores may not exceed 180.")
        players.append((name, score))
    if len({name.casefold() for name, _ in players}) != 12:
        raise LabError("Every player must appear exactly once.")
    if sum(score for _, score in players) != 984:
        raise LabError("The 12 scores must add up to 984.")
    return players


class LabStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS players (
                    user_id INTEGER PRIMARY KEY, mkc_id INTEGER UNIQUE NOT NULL,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
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
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
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
            if not db.execute("SELECT 1 FROM players WHERE user_id=?", (user_id,)).fetchone():
                raise LabError("Player is not verified in the lab.")
            return db.execute("INSERT INTO strikes(user_id,issuer_id,reason,mmr_penalty) VALUES (?,?,?,?)",
                              (user_id,issuer_id,reason,penalty)).lastrowid

    def submit_table(self, reporter_id: int, format_size: int, scores: str) -> int:
        players = parse_scores(scores,format_size)
        with self.connect() as db:
            for name,_ in players:
                if not db.execute("SELECT 1 FROM players WHERE name=?", (name,)).fetchone():
                    raise LabError(f"Player {name!r} is not verified in the lab.")
            return db.execute("INSERT INTO tables(reporter_id,format_size,scores) VALUES (?,?,?)",
                              (reporter_id,format_size,scores)).lastrowid
