"""Reporter-only Yes/No confirmation with atomic, stale-safe application."""
import asyncio
import logging
import sqlite3
import discord
from v2.lab_store import LabError
from v2.table_image import render_table, render_results, results_date

class TableButtons(discord.ui.View):
    def __init__(self, store, ratings, table_id, reporter_id, digest, authorized, publish=None):
        super().__init__(timeout=300)
        self.store,self.ratings=store,ratings
        self.table_id,self.reporter_id,self.digest=table_id,reporter_id,digest
        self.authorized=authorized
        self.publish=publish
        self.message=None
        self.lock=asyncio.Lock()
        self.finished=False
        self.store.register_button_preview(table_id,reporter_id)

    async def interaction_check(self, interaction):
        if interaction.user.id!=self.reporter_id or not self.authorized(interaction):
            await interaction.response.send_message('Only the submitting authorized Reporter can use these buttons.',ephemeral=True)
            return False
        return True

    def disable(self):
        self.finished=True
        for child in self.children:child.disabled=True
        self.stop()

    @discord.ui.button(label='Yes',style=discord.ButtonStyle.green)
    async def yes(self, button, interaction):
        # Acknowledge before any database or rendering work.
        await interaction.response.defer()
        async with self.lock:
            if self.finished:
                return await interaction.followup.send('This table confirmation has already finished.',ephemeral=True)
            try:
                await asyncio.to_thread(self.ratings.apply,self.table_id,self.reporter_id,self.digest,True)
            except LabError as error:
                if str(error).startswith('Ratings or policy changed'):
                    try:
                        result=await asyncio.to_thread(self.ratings.preview,self.table_id,True)
                        with self.store.connect() as db:
                            table=dict(db.execute('SELECT * FROM tables WHERE id=?',(self.table_id,)).fetchone())
                        results_png=await asyncio.to_thread(render_results,self.table_id,table['format_size'],table['tier'],table['scores'],result['rows'],results_date(table['created_at']))
                        from io import BytesIO
                        self.digest=result['digest']
                        await interaction.edit_original_response(content=f'Table #{self.table_id}: ratings changed. Review this refreshed table and choose Yes or No again. Nothing applied.',files=[discord.File(BytesIO(results_png),filename='results.png')],attachments=[],view=self)
                        return
                    except LabError as refresh_error:
                        error=refresh_error
                return await interaction.followup.send(f'Not applied: {error}',ephemeral=True)
            except sqlite3.Error:
                logging.exception('Table application failed; transaction rolled back')
                return await interaction.followup.send('Could not apply the table. No partial update was committed; staff should check the bot log.',ephemeral=True)
            self.disable()
            delivery=''
            if self.publish:
                try:
                    result=await self.publish(self.table_id)
                    delivery=' '+(result or 'Results and MMR embeds published to the tier results channel.')
                except Exception:
                    logging.exception('Applied table publication pending: %s',self.table_id)
                    delivery=' Ratings applied; results delivery is pending. Staff can retry publication without reapplying ratings.'
            await interaction.edit_original_response(content=f'Table #{self.table_id} confirmed and applied once.'+delivery,view=self)

    @discord.ui.button(label='No',style=discord.ButtonStyle.red)
    async def no(self, button, interaction):
        await interaction.response.defer()
        async with self.lock:
            if self.finished:
                return await interaction.followup.send('This table confirmation has already finished.',ephemeral=True)
            try:
                await asyncio.to_thread(self.store.cancel_table,self.table_id,self.reporter_id)
            except LabError as error:
                return await interaction.followup.send(str(error),ephemeral=True)
            self.disable()
            await interaction.edit_original_response(content=f'Table #{self.table_id} canceled. MMR unchanged. Submit corrected scores with /v2table.',view=self)

    async def on_timeout(self):
        async with self.lock:
            if self.finished:return
            try:
                await asyncio.to_thread(self.store.cancel_table,self.table_id,self.reporter_id,'Confirmation expired after five minutes')
            except LabError:
                pass
            self.disable()
            if self.message:
                try:
                    await self.message.edit(content=f'Table #{self.table_id}: confirmation expired. No rating application was made by this timeout. Submit again if needed.',view=self)
                except discord.HTTPException:
                    pass
