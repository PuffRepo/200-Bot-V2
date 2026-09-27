# V2 season MMR reset: first implementation

This is a read-only preview. It changes neither the database nor Discord roles.
The source is the previous **actual** season database, not the old SQL dumps in this repository.

## Agreed rule

- Eligibility: people currently in the server with one of these rank roles: Iron, Bronze, Silver, Gold, Platinum, Ruby, Diamond, Master, Grandmaster. Placement-only members are excluded.
- One observation per completed table: `player_mogi.new_mmr` in the previous season. The new starting MMR is the median of that player's observations. For an even count, take the mean of the two middle values and round a half upward to a whole MMR.
- A ranked player with no previous-season tables carries forward their existing `player.mmr`.
- Preserve the previous season's match and strike records. The decision to carry or reset active strikes, bans, and their effects is pending with the owner. It must be explicit before the write phase.

The legacy schema has no completed-table flag. The preview flags tables whose player row count differs from 12, duplicate player rows, and missing/invalid post-table MMR. Substitutions can make a row count other than 12 legitimate: staff must review these exceptions. A complete-looking table can still be incorrect, so compare a sample with the published results. A standalone strike or MMR penalty changes `player.mmr` separately and is not itself a post-table observation.

## Four local CSV exports

Keep the exports outside Git, for example in a private folder on your computer. Use a read-only database account against the actual previous-season schema. Export query results as CSV with column headers and the exact column names below; never run `sql/development_init.sql` on season data.

| File | Required columns | Source |
| --- | --- | --- |
| `players.csv` | `player_id,mmr,rank_id` | `SELECT player_id, mmr, rank_id FROM <PREVIOUS_SEASON_SCHEMA>.player` |
| `results.csv` | `player_id,mogi_id,new_mmr` | `SELECT pm.player_id, pm.mogi_id, pm.new_mmr FROM <PREVIOUS_SEASON_SCHEMA>.player_mogi pm JOIN <PREVIOUS_SEASON_SCHEMA>.mogi m ON m.mogi_id = pm.mogi_id` |
| `ranks.csv` | `rank_id,rank_name,mmr_min,mmr_max` | Current server rank roles and **current** MMR boundaries. Include all nine ranked roles and Ruby; the archived seed is incomplete. |
| `ranked_members.csv` | `player_id,rank_role_id` | Current Discord guild role roster, one row per member per ranked role. This defines eligibility; use the helper below if the bot has member-list access. |

In the SQL examples, replace `<PREVIOUS_SEASON_SCHEMA>` with the schema verified by the owner. Do not copy the angle brackets into SQL. Confirm the export covers every player and every table from that season. Keep the `new_mmr` values as stored, including blanks so the preview can flag errors.

To export the guild rank roster, use a bot already installed in the current server with Guild Members intent enabled. The command only reads member roles and saves a local CSV. Use the application ID for the token supplied at the hidden prompt and the **current server** ID; the lab-only bot cannot read another server.

```powershell
python -m scripts.export_ranked_members --application-id <BOT_APPLICATION_ID> --guild-id <CURRENT_SERVER_ID> --ranks C:\private\ranks.csv --output C:\private\ranked_members.csv
```

## Produce and review the preview

Run from the root of the `v2-development` checkout, replacing the example paths with your private export location:

```powershell
python -m scripts.season_reset_preview --players C:\private\players.csv --results C:\private\results.csv --ranks C:\private\ranks.csv --ranked-members C:\private\ranked_members.csv --output C:\private\season_reset_preview.csv
```

The output contains each ranked member's current MMR, table count, exact median, proposed integer MMR and rank, carryover/median source, and any review notes. Exit code `0` means every row was computed without a flagged exception; exit code `1` means inspect the review rows and flagged tables; exit code `2` means the inputs or output failed validation. **Exit code `0` is not approval to write**: staff must compare sample players and unusual rows with the prior-season results and current roles. If an input changes, rerun with a new output path or `--overwrite`.

## Write phase gate

Once the owner confirms the active-strike policy and staff sign off on the preview, back up the previous season and create a separate new-season schema. Record the old and proposed values for every eligible player in a transition ledger; set new-season starting, current, and peak MMR consistently with the approved rule; recalculate ranks from current thresholds; reconcile Discord roles; and verify the final player and exception counts. Keep one bot writing table results at a time. Prepare rollback from the snapshot and ledger before accepting the first new-season table. No write or rollback command is included in this preview release.
