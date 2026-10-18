"""Reviewable country-only batches keyed by Discord ID, never by display name."""
import hashlib
import json
from v2.countries import country_code, player_countries
from v2.lab_store import LabError


def digest(plan):
    return hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def preview(db, records):
    players={str(r[0]):r[1] for r in db.execute('SELECT user_id,name FROM players')}
    overrides={str(r[0]) for r in db.execute('SELECT user_id FROM player_countries')}
    current=player_countries(db)
    seen=set();rows=[]
    for record in records:
        uid=record.get('discord_id');code=country_code(record.get('country_code'))
        if not isinstance(uid,str) or not uid.isascii() or not uid.isdecimal() or not 0<int(uid)<2**63:
            raise LabError('Every row needs a positive Discord ID stored as text.')
        uid=str(int(uid))
        if uid in seen:raise LabError('Duplicate Discord IDs: resolve conflicting source rows first.')
        seen.add(uid)
        if not code:raise LabError('Every row needs a valid ISO two-letter country_code.')
        before=current.get(int(uid),'')
        status=('unknown_player' if uid not in players else 'protected_staff_record' if uid in overrides
                else 'unchanged' if before==code else 'conflicting_country' if before else 'fill_missing')
        rows.append(dict(discord_id=uid,name=players.get(uid),before=before,country_code=code,status=status))
    return dict(schema_version=1,rows=sorted(rows,key=lambda r:int(r['discord_id'])))


def apply(store, records, expected_digest, actor_id, reason):
    if type(actor_id) is not int or actor_id<=0:raise LabError('Record the staff actor Discord ID.')
    reason=reason.strip()
    if not 3<=len(reason)<=500:raise LabError('Record a source/reason of 3–500 characters.')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        plan=preview(db,records)
        if digest(plan)!=expected_digest:raise LabError('Country preview changed. Review a fresh preview before applying.')
        count=0
        for row in plan['rows']:
            if row['status']!='fill_missing':continue
            uid=int(row['discord_id']);code=row['country_code']
            db.execute('INSERT INTO player_countries(user_id,country_code,actor_id,reason) VALUES (?,?,?,?)',
                       (uid,code,actor_id,reason))
            db.execute('INSERT INTO player_country_audit(user_id,before_code,after_code,actor_id,reason) VALUES (?,?,?,?,?)',
                       (uid,row['before'],code,actor_id,reason))
            count+=1
    return count
