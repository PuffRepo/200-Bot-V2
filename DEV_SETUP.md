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

On **Installation**, use **Guild Install** with the `bot` and `applications.commands` scopes. Start with permissions to view channels, send messages, embed links, read message history, and add reactions. Add moderation permissions only as reviewed features require them. Use the lab server for V2 commands; the dev bot may also remain in the one approved live server for the read-only archive. Keep its token in a local secret manager. Never put the token in Git, an issue, a PR, or chat.

## 2. Run the lab connection check

From a local terminal, clone the development branch and install Pycord in an isolated environment. Python 3.10 matches the inherited project's README; current Pycord also supports Python 3.13 for this connection check.

On Linux/macOS:

```sh
git clone --branch v2-season-reset-preview https://github.com/PuffRepo/200-Bot-V2.git
cd 200-Bot-V2
python3.10 -m venv venv
. venv/bin/activate
python -m pip install py-cord
python scripts/discord_smoke.py
```

On Windows PowerShell with Python 3.10 installed:

```powershell
git clone --branch v2-season-reset-preview https://github.com/PuffRepo/200-Bot-V2.git
cd 200-Bot-V2
py -3.10 -m venv venv
.\venv\Scripts\python.exe -m pip install py-cord
.\venv\Scripts\python.exe scripts\discord_smoke.py
```

For an existing checkout, switch to `v2-season-reset-preview` and pull the latest commits before rerunning the check. Python 3.13 can run this check too; use a separate Python 3.10 environment when preparing the full inherited bot.

The script prompts for the development bot token without echoing it. It checks that the token belongs to application `1553807544013684807`, that the bot sees exactly the lab server, and that the debug text channel exists there. It then disconnects. This check does not load legacy cogs, touch the database, register commands, or send messages. **Skip this smoke check while the bot remains in the live archive server**; the V2 lab runner below accepts both known guilds safely.

## 3. Test V2 commands in the lab

After the connection check passes, run the V2 lab bot from your existing Windows checkout in PowerShell:

```powershell
cd D:\Projects\200-Lounge\200-Bot-V2
git switch v2-season-reset-preview
git pull --ff-only origin v2-season-reset-preview
py -3.13 scripts\v2_lab.py
```

Use the Python interpreter where you installed `py-cord`; if you followed the Python 3.10 virtual environment steps above, run `.\venv\Scripts\python.exe scripts\v2_lab.py` instead of the last command. Enter the development token at the hidden prompt. Wait for `Lab bot ready`, then use `/v2status` in the lab `#bot-debug` channel. The bot should reply **V2 lab bot is online.** in a message visible only to you. Press **Ctrl+C** in the terminal to stop the bot.

For later sessions, open PowerShell in the checkout and run `.\start-v2-lab.cmd`.
This command file works even if PowerShell script execution is disabled. Both
the `.cmd` launcher and the optional `.ps1` launcher require the project's
`venv\Scripts\python.exe` and run from the repository directory so
`lab-v2.sqlite3` stays in the same place across sessions. The runner prompts
for the development token each time; do not save it in either launcher. Stop
the lab bot with **Ctrl+C** before starting another process with the same
token, including the archive tool.

This runner validates the application and lab server before registering its lab guild commands. It never loads inherited cogs, opens the legacy database, sends startup messages, or removes other registered commands. It opens its own local SQLite lab database. If `/v2status` does not appear, check that the lab application was installed with the `applications.commands` scope and that the runner printed `Lab bot ready`.

### V2 lab workflow trials

The lab runner also registers `/v2verify`, `/v2name`, `/v2pending`, `/v2review`,
`/v2strike`, `/v2table`, `/v2mmrdeduct`, `/v2tables`, `/v2tableview`, and
`/v2tablereview`, and `/v2mmrpreview` **only in the lab guild**. All commands run only in
lab `#bot-debug`, use ephemeral responses, and keep records in the local
`lab-v2.sqlite3` SQLite file (ignored by Git). You can set `V2_LAB_DB` to an
absolute path to keep the lab database in another private location. Back up
that file before replacing it. The runner accepts only the lab guild and the
approved live archive guild. It does not register commands in the live guild or
send startup messages there.

Suggested test sequence: run `/v2verify` using a test MKCentral profile URL;
a staff member with **Manage Server** runs `/v2pending`, checks the claimant's
identity independently, then runs `/v2review` with the request number,
`approve: true`, and a unique `verified_name`. Try `/v2name` and review the
request the same way. Staff can record a `/v2strike` against the test member's
numeric Discord user ID. For `/v2table`, register 12 lab players, then submit
12 name/score pairs in team order with scores totaling 984.
Staff can use `/v2mmrdeduct` with a verified lab player's numeric Discord user
ID, positive `points`, and a reason to record a proposed standalone MMR
deduction without issuing a strike. No rating is changed yet.

