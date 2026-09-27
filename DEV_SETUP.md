# 200-Bot V2 development lab

This branch prepares an isolated Discord server and application for the MK8DX 200cc Lounge bot. Keep the archived code on `main` as a comparison point while V2 is rebuilt.

## 1. Discord lab resources

Use the separate development application and lab server for all tests. The IDs below are identifiers, not credentials.

| Setting | Lab value |
| --- | --- |
| Development application ID | `1553807544013684807` |
| Lab server (guild) ID | `1553806194257432726` |
| `#bot-debug` channel ID | `1553807230598647808` |

In the [Discord Developer Portal](https://discord.com/developers/applications), make sure the development application has a bot user. On **Bot → Privileged Gateway Intents**, enable **Guild Members** and **Message Content** before running the inherited `main.py`; that code requests both intents. The smoke check below uses only the Guilds intent.

On **Installation**, use **Guild Install** with the `bot` and `applications.commands` scopes. Start with permissions to view channels, send messages, embed links, read message history, and add reactions. Add moderation permissions only as reviewed features require them. Install this development bot only in the lab server, and keep its token in a local secret manager. Never put the token in Git, an issue, a PR, or chat.

## 2. Run the lab connection check

From a local terminal with Python 3.10, clone the development branch and install Pycord in an isolated environment:

```sh
git clone --branch v2-development https://github.com/PuffRepo/200-Bot-V2.git
cd 200-Bot-V2
python3.10 -m venv venv
. venv/bin/activate
python -m pip install py-cord
python scripts/discord_smoke.py
```

The script prompts for the development bot token without echoing it. It checks that the token belongs to application `1553807544013684807`, that the bot sees exactly the lab server, and that the debug text channel exists there. It then disconnects. This check does not load legacy cogs, touch the database, register commands, or send messages. If it fails, check the application and guild installation before proceeding.

## 3. Prepare the full inherited bot before running `main.py`

- Install the remaining dependencies with `python -m pip install -r requirements.txt` in the same virtual environment. Use a separate MySQL/MariaDB database for development.
- Copy `constants_example.py` to `constants.py`; the latter is already ignored by Git. Set the development token and database credentials locally. Set `BOT_ID = 1553807544013684807`, `LOUNGE = [1553806194257432726]`, and `DEBUG_CHANNEL_ID = 1553807230598647808` there. Do not commit `constants.py`.
- Create lab equivalents for the other channels and roles in `constants_example.py`, then set their local IDs in `constants.py`.
- `main.py` loads every extension listed in `config.ini`. Its startup code posts to `DEBUG_CHANNEL_ID` and leaves every server except `LOUNGE[0]`; incorrect IDs can prevent startup or remove the bot from a server. Review this behavior during V2 refactoring.
- `sql/development_init.sql` **drops tables** and seeds IDs from the old server. Use it only on a disposable development database, then replace or remove old server IDs before exercising features.
- Audit moderation permissions, hard coded server IDs, third party integrations, and dependency versions before broad testing. Plan a deliberate migration and rollback before replacing the live bot.
