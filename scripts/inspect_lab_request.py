"""Read one V2 lab request and its player state without changing the database."""

import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request_id", nargs="?", type=int, help="omit to list recent requests")
    args = parser.parse_args()
    path = Path(os.environ.get("V2_LAB_DB", "lab-v2.sqlite3")).resolve()
    if not path.is_file():
        parser.error(f"Lab database not found: {path}")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        if args.request_id is None:
            print(f"Lab database: {path}")
            rows = db.execute(
                "SELECT id, kind, user_id, status, reviewer_id, created_at, reviewed_at "
                "FROM requests ORDER BY id DESC LIMIT 20"
            ).fetchall()
            for row in rows:
                print(dict(row))
            if not rows:
                print("No lab requests found.")
            return 0
        row = db.execute(
            "SELECT id, kind, user_id, status, reviewer_id, created_at, reviewed_at "
            "FROM requests WHERE id=?", (args.request_id,)
        ).fetchone()
        if row is None:
            print(f"Request #{args.request_id} does not exist in {path}.")
            return 1
        print("Request:", dict(row))
        player = db.execute(
            "SELECT user_id, name, verified_by, verified_at FROM players WHERE user_id=?",
            (row["user_id"],)
        ).fetchone()
        print("Current lab player:", dict(player) if player else "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
