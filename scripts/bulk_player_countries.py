"""Preview/apply a reviewed CSV country batch. Does not start a Discord bot."""
import argparse
from contextlib import closing
import csv
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from v2.bulk_countries import preview,apply,digest
from v2.lab_store import LabStore,LabError


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--csv',type=Path,required=True)
    parser.add_argument('--apply-digest',help='Explicitly apply exactly the reviewed preview hash')
    parser.add_argument('--actor',type=int)
    parser.add_argument('--reason')
    args=parser.parse_args()
    try:
        if not args.database.is_file():raise LabError('Database must already exist.')
        with args.csv.open(encoding='utf-8-sig',newline='') as source:
            reader=csv.DictReader(source)
            if reader.fieldnames!=['discord_id','country_code']:raise LabError('CSV header must be discord_id,country_code.')
            records=list(reader)
        with closing(sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.execute('BEGIN')
            plan=preview(db,records)
        if args.apply_digest:
            if not args.reason or not args.actor:raise LabError('Apply requires --actor and --reason.')
            # Require the deployed country schema; do not run migrations here.
            class ExistingStore:
                def connect(self):return LabStore.connect(self)
                path=args.database.resolve()
            count=apply(ExistingStore(),records,args.apply_digest,args.actor,args.reason)
            print(json.dumps({'countries_updated':count}));return 0
        print(json.dumps({'digest':digest(plan),'preview':plan},ensure_ascii=False,indent=2))
        return 0
    except (LabError,sqlite3.Error,OSError,ValueError) as error:
        print(f'Country batch stopped: {error}',file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
