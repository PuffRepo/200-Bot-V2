# Bulk country updates (review only)

The released bot already reads any unambiguous country_code preserved in
registry_import_audit historical registration records. No mass write is needed
for those players. This workspace contains archived S5/S6/S7 SQL exports, but no current S9
roster export or production database. The live read-only coverage check reported
1,916 players and zero recorded countries. Historical-to-current ID overlap
has not yet been verified.

The read-only audit prints counts only, without names, IDs or private source
records. It refuses to create a missing SQLite file:

```text
python scripts/audit_player_countries.py --database <existing.sqlite3>
```

When an approved source is available, prepare UTF-8 CSV with exactly these
columns (Discord IDs must stay text when edited in spreadsheets):

```csv
discord_id,country_code
123456789012345678,JP
```

Preview only (no database migration or writes):

```text
python scripts/bulk_player_countries.py --database <existing.sqlite3> --csv countries.csv
```

Review every row and its status. Only fill_missing rows are eligible. Existing
countries that conflict, explicit staff corrections (including cleared flags),
unchanged rows and unknown players are skipped. Duplicate IDs or invalid codes
reject the whole input. Display names are never identity matches.

After approval, an authorized operator can apply the exact reviewed digest:

```text
python scripts/bulk_player_countries.py --database <existing.sqlite3> --csv countries.csv --apply-digest <reviewed-sha256> --actor <staff-discord-id> --reason "Reviewed country export from <source>"
```

Apply uses one transaction, rechecks the full preview after acquiring the write
lock, and records each country's previous value, actor and reason. A concurrent
country change or repeated old confirmation fails without partial writes.
The CLI requires the existing country schema and does not run migrations.
It changes no names, MKC links, ratings, ledger events, roles or statistics.
Do not run apply against production until the specific batch has been reviewed.
No production database access or deployment was performed for this patch.

A recorded MKC number alone is not proof that it is a valid current registry
profile ID. Review its namespace/identity before fetching a profile country.
Never match profiles by approximate display name, language or assumed location.


## Restoring historical countries

The tracked archives contain real country values; they must be parsed as text,
never imported into or executed against any database. Generate a fresh review
folder and country-only CSV with:

```text
python scripts/extract_historical_countries.py --sql sql/s5200lounge-export.sql sql/s6200lounge-export.sql sql/s7200lounge-export.sql --output historical-country-review
```

The extractor reads the actual player schema, preserves exact Discord IDs,
ignores display names for matching and exports no names, friend codes or MMR.
It records source hashes and excludes both cross-season conflicts and invalid
nonempty codes. Its output directory must not already exist.

Results from the repository archives: S5 1,709 player rows, S6 2,627, S7 3,786.
Their union has 3,823 unique Discord IDs: 3,819 unambiguous country candidates,
3 country conflicts and 1 invalid-source player. These are historical totals,
not a claim that all 1,916 current S9 players can be filled. Historical countries
may be out of date. Review the source and current-player overlap before apply.

Pass the generated countries.csv to the bulk preview above. The preview joins
by exact Discord ID, skips unknown players and protects staff records. Do not
execute the SQL dumps, restore an old database, or import historical ratings.
The current deployment does not yet include these extraction/bulk scripts.


## Next: check current S9 overlap without a deployment

Run locally in the Windows repository folder, not inside Railway SSH:

```powershell
git fetch origin
git switch codex/bulk-country-review
git merge --ff-only origin/codex/bulk-country-review
.\.venv-v2-tests\Scripts\python.exe scripts\preview_historical_countries.py --public-url https://200-lounge-v2.up.railway.app/public/s9.json
```

This reads the archived SQL as text and the fresh public S9 snapshot, joins only
exact Discord IDs and prints aggregate counts. It writes no files, registers
no commands and changes no database. No Git push or Railway deployment is
needed. If still in the Railway SSH session, run `exit` first.

The public-mode count is an estimate: explicit staff-cleared flags are private
and cannot be distinguished from missing countries in the public feed. Before
any apply, the database preview must enforce those protections and bind the
reviewed digest. The same script supports a read-only database preview via
`--database <existing.sqlite3>` when run by the production operator; never
copy or replace production SQLite to get this count.
