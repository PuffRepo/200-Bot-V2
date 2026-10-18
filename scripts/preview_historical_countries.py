"""Count archived-country matches against S9. No writes or Discord connection."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlsplit
from urllib.request import Request,urlopen
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.extract_historical_countries import extract
from v2.bulk_countries import preview,digest
from v2.countries import country_code
from v2.lab_store import LabError

ROOT=Path(__file__).resolve().parents[1]
LIMIT=50*1024*1024


def public_coverage(historical,data):
    if data.get('schema_version')!=1 or data.get('season')!='S9' or not isinstance(data.get('players'),list):
        raise LabError('Expected an S9 public snapshot.')
    stamp=datetime.fromisoformat(data['generated_at'])
    age=(datetime.now(timezone.utc)-stamp).total_seconds()
    if not -300<=age<=900:raise LabError('Public S9 snapshot is stale or future-dated.')
    current={}
    for player in data['players']:
        if not isinstance(player,dict):raise LabError('Public snapshot has an invalid player record.')
        uid=player.get('user_id')
        if not isinstance(uid,str) or not uid.isascii() or not uid.isdecimal() or str(int(uid))!=uid or not 0<int(uid)<2**63 or uid in current:
            raise LabError('Public snapshot has invalid or duplicate Discord IDs.')
        current[uid]=country_code(player.get('country_code'))
    candidates={r['discord_id']:r['country_code'] for r in historical['candidates']}
    conflicts={r['discord_id'] for r in historical['conflicts']}
    invalid={r['discord_id'] for r in historical['invalid']}
    matched=set(current)&set(candidates)
    return dict(mode='public_feed_estimate',current_players=len(current),
                already_have_country=sum(bool(code) for code in current.values()),
                historical_candidates_matching_current_players=len(matched),
                potential_missing_countries_to_fill=sum(not current[uid] for uid in matched),
                current_players_with_historical_conflicts=len(set(current)&conflicts),
                current_players_with_invalid_historical_country=len(set(current)&invalid),
                current_players_without_usable_historical_country=len(set(current)-set(candidates)),
                note='Read-only estimate. Public data cannot reveal staff-cleared flags; the database batch preview protects those before apply.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--public-url',help='HTTPS public S9 feed, no credentials')
    source.add_argument('--database',type=Path,help='Existing SQLite, opened read-only')
    args=parser.parse_args()
    try:
        historical=extract([ROOT/'sql'/f's{s}200lounge-export.sql' for s in (5,6,7)])
        if args.public_url:
            url=urlsplit(args.public_url)
            if url.scheme!='https' or not url.hostname or url.username or url.password:
                raise LabError('Use an HTTPS public URL without credentials.')
            with urlopen(Request(args.public_url,headers={'Accept':'application/json'}),timeout=15) as response:
                if urlsplit(response.url).scheme!='https':raise LabError('Refusing an insecure feed redirect.')
                raw=response.read(LIMIT+1)
            if len(raw)>LIMIT:raise LabError('Public snapshot exceeds size limit.')
            result=public_coverage(historical,json.loads(raw))
        else:
            with closing(sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
                plan=preview(db,historical['candidates'])
                result=dict(mode='read_only_database_preview',current_players=db.execute('SELECT COUNT(*) FROM players').fetchone()[0],
                            candidate_status_counts=dict(Counter(r['status'] for r in plan['rows'])),digest=digest(plan))
        print(json.dumps(result,indent=2));return 0
    except (LabError,OSError,ValueError,KeyError,TypeError,sqlite3.Error) as error:
        print(f'Country preview stopped: {error}',file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
