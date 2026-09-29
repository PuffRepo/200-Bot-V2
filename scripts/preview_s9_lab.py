"""Print a read-only S9 lab inventory; never apply a reset."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v2.season_policy import load_policy, preview_lab_transition


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=Path('lab-v2.sqlite3'))
    parser.add_argument('--policy', type=Path, default=Path(__file__).resolve().parents[1] / 'settings/s9-policy.json')
    args = parser.parse_args()
    try:
        policy = load_policy(args.policy)
        print(json.dumps(preview_lab_transition(args.db, policy), indent=2))
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f'Preview failed: {error}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
