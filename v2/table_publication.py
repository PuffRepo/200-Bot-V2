"""Lab-only results delivery from committed rating events, never reapplies MMR."""
import asyncio
from datetime import datetime,timezone
from io import BytesIO
import discord
from v2.lab_store import LabError, parse_scores
from v2.table_image import render_results,render_table,results_date

class TablePublisher:
    def __init__(self,store,bot,guild_id,channels):
        self.store,self.bot,self.guild_id,self.channels=store,bot,guild_id,channels
        self.lock=asyncio.Lock()
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS table_retractions(table_id INTEGER PRIMARY KEY,results_deleted INTEGER NOT NULL DEFAULT 0,mmr_deleted INTEGER NOT NULL DEFAULT 0)')
            db.execute('''CREATE TABLE IF NOT EXISTS table_publications(
                table_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL,
                results_id INTEGER, mmr_id INTEGER, results_state TEXT NOT NULL DEFAULT 'new',
                mmr_state TEXT NOT NULL DEFAULT 'new', results_started TEXT, mmr_started TEXT)''')

    async def destination(self,tier):
        channel_id=self.channels.get(tier)
        if not channel_id:raise LabError(f'Configure the lab results channel for {tier} before submitting.')
        channel=self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        if not isinstance(channel,discord.TextChannel) or channel.guild.id!=self.guild_id:
            raise LabError('Results destination must be a text channel in the lab guild.')
        permissions=channel.permissions_for(channel.guild.me)
        if not all(getattr(permissions,p,False) for p in ('view_channel','send_messages','attach_files','embed_links','read_message_history')):
            raise LabError('Results channel needs View Channel, Send Messages, Attach Files, Embed Links and Read Message History for the bot.')
        return channel

    async def prepare(self,table_id,tier):
        channel=await self.destination(tier)
        with self.store.connect() as db:
            db.execute('INSERT INTO table_publications(table_id,channel_id) VALUES (?,?)',(table_id,channel.id))

    def pending(self):
        with self.store.connect() as db:
            return [r[0] for r in db.execute("SELECT p.table_id FROM table_publications p JOIN tables t ON t.id=p.table_id WHERE t.status='applied' AND (p.results_id IS NULL OR p.mmr_id IS NULL) ORDER BY p.table_id")]

    async def publish(self,table_id):
        async with self.lock:
            with self.store.connect() as db:
                table=dict(db.execute('SELECT * FROM tables WHERE id=?',(table_id,)).fetchone())
                job=db.execute('SELECT * FROM table_publications WHERE table_id=?',(table_id,)).fetchone()
                if not job:raise LabError('This table has no prepared results destination.')
                job=dict(job)
                if table['status']!='applied':raise LabError('Only applied tables can be published.')
                events={r['name']:dict(r) for r in db.execute('SELECT * FROM rating_events WHERE table_id=?',(table_id,))}
            channel=await self.destination(table['tier'])
            if channel.id!=job['channel_id']:raise LabError('Destination changed; restore the configured channel before retrying publication.')
            if table['replaces_table_id'] is not None:
                await self._retract(table['replaces_table_id'])
            pairs=parse_scores(table['scores'],table['format_size']);rows=[]
            from v2.season_policy import load_policy,rank_for_mmr
            from v2.lab_ratings import POLICY_PATH
            policy=load_policy(POLICY_PATH)
            for name, score in pairs:
                e=events[name]
                rows.append({'name':e['name'],'before':e['before_mmr'],'after':e['after_mmr'],
                             'applied_delta':e['applied_delta'],'kind':e['kind'],'rank':rank_for_mmr(policy,e['after_mmr'])})
            for part in ('results','mmr'):
                if job[part+'_id']:continue
                marker=f'200 Lounge V2 | Table {table_id} | {part}'
                if job[part+'_state']=='sending':
                    after=datetime.fromisoformat(job[part+'_started']).replace(tzinfo=timezone.utc)
                    matches=[]
                    async for message in channel.history(limit=None,after=after):
                        if message.author.id==self.bot.user.id and any(getattr(embed.footer,'text',None)==marker for embed in message.embeds):matches.append(message)
                    if len(matches)!=1:
                        raise LabError('Previous send outcome is uncertain. Staff must inspect the destination; automatic resend is blocked to avoid duplicate posts.')
                    message=matches[0]
                else:
                    renderer=render_results if part=='results' else render_table
                    args=(table_id,table['format_size'],table['tier'],table['scores'],rows)
                    if part=='results':args+= (results_date(table['created_at']),)
                    png=await asyncio.to_thread(renderer,*args)
                    embed=discord.Embed(title=f"{table['tier']} {'Results' if part=='results' else 'MMR'}",color=discord.Color.blurple())
                    embed.add_field(name='Table ID',value=str(table_id),inline=True)
                    embed.add_field(name='Tier',value=table['tier'],inline=True)
                    embed.add_field(name='Season',value=table['season'],inline=True)
                    if table['replaces_table_id'] is not None:
                        embed.add_field(name='Replaces',value=f"Table #{table['replaces_table_id']}",inline=True)
                    embed.add_field(name='Submitted by',value=f"<@{table['reporter_id']}>",inline=False)
                    embed.set_footer(text=marker);embed.set_image(url=f'attachment://{part}.png')
                    with self.store.connect() as db:
                        db.execute(f"UPDATE table_publications SET {part}_state='sending',{part}_started=CURRENT_TIMESTAMP WHERE table_id=?",(table_id,))
                    try:
                        message=await channel.send(embed=embed,file=discord.File(BytesIO(png),filename=part+'.png'),allowed_mentions=discord.AllowedMentions.none())
                    except discord.HTTPException as error:
                        if 400<=error.status<500:
                            with self.store.connect() as db:db.execute(f"UPDATE table_publications SET {part}_state='new' WHERE table_id=?",(table_id,))
                        raise
                with self.store.connect() as db:
                    db.execute(f"UPDATE table_publications SET {part}_id=?,{part}_state='sent' WHERE table_id=?",(message.id,table_id))
                job[part+'_id']=message.id


    def pending_retractions(self):
        with self.store.connect() as db:
            return [r[0] for r in db.execute("SELECT p.table_id FROM table_publications p JOIN tables t ON t.id=p.table_id LEFT JOIN table_retractions r ON r.table_id=p.table_id WHERE t.status='reversed' AND (r.table_id IS NULL OR r.results_deleted=0 OR r.mmr_deleted=0)")]

    async def retract(self,table_id):
        async with self.lock:return await self._retract(table_id)

    async def _retract(self,table_id):
        with self.store.connect() as db:
            table=db.execute('SELECT * FROM tables WHERE id=?',(table_id,)).fetchone()
            if table is None or table['status']!='reversed':raise LabError('Only an audited reversed table can have results removed.')
            job=db.execute('SELECT * FROM table_publications WHERE table_id=?',(table_id,)).fetchone()
            if not job:return
            job=dict(job)
            db.execute('INSERT OR IGNORE INTO table_retractions(table_id) VALUES (?)',(table_id,))
            progress=dict(db.execute('SELECT * FROM table_retractions WHERE table_id=?',(table_id,)).fetchone())
        channel=await self.destination(table['tier'])
        if channel.id!=job['channel_id']:raise LabError('Restore the original results destination before removing reversed posts.')
        for part in ('results','mmr'):
            if progress[part+'_deleted']:continue
            message_id=job[part+'_id']
            if message_id is None and job[part+'_state']=='sending':
                after=datetime.fromisoformat(job[part+'_started']).replace(tzinfo=timezone.utc)
                marker=f'200 Lounge V2 | Table {table_id} | {part}'
                matches=[]
                async for message in channel.history(limit=None,after=after):
                    if message.author.id==self.bot.user.id and any(getattr(embed.footer,'text',None)==marker for embed in message.embeds):matches.append(message.id)
                if len(matches)!=1:raise LabError('Uncertain original publication needs staff inspection before replacement publication.')
                message_id=matches[0]
            if message_id:
                try:
                    message=await channel.fetch_message(message_id)
                    if message.author.id!=self.bot.user.id:raise LabError('Refusing to delete a post not authored by this bot.')
                    await message.delete()
                except discord.HTTPException as error:
                    if error.status!=404:raise
            with self.store.connect() as db:
                db.execute(f'UPDATE table_retractions SET {part}_deleted=1 WHERE table_id=?',(table_id,))
