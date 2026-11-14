# Fill the 38 linked players' missing flags

This one-time command checks only positive, existing MKC registry links. It never
links accounts or changes registrations, names, Discord roles or ratings. Existing
flags (including unambiguous imported flags) and all explicit staff country entries,
including intentional clears, are protected. New flags have a source audit with
actor 0 identifying the automated backfill.

In Railway SSH, preview without writes:

```sh
/app/.venv/bin/python /app/scripts/backfill_mkc_flags.py --database /app/private/production.sqlite3
```

Apply the authorized missing-flag batch:

```sh
/app/.venv/bin/python /app/scripts/backfill_mkc_flags.py --database /app/private/production.sqlite3 --apply
```

Keep the bot running. Lookups run one at a time with a one-second pause, an eight-second
HTTP timeout, bounded responses and exact public profile ID validation. A rate-limit
response stops further lookups. Hidden, missing and unavailable countries stay blank.
The command refuses more than 38 eligible candidates. It rechecks all identities and
country entries inside a short SQLite transaction before writing, and rolls back the
batch if an audit write fails. It is safe to rerun; already filled flags are skipped.

The summary reports countries found and flags actually added. Website/feed updates
normally appear within 60 seconds. Existing Discord result image attachments remain
unchanged; future tables use the updated flags. No database import or bot restart is
required to apply the backfill after deploying this command.
