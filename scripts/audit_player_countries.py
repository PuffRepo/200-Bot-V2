"""Print aggregate country coverage from existing SQLite, strictly read-only."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from v2.countries import player_countries


def audit(database):
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        players=list(db.execute('SELECT user_id,mkc_id FROM players'))
        countries=player_countries(db)
        return dict(players=len(players),with_country=sum(bool(countries.get(uid)) for uid,_ in players),
                    missing_country=sum(not countries.get(uid) for uid,_ in players),
                    missing_country_with_recorded_mkc_id=sum(not countries.get(uid) and mkc is not None and mkc>0 for uid,mkc in players))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--database',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(audit(args.database),indent=2))