For table-only trials without 12 human testers, stop the lab bot and run
`.\venv\Scripts\python.exe scripts\seed_lab_players.py --seed-lab-fixtures`
from the repository directory, then restart with `.\start-v2-lab.cmd`.
This makes a timestamped backup of `lab-v2.sqlite3` and inserts LAB01 through
LAB12 with reserved negative IDs. They are fictional local records, not
Discord accounts or MKCentral profiles. Staff can try `/v2table` with
`format_size: 2` and `scores:` followed by `LAB01 82 LAB02 82 LAB03 82
LAB04 82 LAB05 82 LAB06 82 LAB07 82 LAB08 82 LAB09 82 LAB10 82 LAB11 82
LAB12 82`. This sums to 984. Do not use fixture names for human verification.
For review, a different staff member runs `/v2tables`, inspects the exact
submission with `/v2tableview table_id: ...`, and uses `/v2tablereview` to
validate or reject it. An identical pending or validated table cannot be
submitted twice; a rejected one may be corrected and resubmitted. Validation
records a second staff decision only: it does not apply MMR or ranks.
After validation, staff may run `/v2mmrpreview table_id: ... starting_mmr: 6000`
for a table containing exactly LAB01–LAB12. The preview runs the inherited
format-specific calculation with an equal synthetic starting rating. It reads
the table and sends private deltas; it does not initialize player MMR, store a
rating history, or apply any rating change. For a varied 2v2 reference trial,
submit `LAB01 100 LAB02 100 LAB03 90 LAB04 90 LAB05 85 LAB06 85 LAB07 80
LAB08 80 LAB09 70 LAB10 70 LAB11 67 LAB12 67` (984 points). Its six team
deltas at 6000 are +200, +120, +40, -40, -120, and -200, respectively.

These are **workflow trials**, not a live replacement. Verification does not
call MKCentral to prove ownership or change Discord roles. Name approvals do
not rename Discord members. Strikes record the proposed penalty without
changing MMR or roles. Standalone MMR deductions are recorded as proposals
until a rating ledger and approval policy exist. Tables validate and retain inputs as pending; they do
not calculate or award MMR, publish images, or update ranks. Staff must decide
the season initialization policy and review scoring and moderation rules
before live deployment. Leaving the dev bot installed in the live server does
not turn these lab commands into a live deployment.

For the S8 results archive, the owner may temporarily keep this development bot
installed in the live server while running the V2 lab runner. Do not run the
archiver and lab runner simultaneously with the same token. The standalone
smoke check still requires lab-only membership. Follow [the live archive
procedure](SEASON_TRANSITION.md#temporarily-archive-the-live-results-channels)
for future archive runs; never run inherited `main.py` in the live server.

## 4. Prepare the full inherited bot before running `main.py`

- Install the remaining dependencies with `python -m pip install -r requirements.txt` in the same virtual environment. Use a separate MySQL/MariaDB database for development.
- Copy `constants_example.py` to `constants.py`; the latter is already ignored by Git. Set the development token and database credentials locally. Set `BOT_ID = 1553807544013684807`, `LOUNGE = [1553806194257432726]`, and `DEBUG_CHANNEL_ID = 1553807230598647808` there. Do not commit `constants.py`.
- Create lab equivalents for the other channels and roles in `constants_example.py`, then set their local IDs in `constants.py`.
- `main.py` loads every extension listed in `config.ini`. Its startup code posts to `DEBUG_CHANNEL_ID` and leaves every server except `LOUNGE[0]`; incorrect IDs can prevent startup or remove the bot from a server. Review this behavior during V2 refactoring.
- `sql/development_init.sql` **drops tables** and seeds IDs from the old server. Use it only on a disposable development database, then replace or remove old server IDs before exercising features.
- Audit moderation permissions, hard coded server IDs, third party integrations, and dependency versions before broad testing. Plan a deliberate migration and rollback before replacing the live bot.

### S9 season policy and local preview

`settings/s9-policy.json` records the staff-confirmed strike reset, provisional
Ruby-only rank thresholds, and pending rating reset/placement decisions.
Iron starts at 0 in the inherited range; the lab rating preview floors MMR at 1.
Grandmaster has no upper limit. This configuration is used by the new policy
helpers; it does not change the legacy bot's rank logic or Discord roles.

Run from the checkout root using your project virtual environment:

```powershell
.\venv\Scripts\python.exe scripts\preview_s9_lab.py --db .\lab-v2.sqlite3
```

This opens the existing lab database read-only and prints a JSON inventory.
An `apply_ready: false` result is expected. The proposed new-season strike count
is zero, but existing strikes remain untouched. The lab store does not yet
support season-specific strike counts or an apply operation. Fixture counts
are included separately; this is not a live-server roster or rating reset.
Do not delete strike rows to implement the confirmed reset.
