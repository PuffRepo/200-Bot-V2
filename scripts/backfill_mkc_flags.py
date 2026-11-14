"""Preview or apply missing flags only; no identities, names, roles or MMR changed."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v2.country_backfill import candidates, make_plan, apply_plan


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=38)
    parser.add_argument('--apply', action='store_true')
    args=parser.parse_args()
    if not 1 <= args.limit <= 38:
        parser.error('This batch is limited to at most 38 candidates.')
    rows=candidates(args.database,args.limit)
    print(f'Checking {len(rows)} eligible MKCentral profiles; existing flags and explicit clears are protected.',flush=True)
    plan=make_plan(rows)
    print(json.dumps({'mode':'apply' if args.apply else 'preview','candidates':len(rows),
                      'countries_found':len(plan['updates']),'unavailable':len(plan['skipped'])}),flush=True)
    if args.apply:
        print(json.dumps(apply_plan(args.database,plan)),flush=True)
    else:
        print('Preview only. No database or Discord changes.',flush=True)
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError, OSError) as error:
        print(f'Backfill stopped: {error}',file=sys.stderr)
        raise SystemExit(2)
