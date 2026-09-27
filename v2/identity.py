"""Local, transactional verification and name-change queue for the V2 lab.

Approval means a human moderator checked the MKCentral profile's linked Discord
account. A pasted profile URL alone never proves account ownership.
"""

import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qs, urlsplit


NAME_COOLDOWN_SECONDS = 60 * 24 * 60 * 60
PROFILE_PATH = re.compile(r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?registry/players/profile/?", re.I)


class WorkflowError(ValueError):
    """A request cannot be submitted or decided in its current state."""


def mkc_profile_id(url: str) -> int:
    """Accept only a direct HTTPS MKCentral registry profile with one ID."""
    parsed = urlsplit(url.strip())
    if parsed.scheme != "https" or parsed.netloc.lower() != "mkcentral.com":
        raise WorkflowError("Use an https://mkcentral.com registry profile link.")
    if not PROFILE_PATH.fullmatch(parsed.path):
        raise WorkflowError("Use a MKCentral player profile link.")
    parameters = parse_qs(parsed.query, keep_blank_values=True)
    ids = parameters.get("id", [])
    if len(ids) != 1 or not ids[0].isdigit() or int(ids[0]) <= 0:
        raise WorkflowError("The profile link needs one positive player ID.")
    return int(ids[0])


def clean_name(name: str) -> tuple[str, str]:
    """Accept short leaderboard names without mentions, whitespace, or controls."""
    normalized = name.strip().replace(" ", "-")
    if not 1 <= len(normalized) <= 16 or not all(
        character.isalnum() or character in "_-." for character in normalized
    ):
        raise WorkflowError("Names must be 1–16 letters, numbers, hyphens, underscores, or periods.")
    return normalized, normalized.casefold()


@dataclass(frozen=True)
class Decision:
    player_id: int
    name: str | None = None


class IdentityStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS players (
                    player_id INTEGER PRIMARY KEY,
                    mkc_id INTEGER NOT NULL UNIQUE,
                    player_name TEXT NOT NULL,
                    name_key TEXT NOT NULL UNIQUE,
                    last_name_change_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS verification_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id INTEGER NOT NULL,
                    mkc_id INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'denied')),
                    created_at INTEGER NOT NULL,
                    decided_at INTEGER,
                    reviewer_id INTEGER
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_verification_per_player
                    ON verification_requests(player_id) WHERE status = 'pending';
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_verification_per_mkc
                    ON verification_requests(mkc_id) WHERE status = 'pending';
                CREATE TABLE IF NOT EXISTS name_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id INTEGER NOT NULL REFERENCES players(player_id),
                    requested_name TEXT NOT NULL,
                    name_key TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'denied')),
                    created_at INTEGER NOT NULL,
                    decided_at INTEGER,
                    reviewer_id INTEGER
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_name_per_player
                    ON name_requests(player_id) WHERE status = 'pending';
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_name_key
                    ON name_requests(name_key) WHERE status = 'pending';
            """)
        if os.name == "posix":
            os.chmod(path, 0o600)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def pending(self) -> tuple[list[tuple[int, int, int]], list[tuple[int, int, str]]]:
        """Show staff the pending ID, player, and claimed identity or name."""
        with self._connect() as connection:
            verifications = connection.execute(
                "SELECT id, player_id, mkc_id FROM verification_requests "
                "WHERE status = 'pending' ORDER BY id LIMIT 10"
            ).fetchall()
            names = connection.execute(
                "SELECT id, player_id, requested_name FROM name_requests "
                "WHERE status = 'pending' ORDER BY id LIMIT 10"
            ).fetchall()
        return verifications, names

    def verification_claim(self, request_id: int) -> tuple[int, int]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT player_id, mkc_id FROM verification_requests WHERE id = ? AND status = 'pending'",
                (request_id,),
            ).fetchone()
        if row is None:
            raise WorkflowError("Verification request not found or already decided.")
        return row

    def submit_verification(self, player_id: int, profile_url: str, now: int | None = None) -> int:
        mkc_id = mkc_profile_id(profile_url)
        now = int(time.time()) if now is None else now
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM players WHERE player_id = ?", (player_id,)).fetchone():
                raise WorkflowError("This Discord account is already verified; ask staff to resolve a changed MKCentral account.")
            if connection.execute("SELECT 1 FROM players WHERE mkc_id = ?", (mkc_id,)).fetchone():
                raise WorkflowError("This MKCentral account is already linked to another player.")
            request = connection.execute(
                "SELECT id, mkc_id FROM verification_requests WHERE player_id = ? AND status = 'pending'",
                (player_id,),
            ).fetchone()
            if request:
                if request[1] == mkc_id:
                    return request[0]
                raise WorkflowError("You already have a verification request pending; ask staff to resolve it first.")
            if connection.execute(
                "SELECT 1 FROM verification_requests WHERE mkc_id = ? AND status = 'pending'", (mkc_id,)
            ).fetchone():
                raise WorkflowError("This MKCentral account has a pending verification request.")
            cursor = connection.execute(
                "INSERT INTO verification_requests(player_id, mkc_id, status, created_at) "
                "VALUES (?, ?, 'pending', ?)", (player_id, mkc_id, now),
            )
            return cursor.lastrowid

    def review_verification(
        self, request_id: int, reviewer_id: int, *, approve: bool,
        player_name: str | None = None, now: int | None = None,
    ) -> Decision:
        now = int(time.time()) if now is None else now
        name, name_key = clean_name(player_name) if approve and player_name is not None else (None, None)
        if approve and name is None:
            raise WorkflowError("An approved player needs a leaderboard name.")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            request = connection.execute(
                "SELECT player_id, mkc_id, status FROM verification_requests WHERE id = ?", (request_id,)
            ).fetchone()
            if not request or request[2] != "pending":
                raise WorkflowError("Verification request not found or already decided.")
            if approve:
                try:
                    connection.execute(
                        "INSERT INTO players(player_id, mkc_id, player_name, name_key) VALUES (?, ?, ?, ?)",
                        (request[0], request[1], name, name_key),
                    )
                except sqlite3.IntegrityError as error:
                    raise WorkflowError("Discord account, MKCentral account, or leaderboard name already in use.") from error
            connection.execute(
                "UPDATE verification_requests SET status = ?, decided_at = ?, reviewer_id = ? WHERE id = ?",
                ("approved" if approve else "denied", now, reviewer_id, request_id),
            )
            return Decision(request[0], name)

    def submit_name(self, player_id: int, requested_name: str, now: int | None = None) -> int:
        name, key = clean_name(requested_name)
        now = int(time.time()) if now is None else now
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            player = connection.execute(
                "SELECT name_key, last_name_change_at FROM players WHERE player_id = ?", (player_id,)
            ).fetchone()
            if not player:
                raise WorkflowError("Verify your MKCentral account before requesting a name change.")
            if player[1] is not None and now < player[1] + NAME_COOLDOWN_SECONDS:
                raise WorkflowError(f"Name-change cooldown ends at <t:{player[1] + NAME_COOLDOWN_SECONDS}:F>.")
            if player[0] == key or connection.execute(
                "SELECT 1 FROM players WHERE name_key = ?", (key,)
            ).fetchone():
                raise WorkflowError("That leaderboard name is already in use.")
            existing = connection.execute(
                "SELECT id, name_key FROM name_requests WHERE player_id = ? AND status = 'pending'", (player_id,)
            ).fetchone()
            if existing:
                if existing[1] == key:
                    return existing[0]
                raise WorkflowError("You already have a name change pending; ask staff to resolve it first.")
            if connection.execute(
                "SELECT 1 FROM name_requests WHERE name_key = ? AND status = 'pending'", (key,)
            ).fetchone():
                raise WorkflowError("That name is already requested by another player.")
            cursor = connection.execute(
                "INSERT INTO name_requests(player_id, requested_name, name_key, status, created_at) "
                "VALUES (?, ?, ?, 'pending', ?)", (player_id, name, key, now),
            )
            return cursor.lastrowid

    def review_name(
        self, request_id: int, reviewer_id: int, *, approve: bool, now: int | None = None,
    ) -> Decision:
        now = int(time.time()) if now is None else now
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            request = connection.execute(
                "SELECT player_id, requested_name, name_key, status FROM name_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            if not request or request[3] != "pending":
                raise WorkflowError("Name request not found or already decided.")
            if approve:
                try:
                    updated = connection.execute(
                        "UPDATE players SET player_name = ?, name_key = ?, last_name_change_at = ? "
                        "WHERE player_id = ?", (request[1], request[2], now, request[0]),
                    ).rowcount
                except sqlite3.IntegrityError as error:
                    raise WorkflowError("That name is now taken; deny the request or choose another.") from error
                if updated != 1:
                    raise WorkflowError("Player no longer exists; request was not decided.")
            connection.execute(
                "UPDATE name_requests SET status = ?, decided_at = ?, reviewer_id = ? WHERE id = ?",
                ("approved" if approve else "denied", now, reviewer_id, request_id),
            )
            return Decision(request[0], request[1] if approve else None)
