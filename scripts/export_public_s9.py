"""Export sanitized S9 website data without opening SQLite for writes or starting a bot."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from v2.statistics import snapshot
from v2.lab_store import LabError


def export(database, output):
    database = Path(database).resolve()
    destination = Path(output)
    if destination.is_symlink():
        raise LabError('Export destination must not be a symlink.')
    output = destination.resolve()
    # Protect the whole SQLite family, not just the primary file.
    protected = {database, *(Path(str(database)+suffix) for suffix in ('-wal','-shm','-journal'))}
    if output in protected or output.suffix.lower() != '.json':
        raise LabError('Export to a separate .json file, never a database or SQLite sidecar.')
    if output.is_symlink():
        raise LabError('Export destination must not be a symlink.')
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        data=snapshot(db)
        if any(int(p['user_id']) < 1 for p in data['players']):
            raise LabError('Refusing to publish fixture identities.')
    output.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.s9-export-',dir=output.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(data,stream,ensure_ascii=False,allow_nan=False,separators=(',',':'))
            stream.flush();os.fsync(stream.fileno())
        os.replace(tmp,output)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return len(data['players'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        count=export(args.database,args.output)
    except (OSError,sqlite3.Error,ValueError) as error:
        parser.exit(2,f'Public export failed: {error}\n')
    print(f'Public S9 export written: {count} players. No bot or database write performed.')
    return 0


if __name__=='__main__':raise SystemExit(main())
