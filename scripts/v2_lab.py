"""Run one guild-only V2 command without loading the inherited bot or database."""

import asyncio
import getpass
import os
import sys
from contextlib import suppress
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import discord

from discord_smoke import APPLICATION_ID, DEBUG_CHANNEL_ID, LAB_GUILD_ID
from v2.lab_store import LabError, LabStore


async def serve_lab(token: str) -> int:
    store = LabStore(Path(os.environ.get("V2_LAB_DB", "lab-v2.sqlite3")))
    intents = discord.Intents.none()
    intents.guilds = True
    bot = discord.Bot(
        intents=intents,
        debug_guilds=[LAB_GUILD_ID],
        auto_sync_commands=False,
    )

    @bot.slash_command(
        name="v2status",
        description="Check the V2 lab bot",
        guild_ids=[LAB_GUILD_ID],
    )
    async def v2status(ctx: discord.ApplicationContext) -> None:
        if ctx.guild_id != LAB_GUILD_ID or ctx.channel_id != DEBUG_CHANNEL_ID:
            await ctx.respond("Use this command in the lab #bot-debug channel.", ephemeral=True)
            return
        await ctx.respond("V2 lab bot is online.", ephemeral=True)

    def lab_channel(ctx: discord.ApplicationContext) -> bool:
        return ctx.guild_id == LAB_GUILD_ID and ctx.channel_id == DEBUG_CHANNEL_ID

    def staff(ctx: discord.ApplicationContext) -> bool:
        return lab_channel(ctx) and ctx.author.guild_permissions.manage_guild

    async def reply_error(ctx: discord.ApplicationContext, error: LabError) -> None:
        await ctx.respond(str(error), ephemeral=True)

    @bot.slash_command(name="v2verify", description="Request lab verification", guild_ids=[LAB_GUILD_ID])
    async def v2verify(ctx: discord.ApplicationContext, profile: str):
        if not lab_channel(ctx):
            return await ctx.respond("Use lab #bot-debug.", ephemeral=True)
        try:
            request_id = store.request_verify(ctx.author.id, profile)
        except LabError as error:
            return await reply_error(ctx, error)
        await ctx.respond(f"Lab verification request #{request_id} recorded for staff review.", ephemeral=True)

    @bot.slash_command(name="v2name", description="Request a lab player name", guild_ids=[LAB_GUILD_ID])
    async def v2name(ctx: discord.ApplicationContext, name: str):
        if not lab_channel(ctx):
            return await ctx.respond("Use lab #bot-debug.", ephemeral=True)
        try:
            request_id = store.request_name(ctx.author.id, name)
        except LabError as error:
            return await reply_error(ctx, error)
        await ctx.respond(f"Lab name request #{request_id} recorded for staff review.", ephemeral=True)

    @bot.slash_command(name="v2review", description="Review a lab verification or name request", guild_ids=[LAB_GUILD_ID])
    async def v2review(ctx: discord.ApplicationContext, request_id: int, approve: bool,
                       verified_name: str = ""):
        if not staff(ctx):
            return await ctx.respond("Lab Manage Server permission required in #bot-debug.", ephemeral=True)
        try:
            status = store.review(request_id, ctx.author.id, approve, verified_name)
        except LabError as error:
            return await reply_error(ctx, error)
        await ctx.respond(f"Lab request #{request_id}: {status}.", ephemeral=True)

    @bot.slash_command(name="v2pending", description="List pending lab requests", guild_ids=[LAB_GUILD_ID])
    async def v2pending(ctx: discord.ApplicationContext):
        if not staff(ctx):
            return await ctx.respond("Lab Manage Server permission required in #bot-debug.", ephemeral=True)
        with store.connect() as db:
            requests = db.execute("SELECT id,kind,user_id,value FROM requests WHERE status='pending' ORDER BY id LIMIT 15").fetchall()
        listing = "\n".join(f"#{r['id']} {r['kind']} <@{r['user_id']}>: {r['value']}" for r in requests)
        await ctx.respond(listing or "No pending lab requests.", ephemeral=True)

    @bot.slash_command(name="v2strike", description="Record a lab strike without changing MMR", guild_ids=[LAB_GUILD_ID])
    async def v2strike(ctx: discord.ApplicationContext, user_id: str, reason: str, mmr_penalty: int = 0):
        if not staff(ctx):
            return await ctx.respond("Lab Manage Server permission required in #bot-debug.", ephemeral=True)
        try:
            if not user_id.isdecimal():
                raise LabError("Enter a numeric Discord user ID.")
            strike_id = store.strike(ctx.author.id, int(user_id), reason, mmr_penalty)
        except LabError as error:
            return await reply_error(ctx, error)
        await ctx.respond(f"Lab strike #{strike_id} recorded; MMR was not changed.", ephemeral=True)

    @bot.slash_command(name="v2table", description="Record a lab results table for review", guild_ids=[LAB_GUILD_ID])
    async def v2table(ctx: discord.ApplicationContext, format_size: int, scores: str):
        if not staff(ctx):
            return await ctx.respond("Lab Manage Server permission required in #bot-debug.", ephemeral=True)
        try:
            table_id = store.submit_table(ctx.author.id, format_size, scores)
        except LabError as error:
            return await reply_error(ctx, error)
        await ctx.respond(f"Lab table #{table_id} recorded as pending; MMR was not changed.", ephemeral=True)

    connection = asyncio.create_task(bot.start(token, reconnect=False))
    ready = asyncio.create_task(bot.wait_until_ready())
    try:
        done, _ = await asyncio.wait(
            (connection, ready), timeout=60, return_when=asyncio.FIRST_COMPLETED
        )
        if connection in done:
            connection.result()
            print("Disconnected before the lab bot was ready.")
            return 1
        if ready not in done:
            print("Timed out waiting for the lab bot to become ready.")
            return 1

        try:
            application = await bot.application_info()
        except discord.HTTPException as error:
            print(f"Could not read bot application information (HTTP {error.status}).")
            return 1
        if application.id != APPLICATION_ID:
            print(f"Wrong bot application: expected {APPLICATION_ID}, got {application.id}.")
            return 1
        if {guild.id for guild in bot.guilds} != {LAB_GUILD_ID}:
            print(f"Lab isolation check failed: expected only server {LAB_GUILD_ID}.")
            return 1

        guild = bot.get_guild(LAB_GUILD_ID)
        channel = guild.get_channel(DEBUG_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            print(f"Lab debug text channel {DEBUG_CHANNEL_ID} was not found.")
            return 1

        try:
            await bot.sync_commands(
                commands=[v2status, v2verify, v2name, v2pending, v2review, v2strike, v2table],
                method="individual",
                guild_ids=[LAB_GUILD_ID],
                delete_existing=False,
            )
        except discord.HTTPException as error:
            print(f"Could not register /v2status in the lab (HTTP {error.status}).")
            return 1

        print(f"Lab bot ready as {bot.user}. Try /v2status in #{channel.name}; Ctrl+C stops it.")
        await connection
        return 0
    finally:
        ready.cancel()
        with suppress(asyncio.CancelledError):
            await ready
        if not connection.done():
            connection.cancel()
            with suppress(asyncio.CancelledError):
                await connection
        await bot.close()


def main() -> int:
    token = getpass.getpass("Development bot token (hidden): ")
    if not token:
        print("A development bot token is required.")
        return 2

    try:
        return asyncio.run(serve_lab(token))
    except discord.LoginFailure:
        print("Discord rejected the development bot token.")
        return 2
    except KeyboardInterrupt:
        print("Lab bot stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
