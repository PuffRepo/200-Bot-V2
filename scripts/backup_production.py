"""Run one verified backup, or restore a remote backup to a new test file."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from v2.backups import client, settings, run_backup, restore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=Path('/app/private/production.sqlite3'))
    parser.add_argument('--restore-key')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        bucket, prefix, keep = settings()
        s3 = client()
        if args.restore_key:
            if args.output is None:
                raise ValueError('Restore requires --output pointing to a new file')
            restore(s3, bucket, prefix, args.restore_key, args.output)
            print('RESTORE VERIFIED: downloaded checksum and SQLite checks passed.', flush=True)
        else:
            key = run_backup(args.database, s3, bucket, prefix, keep)
            print('BACKUP VERIFIED: ' + key, flush=True)
        return 0
    except Exception as error:
        # SDK exceptions may contain endpoints or signed request details.
        print(f'BACKUP FAILED ({type(error).__name__}); check storage credentials, permissions, network and database integrity.', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
