"""Reviewer-bound buttons and record pickers; snapshot codes stay inside the bot."""
import asyncio
import logging
import discord
from v2.lab_store import LabError

class ReviewButtons(discord.ui.View):
    def __init__(self,reviewer_id,authorized,preview,refresh,approve,reject,approve_label="Approve",reject_label="Reject"):
        super().__init__(timeout=300)
        self.reviewer_id,self.authorized=reviewer_id,authorized
        self.preview,self.refresh,self.approve,self.reject=preview,refresh,approve,reject
        self.finished=False;self.lock=asyncio.Lock();self.message=None
        for child in self.children:
            if child.label=='Approve':child.label=approve_label
            elif child.label=='Reject':child.label=reject_label
    async def interaction_check(self,interaction):
        if interaction.user.id!=self.reviewer_id or not self.authorized(interaction):
            await interaction.response.send_message('Only the authorized reviewer who opened this preview can use it.',ephemeral=True)
            return False
        return True
    def disable(self):
        self.finished=True
        for child in self.children:child.disabled=True
        self.stop()
    @discord.ui.button(label='Approve',style=discord.ButtonStyle.green)
    async def approve_button(self,button,interaction):
        await interaction.response.defer()
        async with self.lock:
            if self.finished:return await interaction.followup.send('This review has finished.',ephemeral=True)
            try:message=await self.approve(interaction,self.preview)
            except LabError as error:
                try:new=await self.refresh()
                except LabError:return await interaction.followup.send(str(error),ephemeral=True)
                if new['digest']!=self.preview['digest']:
                    self.preview=new
                    return await interaction.edit_original_response(content=new['content']+'\nPreview changed. Nothing applied; review it and press Approve again.',view=self)
                return await interaction.followup.send(str(error),ephemeral=True)
            except Exception:
                logging.exception('Staff approval failed')
                return await interaction.followup.send('Approval failed. Inspect the bot log and current record before retrying.',ephemeral=True)
            self.disable();await interaction.edit_original_response(content=message,view=self)
    @discord.ui.button(label='Reject',style=discord.ButtonStyle.red)
    async def reject_button(self,button,interaction):
        await interaction.response.defer()
        async with self.lock:
            if self.finished:return await interaction.followup.send('This review has finished.',ephemeral=True)
            try:message=await self.reject(interaction)
            except LabError as error:return await interaction.followup.send(str(error),ephemeral=True)
            except Exception:
                logging.exception('Staff rejection failed')
                return await interaction.followup.send('Rejection failed. Inspect the bot log and current record before retrying.',ephemeral=True)
            self.disable();await interaction.edit_original_response(content=message,view=self)
    async def on_timeout(self):
        async with self.lock:
            if self.finished:return
            self.disable()
            if self.message:
                try:await self.message.edit(content='Review expired without an approval. Reopen the queue to review again.',view=self)
                except discord.HTTPException:pass

class ActionView(discord.ui.View):
    def __init__(self,label,authorized,action):
        super().__init__(timeout=300);self.authorized=authorized;self.action=action
        button=discord.ui.Button(label=label,style=discord.ButtonStyle.primary)
        button.callback=self.clicked;self.add_item(button)
    async def clicked(self,interaction):
        if not self.authorized(interaction):return await interaction.response.send_message('You are not authorized for this action.',ephemeral=True)
        await self.action(interaction)

class ChoiceView(discord.ui.View):
    def __init__(self,actor_id,authorized,options,choose):
        super().__init__(timeout=300);self.actor_id,self.authorized,self.choose=actor_id,authorized,choose
        self.select=discord.ui.Select(placeholder='Choose a record to review',options=options[:25])
        self.select.callback=self.selected;self.add_item(self.select)
    async def selected(self,interaction):
        if interaction.user.id!=self.actor_id or not self.authorized(interaction):return await interaction.response.send_message('Reopen your own authorized queue to choose a record.',ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:await self.choose(interaction,self.select.values[0])
        except LabError as error:await interaction.followup.send(str(error),ephemeral=True)

class TextModal(discord.ui.Modal):
    def __init__(self,title,fields,authorized,submit):
        super().__init__(title=title,timeout=300)
        self.authorized,self.submit=authorized,submit
        for field in fields:self.add_item(discord.ui.InputText(**field))
    async def callback(self,interaction):
        if not self.authorized(interaction):return await interaction.response.send_message('You are not authorized for this action.',ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:await self.submit(interaction,[child.value for child in self.children])
        except LabError as error:await interaction.followup.send(str(error),ephemeral=True)

def original_evidence(store,guild_id,table_id,provided=''):
    if provided:return provided
    with store.connect() as db:
        row=db.execute('SELECT channel_id,results_id,mmr_id FROM table_publications WHERE table_id=?',(table_id,)).fetchone()
        if row and (row['results_id'] or row['mmr_id']):return f"https://discord.com/channels/{guild_id}/{row['channel_id']}/{row['results_id'] or row['mmr_id']}"
        request=db.execute('SELECT evidence FROM table_reversal_requests WHERE table_id=? ORDER BY id DESC LIMIT 1',(table_id,)).fetchone()
        if request:return request['evidence']
    raise LabError('Original result link is unavailable; supply evidence for this older result.')

def recorded_result(store,target_guild,guild_id,channel_id,message_id,author_id,bot_id,channels):
    if guild_id!=target_guild or channel_id not in channels.values() or author_id!=bot_id:
        raise LabError('Use this on a recorded V2 post in a configured results channel.')
    with store.connect() as db:
        row=db.execute('SELECT table_id FROM table_publications WHERE channel_id=? AND (results_id=? OR mmr_id=?)',(channel_id,message_id,message_id)).fetchone()
    if not row:raise LabError('This is not a recorded V2 result post.')
    return row['table_id']
