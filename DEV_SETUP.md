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

From a local terminal, clone the development branch and install Pycord in an isolated environment. Python 3.10 matches the inherited project's README; current Pycord also supports Python 3.13 for this connection check.

On Linux/macOS:

```sh
git clone --branch v2-development https://github.com/PuffRepo/200-Bot-V2.git
cd 200-Bot-V2
python3.10 -m venv venv
. venv/bin/activate
python -m pip install py-cord
python scripts/discord_smoke.py
```

On Windows PowerShell with Python 3.10 installed:

```powershell
git clone --branch v2-development https://github.com/PuffRepo/200-Bot-V2.git
cd 200-Bot-V2
py -3.10 -m venv venv
.\venv\Scripts\python.exe -m pip install py-cord
.\venv\Scripts\python.exe scripts\discord_smoke.py
```

For an existing checkout, switch to `v2-development` and pull the latest commits before rerunning the check. Python 3.13 can run this check too; use a separate Python 3.10 environment when preparing the full inherited bot.

The script prompts for the development bot token without echoing it. It checks that the token belongs to application `1553807544013684807`, that the bot sees exactly the lab server, and that the debug text channel exists there. It then disconnects. This check does not load legacy cogs, touch the database, register commands, or send messages. If it fails, check the application and guild installation before proceeding.

## 3. Test V2 commands in the lab

After the connection check passes, run the small V2 lab bot from your existing Windows checkout in PowerShell:

```powershell
cd D:\Projects\200-Lounge\200-Bot-V2
git switch v2-development
git pull --ff-only origin v2-development
py -3.13 scripts\v2_lab.py
```

Use the Python interpreter where you installed `py-cord`; if you followed the Python 3.10 virtual environment steps above, run `.\venv\Scripts\python.exe scripts\v2_lab.py` instead of the last command. Enter the development token at the hidden prompt. Wait for `Lab bot ready`, then use `/v2status` in the lab `#bot-debug` channel. The bot should reply **V2 lab bot is online.** in a message visible only to you. Press **Ctrl+C** in the terminal to stop the bot.

This runner validates the application and lab server before registering commands. It does not load inherited cogs, connect to the Lounge database, send startup messages, or remove other registered commands. If `/v2status` does not appear, check that the lab application was installed with the `applications.commands` scope and that the runner printed `Lab bot ready`.

### Verification and name-change trial

The V2 identity commands use `private/v2_lab.sqlite3` in your local checkout. That file is ignored by Git. It starts empty and is **not** connected to previous-season players, the live website, or Discord roles. Use test identities in the lab; do not interpret an approval here as live verification.

| Who | Command in lab `#bot-debug` | Effect |
| --- | --- | --- |
| Player | `/v2verify profile_url:<your MKCentral profile URL>` | Creates a pending claim using the numeric profile ID. No role is granted. |
| Staff with **Manage Server** | `/v2pending` | Lists up to ten pending claims and name requests. |
| Staff | `/v2verify_review request_id:<ID> decision:approve linked_discord_id:<ID on MKCentral profile> player_name:<leaderboard name>` | Records approval only after staff checks that the profile's linked Discord ID matches the claiming user. The submitted linked ID must equal the Discord ID on the claim. |
| Staff | `/v2verify_review request_id:<ID> decision:deny` | Denies the pending claim and releases its MKCentral ID for a new request. |
| Verified lab player | `/v2name name:<new name>` | Queues a unique name for review. A 60-day cooldown starts on approval. |
| Staff | `/v2name_review request_id:<ID> decision:approve` or `decision:deny` | Decides a pending name request in local state. Discord nickname is not changed. |

For an approval, staff must open the MKCentral profile themselves and verify the **linked Discord account ID**. A profile URL, the ID typed into the command, and this bot's local database do not independently establish ownership. If the profile does not expose an authoritative linked ID, deny or defer the request until there is an agreed verification method. The bot never calls MKCentral automatically in this trial. Names are restricted to 1–16 letters, numbers, hyphens, underscores, or periods; spaces become hyphens. The 60-day name policy comes from the inherited settings and needs owner review before a live release.

## 4. Prepare the full inherited bot before running `main.py`

- Install the remaining dependencies with `python -m pip install -r requirements.txt` in the same virtual environment. Use a separate MySQL/MariaDB database for development.
- Copy `constants_example.py` to `constants.py`; the latter is already ignored by Git. Set the development token and database credentials locally. Set `BOT_ID = 1553807544013684807`, `LOUNGE = [1553806194257432726]`, and `DEBUG_CHANNEL_ID = 1553807230598647808` there. Do not commit `constants.py`.
- Create lab equivalents for the other channels and roles in `constants_example.py`, then set their local IDs in `constants.py`.
- `main.py` loads every extension listed in `config.ini`. Its startup code posts to `DEBUG_CHANNEL_ID` and leaves every server except `LOUNGE[0]`; incorrect IDs can prevent startup or remove the bot from a server. Review this behavior during V2 refactoring.
- `sql/development_init.sql` **drops tables** and seeds IDs from the old server. Use it only on a disposable development database, then replace or remove old server IDs before exercising features.
- Audit moderation permissions, hard coded server IDs, third party integrations, and dependency versions before broad testing. Plan a deliberate migration and rollback before replacing the live bot.
