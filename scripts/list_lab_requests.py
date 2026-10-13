"""List recent V2 lab requests without changing the database."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path


path = Path(os.environ.get("V2_LAB_DB", "lab-v2.sqlite3")).resolve()
if not path.is_file():
    raise SystemExit(f"Lab database not found: {path}")
print(f"Lab database: {path}")
with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT id, kind, user_id, status, reviewer_id, created_at, reviewed_at "
        "FROM requests ORDER BY id DESC LIMIT 20"
    ).fetchall()
    for row in rows:
        print(dict(row))
    if not rows:
        print("No lab requests found.")
