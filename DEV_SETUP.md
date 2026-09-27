# 200-Bot V2 development lab

This branch prepares an isolated Discord server and application for the MK8DX 200cc Lounge bot. Keep the archived code on `main` as a comparison point while V2 is rebuilt.

## 1. Provision Discord resources

1. In Discord, create a server named **200 Lounge V2 Lab** using **Add a Server → Create My Own**. Use this server for all development and test data.
2. In the [Discord Developer Portal](https://discord.com/developers/applications), create an application named **200 Lounge V2 Dev**. New applications include a bot user.
3. On **Bot → Privileged Gateway Intents**, enable **Guild Members** and **Message Content**. The current `main.py` requests `members=True` and `message_content=True`. It does not request Presence.
4. On **Installation**, use **Guild Install** and a **Discord Provided Link** with `bot` and `applications.commands` scopes. Start with permissions to view channels, send messages, embed links, read message history, and add reactions. Add moderation permissions only after reviewing the commands that need them; do not grant Administrator by default.
5. Use the installation link to add the development bot **only** to the lab server. Do not invite this application to the production 200 Lounge.
6. Save the bot token in a local secret manager. Never put it in Git, an issue, a PR, or chat.

Record the following **non-secret** IDs for local configuration:

| Setting | Value |
| --- | --- |
| Development application / bot user ID | (fill locally) |
| Lab server (guild) ID | (fill locally) |
| `#bot-debug` channel ID | (fill locally) |

Enable Discord Developer Mode to copy IDs from the application, server, and channel interfaces.

## 2. Prepare a local checkout

Use Python 3.10 as documented in the upstream README, a virtual environment, and a separate MySQL/MariaDB database. Install `requirements.txt` in that environment. Copy `constants_example.py` to `constants.py`; the latter is already ignored by Git. Set only the **development** token, bot ID, `LOUNGE = [lab_guild_id]`, debug channel ID, and development database credentials there. Do not commit `constants.py`.

Run `python scripts/discord_smoke.py LAB_GUILD_ID` first. It prompts for the development token without echoing it, checks that the application can connect to exactly the expected lab server, and exits without loading legacy cogs, touching the database, or sending messages.

## 3. Prepare the full legacy bot before running `main.py`

- Create lab equivalents for channels and roles in `constants_example.py`, then replace their local IDs in `constants.py`.
- `main.py` loads every extension listed in `config.ini`. Its startup code posts to `DEBUG_CHANNEL_ID` and leaves every server except `LOUNGE[0]`; incorrect IDs can prevent startup or remove the bot from a server. Review this behavior during V2 refactoring.
- `sql/development_init.sql` **drops tables** and seeds IDs from the old server. Use it only on a disposable development database, then replace or remove old-server IDs before exercising features.
- Audit moderation permissions, hard-coded server IDs, third-party integrations, and dependency versions before broad testing.
- Keep the development application and database separate from production. Plan a deliberate migration and rollback before replacing the live bot.
