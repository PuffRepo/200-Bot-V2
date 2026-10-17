# S9 backups on Railway Hobby

This update adds hourly backups to private S3-compatible storage outside Railway's bot volume. It does not include or replace a database, token, production configuration, or existing bot runtime file. It does not deploy automatically.

## What is included

- `v2/backups.py`: SQLite online snapshots, integrity and foreign-key checks, SHA-256 verification, remote download/restore verification, and scoped retention.
- `scripts/backup_production.py`: one backup or a restore into a NEW file.
- `scripts/production_with_backups.py`: launches the existing production bot and one backup worker at a time. Runs a backup immediately, then hourly after each success. Retries failures after five minutes; logs `BACKUP ALERT`. A failed backup does not restart the bot.
- `requirements-v2-backups.txt`: existing V2 dependencies plus boto3.
- `tests/test_production_backups.py`: 10 automated tests.

Default retention is 168 snapshots, approximately seven days at one per hour. Backups include committed WAL changes. No raw copy of an active SQLite file is used. They contain private data and must remain private. This is a full database backup, not a public website feed.

The initial tests use temporary SQLite databases and a simulated object store. Real credentials, transfers, Railway process shutdown, and restoration must be verified below. Alerts currently appear in Railway logs; there are no email or Discord notifications. Check for a recent `BACKUP VERIFIED` entry daily until external alerting is added. Estimated recovery-point loss is up to about an hour when backups work; outages can extend that gap. There is no point-in-time recovery.

## 1. Set up separate storage

Recommended initial destination: a private Cloudflare R2 Standard bucket. An existing private AWS S3-compatible bucket also works. R2 activation may require billing information. Its current Standard free allowance is 10 GB-month, with request allowances; free usage is not an unlimited storage promise. Railway outbound transfer and other usage can still be billed.

Official instructions: https://developers.cloudflare.com/r2/get-started/s3/
Pricing: https://developers.cloudflare.com/r2/pricing/

In Cloudflare:

1. Open **Storage & databases > R2** and activate R2 if needed.
2. Create a **Standard** bucket named `200-lounge-backups` (choose another name if unavailable).
3. Keep public access disabled.
4. Create R2 API credentials with **Object Read & Write**, scoped to this bucket only.
5. Store the Access Key ID, Secret Access Key, and HTTPS S3 endpoint securely. Do not paste secrets into chat or Git.

## 2. Install the additive code update locally

Extract this ZIP into `D:\Projects\200-Lounge\200-Bot-V2`, preserving its folders. It adds six files; no existing runtime source is replaced. Leave the local bot stopped.

Run in PowerShell:

```powershell
cd D:\Projects\200-Lounge\200-Bot-V2
.\venv\Scripts\python.exe -m unittest discover -s tests -p test_production_backups.py -v
$LASTEXITCODE
```

Expected: **10 tests, OK, exit code 0**. The tests require no storage credentials or boto3 installation.

Stage only this update, inspect, commit, and push:

```powershell
git add -- v2/backups.py scripts/backup_production.py scripts/production_with_backups.py requirements-v2-backups.txt tests/test_production_backups.py HOBBY-BACKUPS.md
git --no-pager diff --cached --stat
git --no-pager diff --cached --check
git commit -m "Add verified off-volume SQLite backups for production"
git push origin v2-production-integration
```

Railway is connected to that branch, so pushing may redeploy the existing bot. Its current start command remains unchanged until step 5. Every restart currently repeats the existing roster reconciliation before registering commands; allow it to finish.

## 3. Configure Railway

Add these variables to **200-lounge-v2**, using the actual values from your storage provider:

| Variable | Value |
|---|---|
| `LOUNGE_BACKUP_BUCKET` | Your bucket name |
| `LOUNGE_BACKUP_ENDPOINT` | Your HTTPS S3 endpoint |
| `LOUNGE_BACKUP_PREFIX` | `200-lounge/s9` |
| `AWS_ACCESS_KEY_ID` | Storage Access Key ID |
| `AWS_SECRET_ACCESS_KEY` | Storage Secret Access Key |
| `AWS_DEFAULT_REGION` | `auto` for R2; actual region for AWS S3 |
| `LOUNGE_BACKUP_KEEP` | `168` |
| `LOUNGE_BACKUP_INTERVAL_SECONDS` | `3600` |

