"""Restored strike and MMR penalty behavior with preserved seasonal accounting."""
from datetime import datetime,timezone,timedelta
from v2.lab_store import LabError

class Moderation:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS balance_adjustments(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, season TEXT NOT NULL,
                    user_id INTEGER NOT NULL, actor_id INTEGER NOT NULL, kind TEXT NOT NULL,
                    before_mmr INTEGER, after_mmr INTEGER, requested INTEGER NOT NULL,
                    delta INTEGER NOT NULL, reason TEXT NOT NULL, strike_id INTEGER,
                    operation_key TEXT UNIQUE NOT NULL, revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS strike_removals(
                    strike_id INTEGER PRIMARY KEY REFERENCES strikes(id), actor_id INTEGER NOT NULL,
                    reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS member_restrictions(
                    user_id INTEGER PRIMARY KEY, season TEXT NOT NULL, offenses INTEGER NOT NULL,
                    until_at TEXT, trigger_strike_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            ''')
    def _season(self,db):return db.execute("SELECT value FROM lab_settings WHERE key='current_season'").fetchone()[0]
    def _ready(self,db,user_id):
        if not db.execute('SELECT 1 FROM players WHERE user_id=?',(user_id,)).fetchone():raise LabError('Player is not registered.')
        season=self._season(db)
        if db.execute("SELECT 1 FROM table_reversal_requests r JOIN tables t ON t.id=r.table_id WHERE t.season=? AND r.status='pending'",(season,)).fetchone() or db.execute("SELECT 1 FROM table_flags f JOIN tables t ON t.id=f.table_id WHERE t.season=? AND t.status='applied' AND f.status IN ('open','upheld')",(season,)).fetchone():raise LabError('Finish pending disputes/corrections before changing penalties.')
        if db.execute("SELECT 1 FROM tables t WHERE t.season=? AND t.status='reversed' AND NOT EXISTS (SELECT 1 FROM tables c WHERE c.replaces_table_id=t.id AND c.status IN ('applied','reversed'))",(season,)).fetchone():raise LabError('Finish correction replay before changing penalties.')
        return season
    def _adjust(self,db,season,user_id,actor_id,amount,kind,reason,key,strike_id=None,allow_unplaced=False):
        if db.execute('SELECT 1 FROM balance_adjustments WHERE operation_key=?',(key,)).fetchone():raise LabError('This penalty/removal operation was already applied.')
        current=db.execute('SELECT mmr,revision FROM season_ratings WHERE season=? AND user_id=?',(season,user_id)).fetchone()
        before=current['mmr'] if current else None;revision=current['revision'] if current else 0
        if before is None and not allow_unplaced:raise LabError('Cannot apply an MMR-only penalty to an unplaced player.')
        after=max(1,before+amount) if before is not None else None
        delta=after-before if before is not None else 0
        db.execute('INSERT INTO season_ratings(season,user_id,mmr,revision) VALUES (?,?,?,?) ON CONFLICT(season,user_id) DO UPDATE SET mmr=excluded.mmr,revision=excluded.revision',(season,user_id,after,revision+1))
        db.execute('INSERT INTO balance_adjustments(season,user_id,actor_id,kind,before_mmr,after_mmr,requested,delta,reason,strike_id,operation_key,revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(season,user_id,actor_id,kind,before,after,abs(amount),delta,reason,strike_id,key,revision+1))
        return {'user_id':user_id,'before':before,'after':after,'delta':delta}
    def deduct(self,actor_id,user_id,points,reason,key):
        if type(points) is not int or not 1<=points<=10000 or not 1<=len(reason.strip())<=128:raise LabError('Use 1–10000 points and a 1–128 character reason.')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');season=self._ready(db,user_id)
            return self._adjust(db,season,user_id,actor_id,-points,'deduction',reason.strip(),key)
    def strike(self,actor_id,user_id,points,reason,key):
        if type(points) is not int or not 0<=points<=10000 or not 1<=len(reason.strip())<=128:raise LabError('Use 0–10000 points and a 1–128 character reason.')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');season=self._ready(db,user_id)
            if db.execute('SELECT 1 FROM balance_adjustments WHERE operation_key=?',(key,)).fetchone():raise LabError('This strike operation was already applied.')
            sid=db.execute('INSERT INTO strikes(user_id,issuer_id,reason,mmr_penalty,season) VALUES (?,?,?,?,?)',(user_id,actor_id,reason.strip(),points,season)).lastrowid
            result=self._adjust(db,season,user_id,actor_id,-points,'strike',reason.strip(),key,sid,True)
            count=db.execute("SELECT COUNT(*) FROM strikes s LEFT JOIN strike_removals r ON r.strike_id=s.id WHERE s.user_id=? AND s.season=? AND s.expires_at>datetime('now') AND r.strike_id IS NULL",(user_id,season)).fetchone()[0]
            restriction=None
            if count>=3:
                old=db.execute('SELECT * FROM member_restrictions WHERE user_id=?',(user_id,)).fetchone()
                offenses=(old['offenses'] if old else 0)+1
                restriction=(datetime.now(timezone.utc)+timedelta(days=7*offenses)).strftime('%Y-%m-%d %H:%M:%S')
                db.execute('INSERT INTO member_restrictions(user_id,season,offenses,until_at,trigger_strike_id) VALUES (?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET season=excluded.season,offenses=excluded.offenses,until_at=excluded.until_at,trigger_strike_id=excluded.trigger_strike_id',(user_id,season,offenses,restriction,sid))
            return {**result,'strike_id':sid,'active_count':count,'restriction_until':restriction}
    def unstrike(self,actor_id,strike_id,reason,key):
        if not 1<=len(reason.strip())<=128:raise LabError('Record a removal reason (1–128 characters).')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            strike=db.execute('SELECT * FROM strikes WHERE id=?',(strike_id,)).fetchone()
            if not strike:raise LabError('Strike ID not found.')
            if strike['user_id']==actor_id:raise LabError('You cannot remove your own strike.')
            season=self._ready(db,strike['user_id'])
            if strike['season']!=season:raise LabError('Cannot refund a previous-season strike into the new season.')
            created=datetime.fromisoformat(strike['created_at']).replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc)-created>timedelta(hours=24):raise LabError('Strike is older than the original 24-hour removal window.')
            if db.execute('SELECT 1 FROM strike_removals WHERE strike_id=?',(strike_id,)).fetchone():raise LabError('Strike already removed.')
            applied=db.execute("SELECT delta FROM balance_adjustments WHERE strike_id=? AND kind='strike'",(strike_id,)).fetchone()
            refund=-applied['delta'] if applied else 0
            result=self._adjust(db,season,strike['user_id'],actor_id,refund,'unstrike',reason.strip(),key,strike_id,True)
            db.execute('INSERT INTO strike_removals(strike_id,actor_id,reason) VALUES (?,?,?)',(strike_id,actor_id,reason.strip()))
            restriction=db.execute('SELECT * FROM member_restrictions WHERE user_id=?',(strike['user_id'],)).fetchone()
            active=db.execute("SELECT COUNT(*) FROM strikes s LEFT JOIN strike_removals r ON r.strike_id=s.id WHERE s.user_id=? AND s.season=? AND s.expires_at>datetime('now') AND r.strike_id IS NULL",(strike['user_id'],season)).fetchone()[0]
            if restriction and restriction['season']==season and restriction['until_at'] and active<3:
                db.execute('UPDATE member_restrictions SET offenses=max(0,offenses-1),until_at=NULL WHERE user_id=?',(strike['user_id'],))
            return result
    def member_state(self,user_id):
        with self.store.connect() as db:
            season=self._season(db)
            row=db.execute("SELECT *,until_at>datetime('now') AS active FROM member_restrictions WHERE user_id=?",(user_id,)).fetchone()
            # S9 strike reset also releases bot-managed restrictions from the previous season.
            return {'managed':bool(row),'restricted':bool(row and row['season']==season and row['active']),
                    'until_at':row['until_at'] if row else None}
    def player_strikes(self,user_id):
        with self.store.connect() as db:
            season=self._season(db)
            return [dict(r) for r in db.execute("SELECT s.id,s.reason,s.expires_at FROM strikes s LEFT JOIN strike_removals r ON r.strike_id=s.id WHERE s.user_id=? AND s.season=? AND s.expires_at>datetime('now') AND r.strike_id IS NULL ORDER BY s.id",(user_id,season))]
