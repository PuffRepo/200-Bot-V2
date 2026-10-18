# V2 loss reductions

`/reduce_loss member table_id fraction reason` uses Reporter access (including
staff through the existing Reporter policy) in a configured Lounge channel.
The same actor must confirm the ephemeral preview within five minutes and
still have access. Cancel and expiry change nothing. Self-refunds are denied.
The lab command is `/v2reduceloss`.

Select one registered player with a **negative applied loss** on one currently
applied S9 table, while S9 is active. Placements, zero/positive deltas,
previous-season tables and pending/reversed results are ineligible. Pending
corrections, disputed applied results and unfinished correction replay block
credits, just as they block penalties.

The fraction is the **loss retained**, between 0 and 1 inclusive: `-200 * 1/2`
retains `-100` and credits `100`. `0` refunds the full actual loss; `1` retains
it all. Integer loss rounds down, matching the legacy implementation:
`-200 * 1/3` becomes `-67`. The calculation uses the applied loss after the MMR
floor, not a larger raw calculated loss. Fractions and bounded decimal strings
are parsed exactly without floating point or expression evaluation.

Confirmation records the actor, reason, fraction, original and retained loss,
balance adjustment and preview digest in SQLite. One reduction is permitted
per player/table, including a zero-credit reduction. A second fraction cannot
compound the refund. Duplicate clicks, simultaneous requests and restart
retries cannot credit again. Changed balances or application order require a
fresh preview and a separate confirmation.

## Subsequent tables and corrections

A reduction credits the player's **current balance**. If table A lost 200 and
B was already applied, reducing A to one half credits 100 now; B's original
inputs, results and all other players' results stay unchanged. A new table C
uses the credited balance. This deliberately does not recalculate already
applied dependent tables or other players' MMR.

Original `rating_events` and published table images remain historical
snapshots. `balance_adjustments` records the credit, and `loss_reductions`
links it to its source event. For per-table statistics use
`effective_rating_events.effective_delta` (original applied delta plus refund).
`LabRatings.player_statistics(user_id)` excludes reversed tables and earlier
seasons, reports originals/refunds separately, and includes each credit once
in net/average table deltas. Placements count as tables but have no numeric
delta; penalties stay separate from table performance. Do not sum the effective
delta and the credit again when reconciling balances. No V2 `/stats` command
existed in this branch; this patch supplies the accounting query/API.

The latest applied sequence at credit time is an accounting boundary. Normal
newest-first reversal is allowed for tables applied **after** that boundary,
restoring their credited starting balances. Reversal at/before the boundary
is blocked across the season for positive credits, even if another penalty
later offsets the credit. Zero-credit reductions (fraction 1 or rounding to the
original loss) retain their audit/duplicate protection but do not establish a
reversal boundary. Thus automatic correction replay cannot
silently discard a credit. Crossing that boundary requires a separately audited
correction mechanism; this patch does not offer automatic credit removal or
retrospective replay. Resolve known result mistakes before reducing losses.

The credit, rating revision, audit record and member synchronization retry job
commit atomically. The existing MemberSync reconciles current MMR/rank while
preserving restriction policy; API failures leave durable pending work for
retry or `/fix_roles`, without applying the credit again. The consistency audit
fingerprints reductions and checks their fractions and matching credit records.
Role-sync feedback is scoped to the requested members, including `/fix_roles`;
unrelated pending members do not cause a false failure. A synchronization
exception after commit explicitly reports that the credit is already recorded.

## Offline validation

Use Python 3.13 and `requirements-v2-backups.txt`, then run from the checkout:

```sh
python scripts/run_solo_tests.py
python -m unittest discover -s tests -p test_production_backups.py -v
```

All tests use temporary SQLite databases and transport doubles. This patch
adds tables and a view through existing startup initialization when an operator
later chooses to deploy it. No production data migration, Discord connection,
Railway operation or deployment is performed as part of preparing the patch.

## Review and release

The reviewed branch is `v2-production-integration`, based on
`a01eae2e555484a3933c9f9cd03bdd56ef239d08`. From the checkout containing this patch,
activate Python 3.13 and run the two offline commands above. Review and commit:

```sh
git status --short --branch
git diff --check
git diff
git add -- REDUCE_LOSS.md v2/loss_reductions.py v2/lab_ratings.py scripts/v2_lab.py scripts/solo_lab_audit.py scripts/run_solo_tests.py tests/test_loss_reductions.py tests/test_member_sync.py tests/test_runtime_commands.py
git diff --cached --stat
git commit -m "Add audited one-time S9 loss reductions"
```

Do not stage private configuration, database files, unrelated edits or old ZIPs.
If the remote branch has advanced, integrate and retest before release; do not
force-push. No registry import, role transition or season reset is needed.

Before release, open Railway project **mindful-inspiration**, service
**200-lounge-v2**. Check the active deployment's resolved Start Command, not just
a pending settings edit. The supplied backup supervisor command is:

```text
/app/.venv/bin/python scripts/production_with_backups.py --config /app/private/production.json
```

Confirm recent `BACKUP VERIFIED` entries across two scheduled intervals and
corresponding new R2 objects. A past manual backup or saved command does not
establish that the supervisor is deployed. The local development environment
has no Railway connector, CLI or injected Railway bindings, so live scheduling
has not been verified here. Preserve the existing `/app/private` volume, one
replica, Python 3.13, backup environment settings and configured start command.
Do not change them merely to release this patch. If scheduling is unverified,
inspect it and ensure a fresh verified backup before pushing.

Only when ready to release, the operator runs:

```sh
git push origin HEAD:v2-production-integration
```

That push may auto-deploy. Watch the new deployment for the expected commit,
`Production bot ready`, and continued backup verification. Confirm the existing
registry/transition remains in place; never re-import, reset or replace
`/app/private/production.sqlite3` with an old local database. Do not launch a
second bot or run offline test commands against the production database.

## Discord acceptance using a real table

1. Use a genuine applied S9 table and a player who actually incurred a negative
   loss. Choose another authorized Reporter; self-refunds must be denied.
   Use the original applied table loss, not its raw loss before the floor.
2. Open `/reduce_loss` with the intended fraction and a factual reason. Inspect
   original/retained loss, current MMR and credit, then **Cancel**. Verify `/mmr`
   and the player's rank are unchanged. A non-Reporter must be denied access.
3. If a real reduction is warranted, have the responsible Reporter reopen it
   and confirm once. If no genuine reduction is warranted, stop after the
   preview/cancel checks; wait for a legitimate case rather than fabricating a
   match or consuming the one-time slot with fraction 1.
4. Verify the confirmation's MMR credit, staff-log actor/reason and the player's
   current rank (respecting restrictions). If another table applied meanwhile,
   the preview must refresh and require another confirmation. If role sync is
   pending, use `/fix_roles`; do not repeat the credit.
5. Reopen the same player/table reduction: it must be rejected without another
   credit. Compare `/mmr` with the credited balance plus any real intervening
   table changes. Previously applied results must remain unchanged.

Rounding, concurrent confirmations, rollback, replay, reversal boundaries and
role-failure retries are exercised on temporary databases in regression tests;
do not trigger artificial disputes/reversals or force role failures on live
matches to test them. Historical images remain snapshots. The refund-aware
statistics API is ready for the queued `/stats` restoration; no `/stats` slash
command is added by this patch.
