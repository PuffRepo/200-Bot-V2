"""Read-only Discord connection check for the V2 development bot."""

import argparse
import getpass

import discord


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("guild_id", type=int, help="ID of the lab server")
    args = parser.parse_args()
    if args.guild_id <= 0:
        parser.error("guild_id must be a positive Discord server ID")

    token = getpass.getpass("Development bot token (hidden): ")
    if not token:
        parser.error("a development bot token is required")

    intents = discord.Intents.none()
    intents.guilds = True
    bot = discord.Bot(intents=intents)
    connected_to_lab = False

    @bot.event
    async def on_ready() -> None:
        nonlocal connected_to_lab
        guilds = {guild.id: guild for guild in bot.guilds}
        if set(guilds) == {args.guild_id}:
            connected_to_lab = True
            print(f"Connected as {bot.user} to lab server: {guilds[args.guild_id].name}")
        else:
            print(
                "Lab isolation check failed: expected only server "
                f"{args.guild_id}; bot currently sees {len(guilds)} server(s)."
            )
        await bot.close()

    try:
        bot.run(token)
    except discord.LoginFailure:
        print("Discord rejected the development bot token.")
        return 2
    return 0 if connected_to_lab else 1


if __name__ == "__main__":
    raise SystemExit(main())
