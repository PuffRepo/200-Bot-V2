"""Preview a per-player season reset from read-only CSV exports.

Run from the repository root: python -m scripts.season_reset_preview --help
"""

import argparse
import csv
import sys
from dataclasses import asdict
from pathlib import Path

from v2.season_reset import MatchResult, Player, Rank, RankedMember, build_preview


def read_csv(path: Path, columns: set[str]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        missing = columns - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path}: missing columns: {', '.join(sorted(missing))}")
        return list(reader)


def number(value: str | None, label: str, *, nullable: bool = False) -> int | None:
    if value is None or not value.strip():
        if nullable:
            return None
        raise ValueError(f"Missing {label}")
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"Invalid {label}: {value!r}") from error


def required(value: str | None, label: str) -> int:
    result = number(value, label)
    assert result is not None
    return result


def preview_from_exports(args: argparse.Namespace):
    players = [
        Player(required(row["player_id"], "player_id"), number(row["mmr"], "mmr", nullable=True),
               number(row["rank_id"], "rank_id", nullable=True))
        for row in read_csv(args.players, {"player_id", "mmr", "rank_id"})
    ]
    results = [
        MatchResult(required(row["player_id"], "player_id"), required(row["mogi_id"], "mogi_id"),
                    number(row["new_mmr"], "new_mmr", nullable=True))
        for row in read_csv(args.results, {"player_id", "mogi_id", "new_mmr"})
    ]
    ranks = [
        Rank(required(row["rank_id"], "rank_id"), row["rank_name"].strip(),
             required(row["mmr_min"], "mmr_min"), required(row["mmr_max"], "mmr_max"))
        for row in read_csv(args.ranks, {"rank_id", "rank_name", "mmr_min", "mmr_max"})
    ]
    members = [
        RankedMember(required(row["player_id"], "player_id"), required(row["rank_role_id"], "rank_role_id"))
        for row in read_csv(args.ranked_members, {"player_id", "rank_role_id"})
    ]
    return build_preview(players, results, members, ranks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--players", type=Path, required=True, help="Previous-season player CSV")
    parser.add_argument("--results", type=Path, required=True, help="Previous-season player_mogi CSV")
    parser.add_argument("--ranks", type=Path, required=True, help="Current rank IDs and MMR ranges CSV")
    parser.add_argument("--ranked-members", type=Path, required=True, help="Current guild rank-role roster CSV")
    parser.add_argument("--output", type=Path, required=True, help="Destination preview CSV")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing preview CSV")
    args = parser.parse_args(argv)
    if args.output.resolve() in {path.resolve() for path in
                                 (args.players, args.results, args.ranks, args.ranked_members)}:
        parser.error("Output may not replace an input export")
    if args.output.exists() and not args.overwrite:
        parser.error("Output exists; choose another path or pass --overwrite")

    try:
        preview = preview_from_exports(args)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=(
                "player_id", "current_role_id", "database_rank_id", "current_mmr",
                "table_count", "median_mmr", "proposed_mmr", "proposed_rank_id",
                "proposed_rank_name", "source", "status", "notes",
            ))
            writer.writeheader()
            for row in preview.rows:
                writer.writerow(asdict(row))
    except (OSError, ValueError) as error:
        print(f"Preview failed: {error}", file=sys.stderr)
        return 2

    reviewed = sum(row.status == "review" for row in preview.rows)
    medians = sum(row.source == "median" for row in preview.rows)
    print(f"{len(preview.rows)} ranked players: {medians} medians, "
          f"{len(preview.rows) - medians} carryovers, {reviewed} requiring review. "
          f"{len(preview.flagged_tables)} tables flagged. Preview: {args.output}")
    return 1 if reviewed else 0


if __name__ == "__main__":
    raise SystemExit(main())
