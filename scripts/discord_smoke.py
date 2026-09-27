"""Read-only Discord connection check for the V2 development bot."""

import getpass

import discord

APPLICATION_ID = 1553807544013684807
LAB_GUILD_ID = 1553806194257432726
DEBUG_CHANNEL_ID = 1553807230598647808


def main() -> int:
    token = getpass.getpass("Development bot token (hidden): ")
    if not token:
        print("A development bot token is required.")
        return 2

    intents = discord.Intents.none()
    intents.guilds = True
    client = discord.Client(intents=intents)
    connected_to_lab = False

    @client.event
    async def on_ready() -> None:
        nonlocal connected_to_lab
        try:
            application = await client.application_info()
            guilds = {guild.id: guild for guild in client.guilds}

            if application.id != APPLICATION_ID:
                print(f"Wrong bot application: expected {APPLICATION_ID}, got {application.id}.")
            elif set(guilds) != {LAB_GUILD_ID}:
                print(
                    "Lab isolation check failed: expected only server "
                    f"{LAB_GUILD_ID}; bot currently sees {len(guilds)} server(s)."
                )
            else:
                guild = guilds[LAB_GUILD_ID]
                channel = guild.get_channel(DEBUG_CHANNEL_ID)
                if not isinstance(channel, discord.TextChannel):
                    print(f"Lab debug text channel {DEBUG_CHANNEL_ID} was not found.")
                else:
                    connected_to_lab = True
                    print(
                        f"Connected as {client.user} to {guild.name}; "
                        f"found #{channel.name}. Lab checks passed."
                    )
        except discord.HTTPException as error:
            print(f"Could not read bot application information (HTTP {error.status}).")
        finally:
            await client.close()

    try:
        client.run(token)
    except discord.LoginFailure:
        print("Discord rejected the development bot token.")
        return 2
    return 0 if connected_to_lab else 1


if __name__ == "__main__":
    raise SystemExit(main())
