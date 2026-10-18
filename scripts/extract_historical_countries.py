"""Extract country-only candidates from archived SQL as text. Never execute SQL."""
import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from v2.countries import country_code
from v2.lab_store import LabError

TOKEN=re.compile(r"\s*(?:(?P<string>'(?:\\.|''|[^'\\])*')|(?P<number>-?\d+(?:\.\d+)?)|(?P<null>NULL)|(?P<punct>[(),;]))",re.S)
ESCAPES={'0':'\0','n':'\n','r':'\r','t':'\t','b':'\b','Z':'\x1a'}

def sql_string(value):
    value=value[1:-1];result=[];i=0
    while i<len(value):
        if value[i]=='\\':
            i+=1
            if i==len(value):raise LabError('Truncated SQL string.')
            result.append(ESCAPES.get(value[i],value[i]))
        elif value[i:i+2]=="''":result.append("'");i+=1
        else:result.append(value[i])
        i+=1
    return ''.join(result)


def player_rows(text):
    schema=re.search(r'CREATE TABLE `player`\s*\((.*?)\)\s*ENGINE',text,re.S)
    if not schema:raise LabError('No supported archived player schema.')
    columns=re.findall(r'^\s*`([^`]+)`',schema[1],re.M)
    if 'player_id' not in columns or 'country_code' not in columns:raise LabError('Player ID/country columns are missing.')
    count=0
    for statement in re.finditer(r'(?m)^INSERT INTO `player` VALUES\s*',text):
        pos=statement.end();row=None;expect='open'
        while True:
            token=TOKEN.match(text,pos)
            if not token:raise LabError('Unsupported or truncated archived player INSERT.')
            pos=token.end();punct=token['punct']
            if punct=='(' and expect=='open':row=[];expect='value'
            elif expect=='value' and punct is None:
                value=sql_string(token['string']) if token['string'] is not None else token['number'] if token['number'] is not None else None
                row.append(value);expect='separator'
            elif punct==',' and expect=='separator':expect='value'
            elif punct==')' and expect=='separator':
                if len(row)!=len(columns):raise LabError('Archived player row does not match schema.')
                uid=row[columns.index('player_id')];code=row[columns.index('country_code')]
                if not isinstance(uid,str) or not uid.isascii() or not uid.isdecimal() or not 0<int(uid)<2**63:raise LabError('Invalid archived Discord ID.')
                yield str(int(uid)),code
                count+=1;expect='next'
            elif punct==',' and expect=='next':expect='open'
            elif punct==';' and expect=='next':break
            else:raise LabError('Unexpected archived SQL player syntax.')
    if not count:raise LabError('No archived player rows found.')


def extract(paths):
    observations=defaultdict(list);sources=[]
    for path in paths:
        path=Path(path);raw=path.read_bytes();rows=list(player_rows(raw.decode('utf-8-sig')))
        valid=sum(bool(country_code(value)) for _,value in rows)
        blank=sum(value is None or isinstance(value,str) and not value.strip() for _,value in rows)
        sources.append(dict(source=path.name,sha256=hashlib.sha256(raw).hexdigest(),players=len(rows),valid_country=valid,blank_country=blank,invalid_country=len(rows)-valid-blank))
        for uid,value in rows:
            observations[uid].append(dict(source=path.name,country_code=country_code(value),
                invalid_nonempty=value is not None and (not isinstance(value,str) or bool(value.strip())) and not country_code(value)))
    candidates=[];conflicts=[];invalid=[];missing=0
    for uid,rows in sorted(observations.items(),key=lambda item:int(item[0])):
        codes=sorted({r['country_code'] for r in rows if r['country_code']})
        if len(codes)>1:conflicts.append(dict(discord_id=uid,countries=codes,sources=rows))
        elif any(r['invalid_nonempty'] for r in rows):invalid.append(dict(discord_id=uid,sources=rows))
        elif len(codes)==1:candidates.append(dict(discord_id=uid,country_code=codes[0]))
        else:missing+=1
    return dict(schema_version=1,sources=sources,summary=dict(historical_players=len(observations),unambiguous_candidates=len(candidates),conflicting_players=len(conflicts),invalid_source_players=len(invalid),no_country_players=missing),candidates=candidates,conflicts=conflicts,invalid=invalid)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sql',type=Path,nargs='+',required=True)
    parser.add_argument('--output',type=Path,required=True,help='New directory, never an existing path')
    args=parser.parse_args()
    try:
        result=extract(args.sql)
        args.output.mkdir(parents=True,exist_ok=False)
        (args.output/'country-review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        with (args.output/'countries.csv').open('w',encoding='utf-8',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=['discord_id','country_code']);writer.writeheader();writer.writerows(result['candidates'])
        print(json.dumps(dict(sources=result['sources'],summary=result['summary']),indent=2))
        return 0
    except (LabError,OSError,UnicodeError) as error:
        print(f'Historical country extraction stopped: {error}',file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
