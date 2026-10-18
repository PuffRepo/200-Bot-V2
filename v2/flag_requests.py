"""Registered-member country requests; country-only atomic independent review."""
import hashlib
import json
from pathlib import Path
import sqlite3
from v2.countries import CODES,country_code,player_countries
from v2.lab_store import LabError

NAMES=json.loads(Path(__file__).with_name('country_names.json').read_text(encoding='utf-8'))

def aliases(code):
    value=NAMES.get(code,code)
    return value if isinstance(value,list) else [value]

def country_label(code):return f'{aliases(code)[0]} ({code})' if code else 'Not set'

def country_choices(query):
    query=query.strip().casefold()
    codes=sorted(CODES,key=lambda code:(code.casefold()!=query,aliases(code)[0].casefold(),code))
    return [(country_label(code),code) for code in codes if not query or query in code.casefold() or any(query in name.casefold() for name in aliases(code))][:25]

def normalize_country(value):
    if not isinstance(value,str):raise LabError('Choose a country from the suggestions, or enter its two-letter code.')
    code=country_code(value)
    if code:return code
    found=[code for code in CODES if any(value.strip().casefold()==name.casefold() for name in aliases(code))]
    if len(found)==1:return found[0]
    raise LabError('Choose a country from the suggestions, or enter its two-letter code (US, GB, JP).')

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

class FlagRequests:
    def __init__(self,store):self.store=store
    def _state(self,db,user_id):
        return dict(country=player_countries(db).get(user_id,''),
                    override=bool(db.execute('SELECT 1 FROM player_countries WHERE user_id=?',(user_id,)).fetchone()),
                    revision=db.execute('SELECT COALESCE(MAX(id),0) FROM player_country_audit WHERE user_id=?',(user_id,)).fetchone()[0])
    def request(self,user_id,country,reason=''):
        code=normalize_country(country)
        if reason is not None and not isinstance(reason,str):raise LabError('Context must be text.')
        reason=(reason or '').strip()
        if any(ord(char)<32 and char not in '\n\t' for char in reason):raise LabError('Context contains unsupported control characters.')
        if len(reason)>400:raise LabError('Context must be at most 400 characters.')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM players WHERE user_id=?',(user_id,)).fetchone():
                raise LabError('You must already be registered and verified before requesting a flag.')
            state=self._state(db,user_id)
            if state['country']==code:raise LabError('Your flag is already set to that country.')
            try:
                return db.execute('INSERT INTO flag_requests(user_id,country_code,reason,base_country,base_digest) VALUES (?,?,?,?,?)',
                    (user_id,code,reason,state['country'],digest(state))).lastrowid
            except sqlite3.IntegrityError as error:
                raise LabError('You already have a pending flag request. Staff can review it in /pending.') from error
    def _preview(self,db,request_id,reviewer_id):
        row=db.execute("SELECT * FROM flag_requests WHERE id=? AND status='pending'",(request_id,)).fetchone()
        if not row:raise LabError('Flag request is missing or already reviewed.')
        if row['user_id']==reviewer_id:raise LabError('A different staff member must review your flag request.')
        if not db.execute('SELECT 1 FROM players WHERE user_id=?',(row['user_id'],)).fetchone():raise LabError('Player is no longer registered.')
        row=dict(row);state=self._state(db,row['user_id'])
        stale=digest(state)!=row['base_digest']
        content=(f"Flag request #F{request_id} from <@{row['user_id']}>\n"
                 f"Current: {country_label(state['country'])}\nRequested: {country_label(row['country_code'])}")
        if row['reason']:content+='\nMember context: '+row['reason']
        if stale:content+='\nCountry changed after submission. Reject this stale request and ask the member to submit again.'
        return dict(digest=digest(dict(request=row,current=state,reviewer_id=reviewer_id)),content=content,stale=stale,request=row)
    def preview(self,request_id,reviewer_id):
        with self.store.connect() as db:
            db.execute('BEGIN');return self._preview(db,request_id,reviewer_id)
    def review(self,request_id,reviewer_id,approve,expected_digest=None):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            preview=self._preview(db,request_id,reviewer_id);row=preview['request']
            if approve:
                if preview['stale']:raise LabError('Country changed after submission. Reject this request and ask the member to submit again.')
                if expected_digest!=preview['digest']:raise LabError('Flag preview changed. Reopen /pending and review again.')
                code=country_code(row['country_code'])
                if not code:raise LabError('Invalid stored country; reject this request.')
                reason=f"Approved member flag request #F{request_id}: {country_label(code)}. "+(row['reason'] or 'Member-submitted country.')
                db.execute('''INSERT INTO player_countries(user_id,country_code,actor_id,reason) VALUES (?,?,?,?)
                    ON CONFLICT(user_id) DO UPDATE SET country_code=excluded.country_code,
                    actor_id=excluded.actor_id,reason=excluded.reason,updated_at=CURRENT_TIMESTAMP''',
                    (row['user_id'],code,reviewer_id,reason))
                db.execute('INSERT INTO player_country_audit(user_id,before_code,after_code,actor_id,reason) VALUES (?,?,?,?,?)',
                    (row['user_id'],row['base_country'],code,reviewer_id,reason))
            status='approved' if approve else 'rejected'
            db.execute('UPDATE flag_requests SET status=?,reviewer_id=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=?',
                       (status,reviewer_id,request_id))
            return status
