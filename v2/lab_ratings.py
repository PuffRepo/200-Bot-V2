"""Season rating ledger for isolated lab trials; never touches Discord roles."""
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from v2.lab_store import LabError, parse_scores
from v2.rating import calculate_table
from v2.season_policy import load_policy, rank_for_mmr

POLICY_PATH=Path(__file__).resolve().parents[1]/'settings/s9-policy.json'
FIXTURE_CASES={
    'placements':[None]*12,
    'equal':[6000]*12,
    'mixed':[6000,None,None,6000,6000,6000,None,None,None,6000,6000,6000],
    'boundaries':[1499,1500,2999,3000,4499,4500,5999,6000,7499,7500,13499,13500],
    'extreme':[30000]*3+[1000]*9,
    'floor':[1]*3+[6000]*9,
}

class LabRatings:
    def __init__(self, store):
        self.store=store
        self.policy=load_policy(POLICY_PATH)
        with store.connect() as source:
            exists=source.execute("SELECT 1 FROM sqlite_master WHERE name='season_ratings'").fetchone()
            if not exists:
                stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                backup=store.path.with_name(store.path.stem+f'-before-ratings-{stamp}.sqlite3')
                with closing(sqlite3.connect(backup)) as destination:
                    source.backup(destination)
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS season_ratings (
                    season TEXT NOT NULL REFERENCES lab_seasons(name),
                    user_id INTEGER NOT NULL REFERENCES players(user_id),
                    mmr INTEGER CHECK(mmr IS NULL OR mmr>=1),
                    revision INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(season,user_id)
                );
                CREATE TABLE IF NOT EXISTS rating_applications (
                    table_id INTEGER PRIMARY KEY REFERENCES tables(id),
                    season TEXT NOT NULL, actor_id INTEGER NOT NULL,
                    policy_json TEXT NOT NULL, snapshot_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS rating_events (
                    table_id INTEGER NOT NULL REFERENCES rating_applications(table_id),
                    user_id INTEGER NOT NULL, name TEXT NOT NULL,
                    before_mmr INTEGER, after_mmr INTEGER NOT NULL,
                    raw_delta INTEGER NOT NULL, applied_delta INTEGER,
                    kind TEXT NOT NULL, PRIMARY KEY(table_id,user_id)
                );
                CREATE TABLE IF NOT EXISTS lab_fixture_runs (
                    season TEXT PRIMARY KEY REFERENCES lab_seasons(name),
                    case_name TEXT NOT NULL, actor_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS application_sequence (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    table_id INTEGER NOT NULL UNIQUE REFERENCES rating_applications(table_id)
                );
                CREATE TABLE IF NOT EXISTS table_reversal_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, table_id INTEGER NOT NULL REFERENCES tables(id),
                    requester_id INTEGER NOT NULL, reason TEXT NOT NULL, evidence TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
                    reviewer_id INTEGER, digest TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    reviewed_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_table_reversal ON table_reversal_requests(table_id) WHERE status='pending';
                CREATE TABLE IF NOT EXISTS table_reversals (
                    table_id INTEGER PRIMARY KEY REFERENCES rating_applications(table_id),
                    request_id INTEGER NOT NULL UNIQUE REFERENCES table_reversal_requests(id),
                    actor_id INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            ''')

        with store.connect() as db:
            if 'requires_independent' not in {r[1] for r in db.execute('PRAGMA table_info(table_reversal_requests)')}:
                db.execute('ALTER TABLE table_reversal_requests ADD COLUMN requires_independent INTEGER NOT NULL DEFAULT 1')
                from v2.correction_policy import flagged
                for row in db.execute("SELECT id,table_id FROM table_reversal_requests WHERE status='pending'").fetchall():
                    db.execute('UPDATE table_reversal_requests SET requires_independent=? WHERE id=?',(int(flagged(db,row['table_id'])),row['id']))

    def _snapshot(self, db, table_id, routine=False, review_pending=False):
        table=db.execute('SELECT * FROM tables WHERE id=?',(table_id,)).fetchone()
        current=db.execute("SELECT value FROM lab_settings WHERE key='current_season'").fetchone()[0]
        if table is None or table['status']!=('pending' if routine or review_pending else 'validated'):
            raise LabError('Table must be validated and not already applied.')
        if table['season']!=current or table['participant_ids'] is None:
            raise LabError('This table is from an earlier lab season. Submit a fresh table.')
        if db.execute("SELECT 1 FROM table_reversal_requests r JOIN tables t ON t.id=r.table_id WHERE r.status='pending' AND t.season=?",(current,)).fetchone():
            raise LabError('Rating application is paused while a season correction request is pending.')
        if db.execute("SELECT 1 FROM table_flags WHERE table_id=? AND status IN ('open','upheld')",(table_id,)).fetchone():
            raise LabError('This table is disputed; independent staff resolution is required.')
        if db.execute("SELECT 1 FROM table_flags f JOIN tables t ON t.id=f.table_id WHERE t.season=? AND t.status='applied' AND f.status IN ('open','upheld')",(current,)).fetchone():
            raise LabError('An applied result is disputed. Resolve or reverse it before applying later tables.')
        if review_pending and table['replaces_table_id'] is None:
            raise LabError('Combined staff approval is only for linked corrections.')
        if routine and table['replaces_table_id'] is not None:
            raise LabError('Replacement tables use the staff correction review.')
        if table['replaces_table_id'] is not None and not table['evidence']:
            raise LabError('A replacement table requires result evidence.')
        next_replacement=db.execute("SELECT t.id FROM tables t JOIN application_sequence s ON s.table_id=t.id WHERE t.season=? AND t.status='reversed' AND NOT EXISTS (SELECT 1 FROM tables child WHERE child.replaces_table_id=t.id AND child.status IN ('applied','reversed')) ORDER BY s.sequence LIMIT 1",(current,)).fetchone()
        if next_replacement and table['replaces_table_id']!=next_replacement[0]:
            raise LabError(f"Correction replay is paused: review and apply a replacement for table #{next_replacement[0]} first, in original application order.")
        if table['format_size']==6:
            raise LabError('6v6 is disabled for S9; this pending table cannot be applied.')
        scores=parse_scores(table['scores'],table['format_size'])
        identities=json.loads(table['participant_ids'])
        players=[]
        for user_id,(name,score) in zip(identities,scores):
            row=db.execute('SELECT mmr,revision FROM season_ratings WHERE season=? AND user_id=?',(current,user_id)).fetchone()
            players.append({'user_id':user_id,'name':name,'score':score,
                            'mmr':row['mmr'] if row else None,'revision':row['revision'] if row else 0})
        snapshot={'table_id':table_id,'season':current,'format':table['format_size'],
                  'tier':table['tier'],'policy':self.policy,'players':players,
                  'evidence':table['evidence'],'routine':routine}
        if table['replaces_table_id'] is not None:
            from v2.correction_policy import replacement_independent
            snapshot['requires_independent']=replacement_independent(db,table_id)
        if review_pending:snapshot['review_pending']=True
        digest=hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()[:20]
        rows=calculate_table(self.policy,players,table['format_size'],table['tier'])
        return table,rows,digest

    def preview(self, table_id, routine=False):
        with self.store.connect() as db:
            db.execute('BEGIN')
            table,rows,digest=self._snapshot(db,table_id,routine)
            return {'table_id':table_id,'season':table['season'],'digest':digest,'rows':rows}

    def review_preview(self, table_id):
        """Preview pending corrections without persisting a separate validation step."""
        with self.store.connect() as db:
            db.execute('BEGIN')
            item=db.execute('SELECT status FROM tables WHERE id=?',(table_id,)).fetchone()
            pending=bool(item and item['status']=='pending')
            table,rows,digest=self._snapshot(db,table_id,review_pending=pending)
            if table['replaces_table_id'] is None:raise LabError('Use the reporter Yes/No for routine tables.')
            return {'table_id':table_id,'rows':rows,'digest':digest,'review_pending':pending}

    def apply(self, table_id, actor_id, digest, routine=False, review_pending=False):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            table,rows,expected=self._snapshot(db,table_id,routine,review_pending)
            if routine and table['reporter_id']!=actor_id:
                raise LabError('Only the submitting Reporter can confirm this routine table.')
            independent=False
            if not routine:
                from v2.correction_policy import assert_replacement_reviewer
                independent=assert_replacement_reviewer(db,table_id,actor_id)
            if digest!=expected:
                raise LabError('Ratings or policy changed, or confirmation is incorrect. Preview again.')
            if review_pending:
                db.execute("INSERT INTO table_reviews(table_id,reviewer_id,decision,requires_independent) VALUES (?,?,'validated',?)",(table_id,actor_id,int(independent)))
                db.execute("UPDATE tables SET status='validated' WHERE id=?",(table_id,))
            db.execute('INSERT INTO rating_applications(table_id,season,actor_id,policy_json,snapshot_digest) VALUES (?,?,?,?,?)',
                       (table_id,table['season'],actor_id,json.dumps(self.policy,sort_keys=True),digest))
            db.execute('INSERT INTO application_sequence(table_id) VALUES (?)',(table_id,))
            if routine:
                db.execute('INSERT INTO table_confirmations(table_id,actor_id,digest) VALUES (?,?,?)',(table_id,actor_id,digest))
            for row in rows:
                db.execute('INSERT INTO season_ratings(season,user_id,mmr,revision) VALUES (?,?,?,1) ON CONFLICT(season,user_id) DO UPDATE SET mmr=excluded.mmr,revision=season_ratings.revision+1',
                           (table['season'],row['user_id'],row['after']))
                db.execute('INSERT INTO rating_events(table_id,user_id,name,before_mmr,after_mmr,raw_delta,applied_delta,kind) VALUES (?,?,?,?,?,?,?,?)',
                           (table_id,row['user_id'],row['name'],row['before'],row['after'],row['raw_delta'],row['applied_delta'],row['kind']))
            db.execute("UPDATE tables SET status='applied' WHERE id=?",(table_id,))
            return rows

    def _reversal_snapshot(self, db, table_id):
        table=db.execute('SELECT * FROM tables WHERE id=?',(table_id,)).fetchone()
        season=db.execute("SELECT value FROM lab_settings WHERE key='current_season'").fetchone()[0]
        if table is None or table['status']!='applied' or table['season']!=season:
            raise LabError('Only an applied table in the current season can be reversed.')
        # Pre-update applications have no reliable application order; never guess it.
        if db.execute("SELECT 1 FROM rating_applications a LEFT JOIN application_sequence s ON s.table_id=a.table_id JOIN tables t ON t.id=a.table_id WHERE a.season=? AND t.status='applied' AND s.table_id IS NULL",(season,)).fetchone():
            raise LabError('This season contains pre-update applications without reliable ordering. Use a fresh lab season for correction tests.')
        latest=db.execute("SELECT s.table_id FROM application_sequence s JOIN tables t ON t.id=s.table_id WHERE t.season=? AND t.status='applied' ORDER BY s.sequence DESC LIMIT 1",(season,)).fetchone()
        if latest is None or latest[0]!=table_id:
            raise LabError('Reverse later applied tables first, newest application first. Earlier changes cannot safely bypass dependent tables.')
        events=db.execute('SELECT * FROM rating_events WHERE table_id=? ORDER BY user_id',(table_id,)).fetchall()
        if len(events)!=12:
            raise LabError('The original ledger must contain exactly 12 events.')
        snapshot=[]
        for event in events:
            current=db.execute('SELECT mmr,revision FROM season_ratings WHERE season=? AND user_id=?',(season,event['user_id'])).fetchone()
            if current is None or current['mmr']!=event['after_mmr']:
                raise LabError('Current ratings disagree with the original ledger. Stop for investigation.')
            snapshot.append({'user_id':event['user_id'],'name':event['name'],'from':event['after_mmr'],
                             'to':event['before_mmr'],'revision':current['revision']})
        return table,snapshot

    def request_reversal(self, table_id, requester_id, reason, evidence):
        from v2.lab_store import evidence_link
        evidence=evidence_link(evidence)
        if not 1<=len(reason.strip())<=200:
            raise LabError('A correction reason must contain 1–200 characters.')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            table,_=self._reversal_snapshot(db,table_id)
            if db.execute("SELECT 1 FROM table_reversal_requests r JOIN tables t ON t.id=r.table_id WHERE r.status='pending' AND t.season=?",(table['season'],)).fetchone():
                raise LabError('Finish the current correction request before opening another.')
            from v2.correction_policy import disputed_lineage
            return db.execute('INSERT INTO table_reversal_requests(table_id,requester_id,reason,evidence,requires_independent) VALUES (?,?,?,?,?)',
                              (table_id,requester_id,reason.strip(),evidence,int(disputed_lineage(db,table_id)))).lastrowid

    def _reversal_preview(self, db, request_id):
        request=db.execute('SELECT * FROM table_reversal_requests WHERE id=?',(request_id,)).fetchone()
        if request is None or request['status']!='pending':
            raise LabError('Correction request is missing or already resolved.')
        table,rows=self._reversal_snapshot(db,request['table_id'])
        from v2.correction_policy import reversal_independent
        snapshot={'request':dict(request),'rows':rows,'season':table['season'],'requires_independent':reversal_independent(db,request)}
        digest=hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()[:20]
        return request,table,rows,digest

    def reversal_preview(self, request_id):
        with self.store.connect() as db:
            db.execute('BEGIN')
            request,table,rows,digest=self._reversal_preview(db,request_id)
            from v2.correction_policy import reversal_independent
            return {'request_id':request_id,'table_id':table['id'],'rows':rows,'digest':digest,
                    'requester_id':request['requester_id'],'reason':request['reason'],'evidence':request['evidence'],'requires_independent':reversal_independent(db,request)}

    def approve_reversal(self, request_id, reviewer_id, digest):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            request,table,rows,expected=self._reversal_preview(db,request_id)
            from v2.correction_policy import assert_reversal_reviewer
            independent=assert_reversal_reviewer(db,request,reviewer_id)
            if digest!=expected:
                raise LabError('Correction preview is stale or confirmation is incorrect. Preview again.')
            for row in rows:
                db.execute('UPDATE season_ratings SET mmr=?,revision=revision+1 WHERE season=? AND user_id=?',
                           (row['to'],table['season'],row['user_id']))
            db.execute('INSERT INTO table_reversals(table_id,request_id,actor_id) VALUES (?,?,?)',
                       (table['id'],request_id,reviewer_id))
            db.execute("UPDATE tables SET status='reversed' WHERE id=?",(table['id'],))
            db.execute("UPDATE table_reversal_requests SET status='approved',reviewer_id=?,digest=?,requires_independent=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=?",(reviewer_id,digest,int(independent),request_id))
            return rows

    def reject_reversal(self, request_id, reviewer_id):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            request=db.execute('SELECT r.*,t.reporter_id,t.season FROM table_reversal_requests r JOIN tables t ON t.id=r.table_id WHERE r.id=?',(request_id,)).fetchone()
            current=db.execute("SELECT value FROM lab_settings WHERE key='current_season'").fetchone()[0]
            if request is None or request['status']!='pending' or request['season']!=current:
                raise LabError('A pending current-season correction is required.')
            from v2.correction_policy import assert_reversal_reviewer
            independent=assert_reversal_reviewer(db,request,reviewer_id)
            db.execute("UPDATE table_reversal_requests SET status='rejected',reviewer_id=?,requires_independent=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=?",(reviewer_id,int(independent),request_id))

    def ratings(self):
        with self.store.connect() as db:
            season=db.execute("SELECT value FROM lab_settings WHERE key='current_season'").fetchone()[0]
            rows=db.execute('SELECT p.user_id,p.name,r.mmr FROM players p LEFT JOIN season_ratings r ON r.user_id=p.user_id AND r.season=? ORDER BY p.name',(season,)).fetchall()
            return season,[{'user_id':r['user_id'],'name':r['name'],'mmr':r['mmr'],'rank':rank_for_mmr(self.policy,r['mmr'])} for r in rows]

    def fixture_case(self, name, season, actor_id):
        if name not in FIXTURE_CASES:
            raise LabError('Case must be placements, equal, mixed, boundaries, extreme, or floor.')
        if not re.fullmatch(r'LAB-S[0-9]{1,3}',season):
            raise LabError('Use a new lab season such as LAB-S901.')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM table_reversal_requests WHERE status='pending'").fetchone():
                raise LabError('Finish pending corrections before switching the lab season.')
            if db.execute('SELECT 1 FROM lab_seasons WHERE name=?',(season,)).fetchone():
                raise LabError('Fixture trials require a NEW lab season; existing seasons are preserved.')
            for i in range(1,13):
                row=db.execute('SELECT name,mkc_id,verified_by FROM players WHERE user_id=?',(-100000-i,)).fetchone()
                if row is None or tuple(row)!=(f'LAB{i:02d}',-200000-i,-1):
                    raise LabError('Seed the 12 reserved fixtures before running fixture cases.')
            db.execute('INSERT INTO lab_seasons(name,started_by) VALUES (?,?)',(season,actor_id))
            db.execute("UPDATE lab_settings SET value=? WHERE key='current_season'",(season,))
            db.execute('INSERT INTO lab_fixture_runs(season,case_name,actor_id) VALUES (?,?,?)',(season,name,actor_id))
            for i,mmr in enumerate(FIXTURE_CASES[name],1):
                db.execute('INSERT INTO season_ratings(season,user_id,mmr) VALUES (?,?,?)',(season,-100000-i,mmr))
        return season
