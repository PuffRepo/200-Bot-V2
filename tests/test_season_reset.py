"""Checks for the season rule agreed with the 200 Lounge staff."""

import csv

import pytest

from scripts.season_reset_preview import main
from v2.season_reset import MatchResult, Player, Rank, RankedMember, build_preview


RANKS = [
    Rank(index, name, lower, upper)
    for index, (name, lower, upper) in enumerate(
        (
            ("Iron", 0, 999), ("Bronze", 1000, 1999),
            ("Silver", 2000, 2999), ("Gold", 3000, 3999),
            ("Platinum", 4000, 4999), ("Ruby", 5000, 5999),
            ("Diamond", 6000, 6999), ("Master", 7000, 7999),
            ("Grandmaster", 8000, 99999),
        ),
        1,
    )
]


def complete_table(mogi_id: int, first_mmr: int, *, second_mmr: int | None = None):
    participants = [MatchResult(1, mogi_id, first_mmr)]
    if second_mmr is not None:
        participants.append(MatchResult(2, mogi_id, second_mmr))
    participants.extend(
        MatchResult(player_id, mogi_id, 1500)
        for player_id in range(20, 31 - (second_mmr is not None))
    )
    assert len(participants) == 12
    return participants


def test_individual_medians_even_rounding_and_no_game_fallback():
    results = [
        *complete_table(10, 2000, second_mmr=7000),
        *complete_table(11, 4000, second_mmr=7501),
        *complete_table(12, 6000),
    ]
    preview = build_preview(
        [Player(1, 3600, 4), Player(2, 7500, 8), Player(3, 2450, 3)],
        results,
        [RankedMember(1, 4), RankedMember(2, 8), RankedMember(3, 3)],
        RANKS,
    )
    a, b, inactive = preview.rows
    assert (a.table_count, str(a.median_mmr), a.proposed_mmr, a.proposed_rank_name) == (3, "4000", 4000, "Platinum")
    assert (b.table_count, str(b.median_mmr), b.proposed_mmr, b.proposed_rank_name) == (2, "7250.5", 7251, "Master")
    assert (inactive.table_count, inactive.source, inactive.proposed_mmr, inactive.proposed_rank_name) == (0, "carryover", 2450, "Silver")
    assert all(row.status == "ready" for row in preview.rows)
    assert preview.flagged_tables == ()


def test_incomplete_tables_and_missing_player_require_review():
    preview = build_preview(
        [Player(1, 3600, 4)],
        [MatchResult(1, 10, 4200)],
        [RankedMember(1, 4), RankedMember(2, 5)],
        RANKS,
    )
    assert preview.rows[0].status == "review"
    assert "table 10" in preview.rows[0].notes
    assert preview.rows[1].status == "review"
    assert preview.rows[1].proposed_mmr is None
    assert "missing from previous-season" in preview.rows[1].notes


def test_current_role_is_the_eligibility_source_and_conflicts_are_visible():
    preview = build_preview(
        [Player(1, 3600, 4), Player(2, 2450, 3)],
        [],
        [RankedMember(1, 4), RankedMember(1, 5), RankedMember(2, 9999)],
        RANKS,
    )
    assert [row.player_id for row in preview.rows] == [1]
    assert preview.rows[0].status == "review"
    assert preview.rows[0].current_role_id is None


def test_old_rank_map_without_ruby_cannot_be_used():
    with pytest.raises(ValueError, match="Ruby|ruby"):
        build_preview([Player(1, 3600, 4)], [], [RankedMember(1, 4)], RANKS[:-4] + RANKS[-3:])


def test_cli_exports_read_only_preview(tmp_path):
    def write_csv(filename, columns, rows):
        path = tmp_path / filename
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(columns)
            writer.writerows(rows)
        return path

    players = write_csv("players.csv", ("player_id", "mmr", "rank_id"), ((1, 3600, 4),))
    results = write_csv("results.csv", ("player_id", "mogi_id", "new_mmr"), ())
    ranks = write_csv("ranks.csv", ("rank_id", "rank_name", "mmr_min", "mmr_max"),
                      ((r.rank_id, r.rank_name, r.mmr_min, r.mmr_max) for r in RANKS))
    members = write_csv("members.csv", ("player_id", "rank_role_id"), ((1, 4),))
    output = tmp_path / "preview.csv"
    argv = ["--players", str(players), "--results", str(results), "--ranks", str(ranks),
            "--ranked-members", str(members), "--output", str(output)]
    assert main(argv) == 0
    with output.open(newline="", encoding="utf-8") as stream:
        row = next(csv.DictReader(stream))
    assert (row["source"], row["proposed_mmr"], row["status"]) == ("carryover", "3600", "ready")
    assert main(argv + ["--overwrite"]) == 0
    with pytest.raises(SystemExit, match="2"):
        main(argv[:-1] + [str(players)])
