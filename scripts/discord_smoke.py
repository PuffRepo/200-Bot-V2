"""Read-only Discord connection check for the V2 development bot."""

import asyncio
import getpass
from contextlib import suppress

import discord

APPLICATION_ID = 1553807544013684807
LAB_GUILD_ID = 1553806194257432726
DEBUG_CHANNEL_ID = 1553807230598647808


async def check_connection(token: str) -> int:
    intents = discord.Intents.none()
    intents.guilds = True
    client = discord.Client(intents=intents)
    connection = asyncio.create_task(client.start(token, reconnect=False))
    ready = asyncio.create_task(client.wait_until_ready())

    try:
        done, _ = await asyncio.wait(
            (connection, ready), timeout=60, return_when=asyncio.FIRST_COMPLETED
        )
        if connection in done:
            connection.result()  # Surface login and connection errors.
            print("Disconnected before the lab check was ready.")
            return 1
        if ready not in done:
            print("Timed out waiting for the development bot to become ready.")
            return 1

        try:
            application = await client.application_info()
        except discord.HTTPException as error:
            print(f"Could not read bot application information (HTTP {error.status}).")
            return 1

        guilds = {guild.id: guild for guild in client.guilds}
        if application.id != APPLICATION_ID:
            print(f"Wrong bot application: expected {APPLICATION_ID}, got {application.id}.")
            return 1
        if set(guilds) != {LAB_GUILD_ID}:
            print(
                "Lab isolation check failed: expected only server "
                f"{LAB_GUILD_ID}; bot currently sees {len(guilds)} server(s)."
            )
            return 1

        guild = guilds[LAB_GUILD_ID]
        channel = guild.get_channel(DEBUG_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            print(f"Lab debug text channel {DEBUG_CHANNEL_ID} was not found.")
            return 1

        print(f"Connected as {client.user} to {guild.name}; found #{channel.name}. Lab checks passed.")
        return 0
    finally:
        ready.cancel()
        with suppress(asyncio.CancelledError):
            await ready
        if not connection.done():
            connection.cancel()
            with suppress(asyncio.CancelledError):
                await connection
        await client.close()


def main() -> int:
    token = getpass.getpass("Development bot token (hidden): ")
    if not token:
        print("A development bot token is required.")
        return 2

    try:
        return asyncio.run(check_connection(token))
    except discord.LoginFailure:
        print("Discord rejected the development bot token.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
