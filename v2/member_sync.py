"""Restore nickname and rank/restriction roles only inside the configured guild."""
import asyncio
from v2.lab_store import LabError

class MemberSync:
    def __init__(self,store,ratings,moderation,bot,config):
        self.store,self.ratings,self.moderation,self.bot,self.config=store,ratings,moderation,bot,config
        self.lock=asyncio.Lock()
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS member_sync_jobs(user_id INTEGER PRIMARY KEY, pending INTEGER NOT NULL DEFAULT 1,generation INTEGER NOT NULL DEFAULT 1,last_error TEXT,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
            if 'generation' not in {r[1] for r in db.execute('PRAGMA table_info(member_sync_jobs)')}:
                db.execute('ALTER TABLE member_sync_jobs ADD COLUMN generation INTEGER NOT NULL DEFAULT 1')
    def queue(self,ids):
        with self.store.connect() as db:
            for uid in ids:
                if uid>0:db.execute('INSERT INTO member_sync_jobs(user_id) VALUES (?) ON CONFLICT(user_id) DO UPDATE SET pending=1,generation=member_sync_jobs.generation+1,updated_at=CURRENT_TIMESTAMP',(uid,))
    async def preflight(self):
        guild=self.bot.get_guild(self.config['guild_id'])
        if not guild:raise LabError('Configured guild is unavailable.')
        me=guild.me
        if not me or not me.guild_permissions.manage_roles or not me.guild_permissions.manage_nicknames:raise LabError('Bot requires Manage Roles and Manage Nicknames.')
        roles=self.config['roles'];ids=[roles['placement'],roles['loungeless'],roles['chat_restricted'],*roles['ranks'].values()]
        if roles['verified_optional']:ids.append(roles['verified_optional'])
        for rid in ids:
            role=guild.get_role(rid)
            if role is None or role.is_default() or role.managed or role>=me.top_role:raise LabError(f'Configured managed role {rid} is missing, managed, or above the bot.')
        for rid in [roles['reporter'],*roles['staff']]:
            if guild.get_role(rid) is None:raise LabError(f'Access role {rid} does not exist in the configured guild.')
    async def sync(self,uid):
        if uid<1:return  # Fixture identities never trigger Discord API calls.
        guild=self.bot.get_guild(self.config['guild_id'])
        if not guild:raise LabError('Configured guild is unavailable.')
        with self.store.connect() as db:
            row=db.execute('SELECT name FROM players WHERE user_id=?',(uid,)).fetchone()
        if not row:raise LabError('Registered member not found.')
        member=await guild.fetch_member(uid)
        if member.guild.id!=self.config['guild_id']:raise LabError('Member belongs to a different guild.')
        role_config=self.config['roles'];state=self.moderation.member_state(uid)
        managed_ranks={role_config['placement'],*role_config['ranks'].values()}
        role_ids={r.id for r in member.roles}
        # Preserve manually assigned restrictions not represented in the bot database.
        restricted=state['restricted'] or (not state['managed'] and role_config['loungeless'] in role_ids)
        _,ratings=self.ratings.ratings();rating=next((r for r in ratings if r['user_id']==uid),None)
        rank='Placement' if rating is None else rating['rank']
        target=role_config['loungeless'] if restricted else (role_config['placement'] if rank=='Placement' else role_config['ranks'][rank])
        desired={target}
        if role_config['verified_optional']:desired.add(role_config['verified_optional'])
        removable=(role_ids&managed_ranks)-desired
        if state['managed'] and not restricted and role_config['loungeless'] in role_ids:removable.add(role_config['loungeless'])
        me=guild.me
        # Owners/higher staff are not renamed, but role assignment may still be possible.
        nickname_blocked=member.id==guild.owner_id or member.top_role>=me.top_role
        if member.nick!=row['name'] and not nickname_blocked:
            await member.edit(nick=row['name'],reason='200 Lounge approved player name')
        for rid in sorted(desired-role_ids):
            role=guild.get_role(rid)
            if role is None or role>=me.top_role or role.managed:raise LabError('Managed role cannot be assigned.')
            await member.add_roles(role,reason='200 Lounge rating/restriction synchronization',atomic=True)
        for rid in sorted(removable):
            role=guild.get_role(rid)
            if role is None or role>=me.top_role or role.managed:raise LabError('Managed role cannot be removed.')
            await member.remove_roles(role,reason='200 Lounge rating/restriction synchronization',atomic=True)
        if member.nick!=row['name'] and nickname_blocked:raise LabError('Rank synchronized; nickname requires the member or higher staff because of Discord hierarchy.')
    async def drain(self):
        async with self.lock:
            with self.store.connect() as db:jobs=list(db.execute('SELECT user_id,generation FROM member_sync_jobs WHERE pending=1'))
            errors=[]
            for uid,generation in jobs:
                try:
                    await self.sync(uid)
                    with self.store.connect() as db:db.execute('UPDATE member_sync_jobs SET pending=0,last_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND generation=?',(uid,generation))
                except Exception as error:
                    message=f'{type(error).__name__}: {error}'
                    with self.store.connect() as db:db.execute('UPDATE member_sync_jobs SET last_error=?,updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND generation=?',(message[:300],uid,generation))
                    errors.append(uid)
            return errors
    async def reconcile(self):
        with self.store.connect() as db:ids=[r[0] for r in db.execute('SELECT user_id FROM players WHERE user_id>0')]
        self.queue(ids)
        return await self.drain()
    async def refresh_restrictions(self):
        with self.store.connect() as db:
            ids=[r[0] for r in db.execute("SELECT user_id FROM member_restrictions WHERE until_at IS NOT NULL AND (until_at<=datetime('now') OR season!=(SELECT value FROM lab_settings WHERE key='current_season'))")]
            for uid in ids:
                db.execute('UPDATE member_restrictions SET until_at=NULL WHERE user_id=?',(uid,))
                db.execute('INSERT INTO member_sync_jobs(user_id) VALUES (?) ON CONFLICT(user_id) DO UPDATE SET pending=1,generation=member_sync_jobs.generation+1,updated_at=CURRENT_TIMESTAMP',(uid,))
        return await self.drain()
