# Bulk country updates (review only)

The released bot already reads any unambiguous country_code preserved in
registry_import_audit historical registration records. No mass write is needed
for those players. This workspace contains no real roster/registry export or
production database; only temporary test fixtures are available. Country
coverage in production has not been verified.

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
