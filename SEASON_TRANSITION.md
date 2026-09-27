# V2 S8 to S9 MMR transition

This is a read-only preview. It changes neither the database nor Discord roles.
The source must cover the **actual S8 season**. The checked-in S7 dump ends at
table 2791 on 2025-09-28 and does not supply S8 results or S8 final MMR.

## Agreed rule

- Eligibility: people currently in the server with one of these rank roles: Iron, Bronze, Silver, Gold, Platinum, Ruby, Diamond, Master, Grandmaster. Placement-only members are excluded.
- One observation per completed table: `player_mogi.new_mmr` in the previous season. The new starting MMR is the median of that player's observations. For an even count, take the mean of the two middle values and round a half upward to a whole MMR.
- A ranked player with no previous-season tables carries forward their existing `player.mmr`.
- Preserve the previous season's match and strike records. The decision to carry or reset active strikes, bans, and their effects is pending with the owner. It must be explicit before the write phase.

The legacy schema has no completed-table flag. The preview flags tables whose player row count differs from 12, duplicate player rows, and missing/invalid post-table MMR. Substitutions can make a row count other than 12 legitimate: staff must review these exceptions. A complete-looking table can still be incorrect, so compare a sample with the published results. A standalone strike or MMR penalty changes `player.mmr` separately and is not itself a post-table observation.

## Recover S8 before computing a reset

