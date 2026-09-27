"""Run isolated V2 identity workflows in the development Discord server."""

import asyncio
import getpass
import sys
from contextlib import suppress
from pathlib import Path

# Support the existing `python scripts/v2_lab.py` command from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import discord  # noqa: E402

from scripts.discord_smoke import APPLICATION_ID, DEBUG_CHANNEL_ID, LAB_GUILD_ID  # noqa: E402
from v2.identity import IdentityStore, WorkflowError  # noqa: E402


def lab_channel(ctx: discord.ApplicationContext) -> bool:
    return ctx.guild_id == LAB_GUILD_ID and ctx.channel_id == DEBUG_CHANNEL_ID


def lab_staff(ctx: discord.ApplicationContext) -> bool:
    permissions = getattr(ctx.author, "guild_permissions", None)
    return lab_channel(ctx) and bool(permissions and permissions.manage_guild)


async def serve_lab(token: str) -> int:
    store = IdentityStore(Path(__file__).resolve().parents[1] / "private" / "v2_lab.sqlite3")
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
        if not lab_channel(ctx):
            await ctx.respond("Use this command in the lab #bot-debug channel.", ephemeral=True)
            return
        await ctx.respond("V2 lab bot is online.", ephemeral=True)

    @bot.slash_command(name="v2verify", description="Request MKCentral verification in the lab", guild_ids=[LAB_GUILD_ID])
    async def v2verify(ctx: discord.ApplicationContext, profile_url: str) -> None:
        if not lab_channel(ctx):
            await ctx.respond("Use the lab #bot-debug channel.", ephemeral=True)
            return
        try:
            request_id = store.submit_verification(ctx.author.id, profile_url)
        except WorkflowError as error:
            await ctx.respond(str(error), ephemeral=True)
            return
        await ctx.respond(
            f"Verification request #{request_id} is pending staff review. "
            "A moderator must check that the MKCentral profile is linked to your Discord account.",
            ephemeral=True,
        )

    @bot.slash_command(name="v2name", description="Request a leaderboard name change in the lab", guild_ids=[LAB_GUILD_ID])
    async def v2name(ctx: discord.ApplicationContext, name: str) -> None:
        if not lab_channel(ctx):
            await ctx.respond("Use the lab #bot-debug channel.", ephemeral=True)
            return
        try:
            request_id = store.submit_name(ctx.author.id, name)
        except WorkflowError as error:
            await ctx.respond(str(error), ephemeral=True)
            return
        await ctx.respond(f"Name request #{request_id} is pending staff review.", ephemeral=True)

    @bot.slash_command(name="v2pending", description="List pending lab identity requests (staff)", guild_ids=[LAB_GUILD_ID])
    async def v2pending(ctx: discord.ApplicationContext) -> None:
        if not lab_staff(ctx):
            await ctx.respond("Use this in lab #bot-debug with Manage Server permission.", ephemeral=True)
            return
        verifications, names = store.pending()
        lines = ["Verification requests (ID: Discord user → MKCentral ID):"]
        lines += [f"#{request_id}: {player_id} → {mkc_id}" for request_id, player_id, mkc_id in verifications]
        lines += ["Name requests (ID: Discord user → requested name):"]
        lines += [f"#{request_id}: {player_id} → {name}" for request_id, player_id, name in names]
        await ctx.respond("\n".join(lines), ephemeral=True)

    @bot.slash_command(name="v2verify_review", description="Review a lab verification claim (staff)", guild_ids=[LAB_GUILD_ID])
    async def v2verify_review(
        ctx: discord.ApplicationContext, request_id: int, decision: str,
        linked_discord_id: str = "", player_name: str = "",
    ) -> None:
        if not lab_staff(ctx):
            await ctx.respond("Use this in lab #bot-debug with Manage Server permission.", ephemeral=True)
            return
        if decision.lower() not in ("approve", "deny"):
            await ctx.respond("Decision must be approve or deny.", ephemeral=True)
            return
        approve = decision.lower() == "approve"
        try:
            player_id, mkc_id = store.verification_claim(request_id)
            if approve and linked_discord_id != str(player_id):
                raise WorkflowError(
                    f"Confirm that MKCentral player {mkc_id} lists Discord ID {player_id}; "
                    "enter that ID from the profile before approval."
                )
            result = store.review_verification(
                request_id, ctx.author.id, approve=approve, player_name=player_name,
            )
        except WorkflowError as error:
            await ctx.respond(str(error), ephemeral=True)
            return
        status = "approved" if approve else "denied"
        await ctx.respond(
            f"Verification request #{request_id} {status} for Discord user {result.player_id}. "
            "Saved in the local lab database; no Lounge roles were assigned.",
            ephemeral=True,
        )

    @bot.slash_command(name="v2name_review", description="Review a lab name change (staff)", guild_ids=[LAB_GUILD_ID])
    async def v2name_review(ctx: discord.ApplicationContext, request_id: int, decision: str) -> None:
        if not lab_staff(ctx):
            await ctx.respond("Use this in lab #bot-debug with Manage Server permission.", ephemeral=True)
            return
        if decision.lower() not in ("approve", "deny"):
            await ctx.respond("Decision must be approve or deny.", ephemeral=True)
            return
        try:
            result = store.review_name(request_id, ctx.author.id, approve=decision.lower() == "approve")
        except WorkflowError as error:
            await ctx.respond(str(error), ephemeral=True)
            return
        status = "approved" if decision.lower() == "approve" else "denied"
        await ctx.respond(
            f"Name request #{request_id} {status} for Discord user {result.player_id}. "
            "The local lab leaderboard name was updated if approved; Discord nickname was not changed.",
            ephemeral=True,
        )

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
                commands=[v2status, v2verify, v2name, v2pending, v2verify_review, v2name_review],
                method="individual",
                guild_ids=[LAB_GUILD_ID],
                delete_existing=False,
            )
        except discord.HTTPException as error:
            print(f"Could not register V2 lab commands (HTTP {error.status}).")
            return 1

        print(f"Lab bot ready as {bot.user}. Try /v2status in #{channel.name}; Ctrl+C stops it.")
        print("Identity and name requests use only private/v2_lab.sqlite3; no live database or roles.")
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
