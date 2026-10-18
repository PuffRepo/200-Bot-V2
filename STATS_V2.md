# V2 S9 statistics and the public website feed

`/stats` (lab: `/v2stats`) defaults to the caller. Optional inputs are `member`,
exact current `player` name, `tier` (all/c/b/a), `mogi_format` (1/2/3/4), and
`last` (1–1000). Choose a member or a name, not both. It uses the existing
configured-channel and restriction policy; Reporter access is not required.
The response is private to the requester. No historical MySQL query is used.

Only applied S9 tables enter the statistics. Reversed, rejected, pending,
other-season and unapplied results are excluded. Stable participant IDs connect
scores to players after renames; teammate averages use the original team
membership, not equal MMR deltas. Last-N follows application order after tier
and format filters. Placements count for tables/scores, but not numeric MMR
averages or gain/loss rates. Gains/losses/ties refer to positive/negative/zero
**effective MMR delta**, not finishing position. Full refunds therefore count
as ties, not additional wins. Refunds are included once in table performance.
Penalties/unstrikes remain separate and do not change table scores or rates.

Current MMR, rank and tied competition leaderboard position are season-wide,
independent of history filters. Recorded peak uses current MMR, active table
before/after balances and adjustment snapshots; it is not reconstructed by
adding filtered deltas. This is the peak visible in the retained V2 ledger,
not an imported peak from an earlier season. Registered unplayed/unplaced
players get an honest empty response. Missing application ordering is an error,
not silently counted as no games.

## Read-only public export

`scripts/export_public_s9.py` prepares the website contract, using a consistent
SQLite read transaction and `mode=ro`, without LabStore initialization or
migrations. It does not start Discord, connect to Railway, upload to storage,
or change the database. It fails if the source is missing or not active S9,
if the target is a SQLite file/sidecar, or if fixture identities are present.
Output uses atomic replacement of a separate JSON file.

Only public player identifiers/names, current ratings/ranks, sanitized match
results and statistical summaries are exported. No registry source JSON, staff
IDs, reasons, tokens, private configuration, evidence URLs, strike records,
restrictions or raw database files are included. Country/friend-code and ban
status are not invented from private source records. Discord IDs remain strings
so JavaScript cannot round them. This export is not a database backup.

A future operator can run, after review, with the Python 3.13 environment:

```text
python scripts/export_public_s9.py --database /app/private/production.sqlite3 --output /app/private/public-s9.json
```

That command has **not** been run against production here. The JSON destination
is distinct from the database. Publish only the sanitized JSON to a separate
website feed location; never make the production database or the private R2
backup bucket public. Hosting and refresh scheduling must be chosen before the
website can receive live updates. The exporter itself is a one-shot command;
it does not imply that a scheduler is active.

The website adapter in the separate `200-lounge.com` checkout expects this
schema version 1. It accepts a server-only `S9_PUBLIC_DATA_FILE` or an HTTPS
`S9_PUBLIC_DATA_URL`, and fails closed on missing/stale/invalid data. Default
maximum age is 900 seconds. Current table images and historical after-balances
remain snapshots; effective deltas and refunds are separate fields. A current
MMR chart point includes later credits/penalties without rewriting old tables.

## Validation and review

```text
python scripts/run_solo_tests.py
python -m unittest discover -s tests -p test_production_backups.py -v
```

Use Python 3.13 with `requirements-v2-backups.txt`. All regression data is local
temporary SQLite data, including command construction interrupted before login.
No production matches are fabricated. Changes belong on `codex/stats-v2-review`;
do not push `v2-production-integration` or deploy as part of this work.

The website keeps its existing design, fonts, styles and explicit S5–S7 archive
paths; S9 is the new default. Its `/api/all_players` contract now supports S9
ratings and loss credits with exact string Discord IDs and a generated-at
header. This does **not** establish which URL MogiBot actually uses, its cache
refresh interval, or whether the live website has been updated. Verify those
separately before claiming `/teamsmmr` has switched to S9.

## Optional Railway public feed worker

The same review branch now includes `scripts/serve_public_s9.py` and
`v2/public_feed.py`. The existing backup supervisor starts it only when
`LOUNGE_PUBLIC_FEED_ENABLED=1`; default bot/backup behavior is unchanged. It
serves `/public/s9.json` and `/health` on Railway's `PORT` (default 8080), opens
SQLite read-only, rejects fixture identities, and refreshes on request at most
once per minute. Unknown file paths return 404; write methods return 405.
Failed reads return a generic 503 without paths or private data. A worker
failure retries independently; it does not launch another bot or restart the
existing bot. Supervisor shutdown still cleans up child processes.

This removes the need to share the SQLite volume or publish R2 backup files to
the website. The website repository has a detailed `RAILWAY_WEBSITE_SETUP.md`
runbook for reviewing/releasing the optional worker, creating a separate web
service and configuring the actual public feed URL. No worker was started
against production, no Railway variables were changed, and no DNS/deployment
was performed during development. Use generated Railway domains first until
control of the old domain and archive hosting are confirmed.
