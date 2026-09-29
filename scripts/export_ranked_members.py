"""Export rank-role membership for a season-reset preview without changing Discord.

Run on a machine with Pycord and a bot installed in the current Lounge server.
"""

import argparse
import asyncio
import csv
import getpass
import sys
from contextlib import suppress
from pathlib import Path

from scripts.season_reset_preview import read_csv, required
from v2.season_reset import RANK_NAMES


async def fetch_ranked_members(token: str, application_id: int, guild_id: int, rank_ids: set[int]):
    import discord

    intents = discord.Intents.none()
    intents.guilds = True
    intents.members = True
    client = discord.Client(intents=intents)
    connection = asyncio.create_task(client.start(token, reconnect=False))
    ready = asyncio.create_task(client.wait_until_ready())
    try:
        done, _ = await asyncio.wait(
            (connection, ready), timeout=60, return_when=asyncio.FIRST_COMPLETED
        )
        if connection in done:
            connection.result()
            raise RuntimeError("Bot disconnected before becoming ready")
        if ready not in done:
            raise RuntimeError("Timed out waiting for bot readiness")

        application = await client.application_info()
        if application.id != application_id:
            raise RuntimeError(f"Wrong application ID: {application.id}")
        guild = client.get_guild(guild_id)
        if guild is None:
            raise RuntimeError(f"Bot is not installed in guild {guild_id}")
        if any(guild.get_role(role_id) is None for role_id in rank_ids):
            raise RuntimeError("A configured rank role was not found in this guild")

        rows = []
        async for member in guild.fetch_members(limit=None):
            if not member.bot:
                for role in member.roles:
                    if role.id in rank_ids:
                        rows.append((member.id, role.id))
        return sorted(rows)
    finally:
        ready.cancel()
        with suppress(asyncio.CancelledError):
            await ready
        if not connection.done():
            connection.cancel()
            with suppress(asyncio.CancelledError):
                await connection
        await client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-id", type=int, required=True)
    parser.add_argument("--guild-id", type=int, required=True)
    parser.add_argument("--ranks", type=Path, required=True, help="Current rank IDs and MMR ranges CSV")
    parser.add_argument("--output", type=Path, required=True, help="Local ranked-members CSV")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    if args.output.resolve() == args.ranks.resolve():
        parser.error("Output may not replace the rank export")
    if args.output.exists() and not args.overwrite:
        parser.error("Output exists; choose another path or pass --overwrite")

    try:
        rank_rows = read_csv(args.ranks, {"rank_id", "rank_name", "mmr_min", "mmr_max"})
        rank_ids = {required(row["rank_id"], "rank_id") for row in rank_rows
                    if row["rank_name"].strip().casefold() in RANK_NAMES}
        if len(rank_ids) != len(RANK_NAMES):
            raise ValueError("Current rank map must include all nine rank roles, including Ruby")
        token = getpass.getpass("Bot token (hidden): ")
        if not token:
            raise ValueError("A bot token is required")
        rows = asyncio.run(fetch_ranked_members(token, args.application_id, args.guild_id, rank_ids))
        if not rows:
            raise ValueError("No ranked members found; check the server and rank IDs")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(("player_id", "rank_role_id"))
            writer.writerows(rows)
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        print(f"Rank roster export failed: {error}", file=sys.stderr)
        return 2

    print(f"Wrote {len(rows)} ranked membership entries to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