Seal the credential variables if desired. They must be available at runtime. The prefix must be dedicated to this database. Do not run two backup schedulers against the same prefix. Retention lists and deletes only generated snapshot names under that prefix. Bucket versioning/lifecycle rules can retain older versions and affect cost independently.

Change `RAILPACK_INSTALL_CMD` to:

```text
python -m venv /app/.venv && /app/.venv/bin/python -m pip install -r requirements-v2-backups.txt
```

Apply the staged Railway changes. Keep the existing start command for this first deployment:

```text
/app/.venv/bin/python scripts/v2_production.py --config /app/private/production.json
```

Keep one replica, the existing `/app/private` volume, and the local bot stopped. No new domain is needed.

## 4. Verify actual upload and restore before enabling the scheduler

From local PowerShell:

```powershell
railway.cmd ssh
```

Inside Railway's Linux shell:

```bash
cd /app
/app/.venv/bin/python -m unittest discover -s tests -p test_production_backups.py -v
/app/.venv/bin/python scripts/backup_production.py --database /app/private/production.sqlite3
```

Expected: **10 passing tests**, then `BACKUP VERIFIED: 200-lounge/s9/<timestamp>-<sha256>.sqlite3`.

That backup command already downloads and opens the uploaded object. To rehearse restoration into a separate file, copy the complete returned key into this command, replacing `PASTE_KEY_HERE`:

```bash
/app/.venv/bin/python scripts/backup_production.py --restore-key 'PASTE_KEY_HERE' --output /tmp/s9-restore-check.sqlite3
```

Expected: `RESTORE VERIFIED`. If the test file already exists, use a new output filename. The restore command refuses to overwrite existing files, including production.

Inspect the restored database:

```bash
/app/.venv/bin/python - <<'PY'
import sqlite3
from contextlib import closing

with closing(sqlite3.connect('file:/tmp/s9-restore-check.sqlite3?mode=ro', uri=True)) as db:
    print('Integrity:', db.execute('PRAGMA integrity_check').fetchall())
    print('Registered players:', db.execute('SELECT COUNT(*) FROM players').fetchone()[0])
    print('Season:', db.execute("SELECT value FROM lab_settings WHERE key='current_season'").fetchone())
    print('Transition:', db.execute('SELECT guild_id, role_transition_applied FROM registry_import_manifest WHERE id=1').fetchone())
PY
```

Expected: integrity `ok`, season S9, guild 461383953937596416, completed transition marker 1. Player count may exceed 1890 if registrations occurred after launch; do not treat the original count as a permanent invariant. This test does not replace the live database or change Discord.

If anything fails, stop here and report the error category. A failure does not mean the live bot has stopped. Do not reinstall the old local production database.

## 5. Enable the hourly scheduler

Only after the real upload and restore checks pass, set Railway's Custom Start Command to:

```text
/app/.venv/bin/python scripts/production_with_backups.py --config /app/private/production.json
```

Deploy. Expect `BACKUP VERIFIED` and, after the existing startup roster check, `Production bot ready`. Run `/status` in `#bot-logs`. Check again after roughly an hour for another verified backup and confirm that a new private object exists in the bucket.

The supervisor forwards shutdown to its children and exits when the bot exits, allowing Railway's restart policy to handle bot failures. A stuck backup is killed after ten minutes and retried. Short retries can temporarily shorten the time span represented by the count-based retention.

## Recovery and rollback

To remove the scheduler, restore the previous `v2_production.py` start command and redeploy. This changes no ratings and restores no old database.

For a real database incident, stop production first and preserve the current volume files. Download a chosen snapshot to a NEW file using `--restore-key`, validate its contents and choose its recovery time before replacing any live database. Handle SQLite WAL/SHM files while the bot is stopped; do not replace only the main database underneath a running process. This package deliberately does not automate production replacement.

Keep the bucket private. A backup is not a suitable publicly downloadable data source for the website. This backup task is separate from the website and `/stats` integration.