First ask whoever controls the **existing Railway project or database** to check its
database service and Backups tab for S8. The expiry of a custom website domain
does not itself prove that its database was deleted. If data still exists, make
a private, read-only export of S8 `player`, `mogi`, and `player_mogi`, plus
verification/name history and strike/penalty records needed for the new bot.
Preserve the original backup and keep any restored copy separate from production.
Server ownership alone does not provide Railway project access.
Railway documents [separate generated and custom domains](https://docs.railway.com/networking/domains/working-with-domains)
and [volume backups in the service Backups tab](https://docs.railway.com/volumes/backups).

If no S8 database is available, archive the published result posts before doing
transcription. The inherited `/table` cog posts a **results image and a separate
MMR JPEG** under embeds bearing a table ID. The MMR image shows each player name
and post-table MMR. It contains no player Discord ID or machine-readable MMR
row. That is why the result images alone cannot be passed to the reset preview.

An authorized bot **already able to see the live server** can run the local
archiver below. It requires Pycord, `View Channel`, `Read Message History`, and
Message Content intent enabled in its Developer Portal; message content
controls access to embeds and attachments. It does not need Administrator,
Manage Roles, or Send Messages. The lab-only development bot cannot read the
live channel unless the owner separately installs it there. Never use a user
account token or send any token to this project.
See [Discord's privileged intents guide](https://support-dev.discord.com/hc/en-us/articles/6207308062871-What-are-Privileged-Intents)
for current access rules.

```powershell
python -m pip install py-cord
python -m scripts.archive_s8_results --application-id <AUTHORIZED_BOT_APP_ID> --guild-id <LIVE_SERVER_ID> --channel-id <TIER_ALL_RESULTS_ID> --since 2025-09-28 --output C:\private\s8-results
```

On Windows, run these commands from `D:\Projects\200-Lounge\200-Bot-V2`
after fetching and checking out `v2-season-reset-preview`. The historical
`tier-all-results` channel ID in `sql/init.sql` is `1010600464003387542`;
confirm that the current live channel has that ID using Discord Developer Mode
before entering it. The live server ID is **not** the lab server ID
`1553806194257432726`. Copy the live server's ID in Discord and enter it as
`--guild-id`.

If you use the development app (`1553807544013684807`) for this one-time read,
the owner must first install it in the live server with access limited to the
results channel. Temporarily having it in two servers makes the lab-only
`discord_smoke.py` and `v2_lab.py` isolation checks fail; do not run those lab
programs while it is installed in the live server. Stop any running lab bot,
run only the archiver, and remove its live-server installation afterward. A
different bot already under
your control and installed in the live server also works; use **that** bot's
application ID and token. The script checks both against the requested server
and channel before downloading anything.

Add another `--channel-id <ID>` for each other S8 results channel. Add
`--until YYYY-MM-DD` if S9 posts have begun; both dates are UTC and `--until`
is exclusive. The token is entered at a hidden local prompt. The output
directory must be new. Run on your own machine and keep the archive private.
The tool reads only and saves each image locally with a SHA-256 checksum. Its
`messages.jsonl` and `index.csv` retain the table IDs, original Discord message
links, and attachment status; `audit.json` lists unpaired posts, missing images,
and gaps in table IDs. An interrupted run leaves the processed messages on disk;
use a fresh output directory for a complete retry.
Exit code `0` means the indexed posts have paired images with no detected gaps;
`1` means inspect the audit; `2` means export failed. No exit code proves that
every S8 table was recovered.

**The archive is evidence, not a database reconstruction.** Verify which table
IDs belong to S8, inspect edits/reverted tables and gaps, and ensure any other
result channels are included. Read the MMR images with OCR/manual review and
verify every post-table MMR value (normally 12 per table) against the image. Match historical
player names to immutable Discord player IDs using authoritative identity or
name-change records; ambiguous names need staff review. For each verified S8
table, construct `results.csv` with `player_id,mogi_id,new_mmr` and maintain a
private source ledger linking **each row** to its image/message and reviewer.
For `players.csv`, recover S8-end `player.mmr` and `rank_id` from the database
or other audited records. The last published table value may be stale after a
penalty, correction, or rename. Current rank roles give reset eligibility but
do not establish a no-game player's S8 current MMR. If either value or the
season's completeness cannot be verified, leave that player unresolved; do not
invent a carryover or treat an absent table as zero S8 games.

## Four local CSV inputs

Keep inputs outside Git, for example in a private folder on your computer. If S8
database access is recovered, use a read-only database account against the actual
S8 schema. Export query results as CSV with column headers and the exact column
names below; never run `sql/development_init.sql` on season data. If rebuilding
from result images, keep a row-by-row source ledger and compare table counts and
MMR with independent records before interpreting the preview.

| File | Required columns | Source |
| --- | --- | --- |
| `players.csv` | `player_id,mmr,rank_id` | `SELECT player_id, mmr, rank_id FROM <S8_SCHEMA>.player` or verified S8-end reconstruction |
| `results.csv` | `player_id,mogi_id,new_mmr` | `SELECT pm.player_id, pm.mogi_id, pm.new_mmr FROM <S8_SCHEMA>.player_mogi pm JOIN <S8_SCHEMA>.mogi m ON m.mogi_id = pm.mogi_id` or individually reviewed MMR images |
| `ranks.csv` | `rank_id,rank_name,mmr_min,mmr_max` | Current server rank roles and **current** MMR boundaries. Include all nine ranked roles and Ruby; the archived seed is incomplete. |
| `ranked_members.csv` | `player_id,rank_role_id` | Current Discord guild role roster, one row per member per ranked role. This defines eligibility; use the helper below if the bot has member-list access. |

In the SQL examples, replace `<S8_SCHEMA>` with the schema verified by the owner.
Do not copy the angle brackets into SQL. Confirm the export covers every player
and every completed table from S8. Keep the `new_mmr` values as stored,
including blanks so the preview can flag errors.

To export the guild rank roster, use a bot already installed in the current server with Guild Members intent enabled. The command only reads member roles and saves a local CSV. Use the application ID for the token supplied at the hidden prompt and the **current server** ID; the lab-only bot cannot read another server.

```powershell
python -m scripts.export_ranked_members --application-id <BOT_APPLICATION_ID> --guild-id <CURRENT_SERVER_ID> --ranks C:\private\ranks.csv --output C:\private\ranked_members.csv
```

## Produce and review the preview

Run from the root of the `v2-development` checkout, replacing the example paths with your private export location:

```powershell
python -m scripts.season_reset_preview --players C:\private\players.csv --results C:\private\results.csv --ranks C:\private\ranks.csv --ranked-members C:\private\ranked_members.csv --output C:\private\season_reset_preview.csv
```

The output contains each ranked member's current MMR, table count, exact median, proposed integer MMR and rank, carryover/median source, and any review notes. Exit code `0` means every row was computed without a flagged exception **within the supplied CSVs**; it cannot detect missing S8 tables or players. Exit code `1` means inspect the review rows and flagged tables; exit code `2` means the inputs or output failed validation. **Exit code `0` is not approval to write**: staff must verify complete S8 coverage, compare sample players and unusual rows with the published results and current roles, and resolve unmatched identities. If an input changes, rerun with a new output path or `--overwrite`.

## Write phase gate

Once the owner confirms the active-strike policy and staff sign off on the preview, back up the previous season and create a separate new-season schema. Record the old and proposed values for every eligible player in a transition ledger; set new-season starting, current, and peak MMR consistently with the approved rule; recalculate ranks from current thresholds; reconcile Discord roles; and verify the final player and exception counts. Keep one bot writing table results at a time. Prepare rollback from the snapshot and ledger before accepting the first new-season table. No write or rollback command is included in this preview release.
