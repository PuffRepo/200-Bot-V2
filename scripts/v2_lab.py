"""Run one guild-only V2 command without loading the inherited bot or database."""

import asyncio
import getpass
from contextlib import suppress

import discord

from discord_smoke import APPLICATION_ID, DEBUG_CHANNEL_ID, LAB_GUILD_ID


async def serve_lab(token: str) -> int:
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
                commands=[v2status],
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
