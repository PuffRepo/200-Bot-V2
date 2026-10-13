"""Seed 12 fictional players into an existing V2 lab database for table trials."""

import argparse
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


FIXTURES = [(-100000 - n, -200000 - n, f"LAB{n:02d}") for n in range(1, 13)]


def seed(path: Path) -> tuple[int, Path]:
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"Lab database does not exist: {path}")
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)) as db:
        existing = db.execute(
            "SELECT user_id, mkc_id, name, verified_by FROM players "
            "WHERE user_id BETWEEN -100012 AND -100001 OR mkc_id BETWEEN -200012 AND -200001 "
            "OR name COLLATE NOCASE IN (" + ",".join("?" for _ in FIXTURES) + ")",
            [name for _, _, name in FIXTURES],
        ).fetchall()
        expected = {(user, mkc, name, -1) for user, mkc, name in FIXTURES}
        if any(tuple(row) not in expected for row in existing):
            raise ValueError("A fixture ID or name is already in use by another player; no changes made.")
        backup = path.with_name(path.stem + "-before-fixtures-" +
                                datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + path.suffix)
        with closing(sqlite3.connect(backup)) as destination:
            db.backup(destination)
        before = len(existing)
        with db:
            db.executemany(
                "INSERT OR IGNORE INTO players(user_id, mkc_id, name, verified_by) "
                "VALUES (?,?,?,-1)", FIXTURES
            )
        return len(FIXTURES) - before, backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-lab-fixtures", action="store_true", required=True,
                        help="explicitly add fictional LAB01–LAB12 player rows")
    args = parser.parse_args()
    path = Path(os.environ.get("V2_LAB_DB", "lab-v2.sqlite3"))
    try:
        added, backup = seed(path)
    except (sqlite3.Error, ValueError) as error:
        parser.exit(1, f"No fixtures seeded: {error}\n")
    print(f"Lab database: {path.resolve()}")
    print(f"Backup: {backup}")
    print(f"Added {added} fictional players; LAB01 through LAB12 are available for table tests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
