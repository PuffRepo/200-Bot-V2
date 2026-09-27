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

### Temporarily archive the live results channels

Use the V2 Dev app (`1553807544013684807`) only if the server owner agrees to
install it temporarily. Before installation, stop **every running instance**
using its token, including `scripts/v2_lab.py`; do not start another instance
until the archive finishes. **Never run the inherited `main.py` in the live
server**: on startup it loads legacy cogs, posts a debug message, and leaves
servers other than its configured lounge. Run only
`scripts.archive_s8_results` during this temporary installation. Neither
`discord_smoke.py` nor `v2_lab.py` can pass their lab-only checks while the
development bot is installed in both servers.

In Discord Developer Mode, copy the **live 200 Lounge server ID** and verify
the names and IDs of its results channels. These IDs come from the old
`sql/init.sql` configuration; they might have changed:

| Old tier | Old results channel ID |
| --- | --- |
| S | `1208850019512483871` |
| A | `1010600237880053800` |
| B | `1010600376187244655` |
| C | `1010600418524532889` |
| ALL | `1010600464003387542` |
| SQ | `1010600944209244210` |

Ask the owner to review any other S8 results channels and add their **verified
current IDs**. If an old channel is gone, record that fact and check for a
replacement; omit the missing ID from the command only after noting the gap.
The live server ID is **not** the lab server ID `1553806194257432726`.

The owner can use the Developer Portal's **Installation** settings to install
the development app with only the `bot` scope and no requested write or
moderation permissions. Give its bot role `View Channel` and `Read Message
History` on the selected results channels, using bot-specific channel
overrides where needed. Enable **Message Content** under **Bot → Privileged
Gateway Intents** so historical embeds and attachments are available. Verify
the bot's effective permissions in those channels; it needs no `Send
Messages`, `Administrator`, `Manage Roles`, `Manage Channels`, or `Manage
Messages`. Do not alter `@everyone` or other members' permissions for this
archive. The script asks for the token in a hidden local prompt; never paste
it into chat, the terminal command, Git, or a PR. See [Discord's privileged
intents guide](https://support-dev.discord.com/hc/en-us/articles/6207308062871-What-are-Privileged-Intents)
for the intent setting.

From your existing Windows PowerShell checkout, fetch the latest archive tool:

```powershell
cd D:\Projects\200-Lounge\200-Bot-V2
git fetch origin v2-season-reset-preview
git switch --detach FETCH_HEAD
py -3.13 -m pip install py-cord
```

Replace the IDs below with the **verified current IDs**, removing any missing
channel only after recording why. Enter the live server ID copied from
Discord. Keep `--primary-channel-id` set to the current `tier-all-results` ID;
if that channel is gone, omit that option and review `index.csv` by channel.
The 2025-09-28 start date deliberately includes the final day in the old S7
dump, so S7 posts must be excluded during later review. Add `--until
YYYY-MM-DD` to `$archiveArgs` if S9 results have begun; it is an **exclusive
UTC** end date.

```powershell
$liveServerId = Read-Host 'Live 200 Lounge server ID'
if ($liveServerId -eq '1553806194257432726') { throw 'This is the lab server ID' }
$tierAllId = '1010600464003387542'
$resultChannels = @(
  '1208850019512483871', # tier-s-results
  '1010600237880053800', # tier-a-results
  '1010600376187244655', # tier-b-results
  '1010600418524532889', # tier-c-results
  $tierAllId,            # tier-all-results
  '1010600944209244210'  # tier-sq-results
)
$archivePath = "$env:USERPROFILE\Documents\200-Lounge-S8-all-results"
$archiveArgs = @('--application-id', '1553807544013684807',
  '--guild-id', $liveServerId, '--primary-channel-id', $tierAllId,
  '--since', '2025-09-28', '--output', $archivePath)
foreach ($id in $resultChannels) { $archiveArgs += @('--channel-id', $id) }
py -3.13 -m scripts.archive_s8_results @archiveArgs --check-access
```

The access check validates the application, live server, selected text
channels, and read permissions. It **does not** read history, download images,
or create an output directory. Compare the printed channel names with the
channels you intended. If it fails, correct the IDs or bot access with the
owner before running the archive. Once the check passes, run:

```powershell
py -3.13 -m scripts.archive_s8_results @archiveArgs
```

The archiver reads only the selected channels, sends no messages, and saves
result images locally with SHA-256 checksums. Keep the archive private.
`messages.jsonl` and `index.csv` retain original message links; `audit.json`
lists the message count and ID range for **each channel**, table IDs found in
another channel but missing from `tier-all-results`, unpaired or mirrored
posts, missing images, and gaps. Duplicate posts across tier channels and ALL
may be normal; inspect their links before treating them as separate tables.
An empty channel or an ID absent from ALL needs investigation. An interrupted
run leaves partial files; use a **new output directory** for a complete retry.
Exit code `0` means no indexed issue was detected, `1` means review
`audit.json`, and `2` means the archive failed. No exit code proves S8 is
complete. Check the known last S8 mogi, **3390**, against the original posts
and investigate any missing range or channel before constructing CSVs.

After saving and reviewing the archive, have the owner remove the V2 Dev bot
from the live server and remove any temporary bot-specific channel overrides.
Verify that it remains in the lab server alone before restarting a lab runner.
A separate existing bot under your control can also read the channels; use
that bot's application ID and token, with the same limited permissions.

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
