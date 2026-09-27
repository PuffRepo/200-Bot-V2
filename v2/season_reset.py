"""Calculate a read-only preview of each ranked player's next-season MMR."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable


RANK_NAMES = frozenset(
    {
        "iron", "bronze", "silver", "gold", "platinum",
        "ruby", "diamond", "master", "grandmaster",
    }
)


@dataclass(frozen=True)
class Player:
    player_id: int
    mmr: int | None
    rank_id: int | None


@dataclass(frozen=True)
class MatchResult:
    player_id: int
    mogi_id: int
    new_mmr: int | None


@dataclass(frozen=True)
class Rank:
    rank_id: int
    rank_name: str
    mmr_min: int
    mmr_max: int


@dataclass(frozen=True)
class RankedMember:
    player_id: int
    rank_role_id: int


@dataclass(frozen=True)
class PreviewRow:
    player_id: int
    current_role_id: int | None
    database_rank_id: int | None
    current_mmr: int | None
    table_count: int
    median_mmr: Decimal | None
    proposed_mmr: int | None
    proposed_rank_id: int | None
    proposed_rank_name: str | None
    source: str
    status: str
    notes: str


@dataclass(frozen=True)
class Preview:
    rows: tuple[PreviewRow, ...]
    flagged_tables: tuple[tuple[int, str], ...]


def median_mmr(values: Iterable[int]) -> Decimal:
    """Use one post-table MMR per completed table, with exact decimal arithmetic."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("A player with no tables has no median")
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return Decimal(ordered[middle])
    return (Decimal(ordered[middle - 1]) + Decimal(ordered[middle])) / 2


def rounded_mmr(value: Decimal) -> int:
    """MMR is stored as an integer; .5 rounds up for nonnegative MMR."""
    if value < 0:
        raise ValueError("MMR cannot be negative")
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _validate_ranks(ranks: Iterable[Rank]) -> tuple[dict[int, Rank], tuple[Rank, ...]]:
    all_ranks = tuple(ranks)
    configured = [rank for rank in all_ranks if rank.rank_name.casefold() in RANK_NAMES]
    names = Counter(rank.rank_name.casefold() for rank in configured)
    missing = RANK_NAMES - names.keys()
    if missing:
        raise ValueError(f"Current rank map is missing: {', '.join(sorted(missing))}")
    if any(count != 1 for count in names.values()):
        raise ValueError("Current rank names must be unique")
    if len({rank.rank_id for rank in configured}) != len(configured):
        raise ValueError("Current rank role IDs must be unique")
    ordered = tuple(sorted(configured, key=lambda rank: rank.mmr_min))
    for rank in ordered:
        if rank.mmr_min < 0 or rank.mmr_min > rank.mmr_max:
            raise ValueError(f"Invalid MMR range for {rank.rank_name}")
    for previous, current in zip(ordered, ordered[1:]):
        if current.mmr_min <= previous.mmr_max:
            raise ValueError(f"Overlapping MMR ranges: {previous.rank_name}, {current.rank_name}")
    return {rank.rank_id: rank for rank in configured}, ordered


def build_preview(
    players: Iterable[Player],
    results: Iterable[MatchResult],
    members: Iterable[RankedMember],
    ranks: Iterable[Rank],
) -> Preview:
    """Preview changes for current guild rank holders; never modify a player or role."""
    ranks_by_id, ordered_ranks = _validate_ranks(ranks)
    players_by_id: dict[int, Player] = {}
    for player in players:
        if player.player_id in players_by_id:
            raise ValueError(f"Duplicate player ID in export: {player.player_id}")
        players_by_id[player.player_id] = player

    roles_by_player: dict[int, set[int]] = defaultdict(set)
    for member in members:
        if member.rank_role_id in ranks_by_id:
            roles_by_player[member.player_id].add(member.rank_role_id)
    if not roles_by_player:
        raise ValueError("No members have a recognized current rank role")

    results_by_player: dict[int, list[MatchResult]] = defaultdict(list)
    results_by_table: dict[int, list[MatchResult]] = defaultdict(list)
    for result in results:
        results_by_player[result.player_id].append(result)
        results_by_table[result.mogi_id].append(result)

    flagged_tables: dict[int, str] = {}
    for mogi_id, table_results in results_by_table.items():
        if len(table_results) != len({result.player_id for result in table_results}):
            flagged_tables[mogi_id] = "duplicate player in table export"
        elif len(table_results) != 12:
            flagged_tables[mogi_id] = f"{len(table_results)} player rows; review completion or substitutes"
        elif any(result.new_mmr is None or result.new_mmr < 0 for result in table_results):
            flagged_tables[mogi_id] = "missing or invalid post-table MMR"

    rows: list[PreviewRow] = []
    for player_id in sorted(roles_by_player):
        roles = roles_by_player[player_id]
        player = players_by_id.get(player_id)
        player_results = results_by_player.get(player_id, [])
        role_id = next(iter(roles)) if len(roles) == 1 else None
        notes: list[str] = []
        if len(roles) > 1:
            notes.append("multiple current rank roles")
        if player is None:
            notes.append("ranked member missing from previous-season player export")
        elif player.mmr is None or player.mmr < 0:
            notes.append("ranked member has no valid current MMR")
        if player and role_id and player.rank_id != role_id:
            notes.append("current rank role differs from database rank ID")
        for mogi_id in sorted({result.mogi_id for result in player_results}):
            if mogi_id in flagged_tables:
                notes.append(f"table {mogi_id}: {flagged_tables[mogi_id]}")

        source = "median" if player_results else "carryover"
        median = None
        proposed = None
        proposed_rank = None
        if player is not None and player.mmr is not None and player.mmr >= 0:
            if player_results and all(result.new_mmr is not None for result in player_results):
                valid_values = [result.new_mmr for result in player_results if result.new_mmr is not None]
                if all(value >= 0 for value in valid_values):
                    median = median_mmr(valid_values)
                    proposed = rounded_mmr(median)
            elif not player_results:
                proposed = player.mmr
            if proposed is not None:
                matches = [rank for rank in ordered_ranks if rank.mmr_min <= proposed <= rank.mmr_max]
                if len(matches) == 1:
                    proposed_rank = matches[0]
                else:
                    notes.append("proposed MMR matches no rank range")

        rows.append(
            PreviewRow(
                player_id=player_id,
                current_role_id=role_id,
                database_rank_id=player.rank_id if player else None,
                current_mmr=player.mmr if player else None,
                table_count=len(player_results),
                median_mmr=median,
                proposed_mmr=proposed,
                proposed_rank_id=proposed_rank.rank_id if proposed_rank else None,
                proposed_rank_name=proposed_rank.rank_name if proposed_rank else None,
                source=source,
                status="review" if notes else "ready",
                notes="; ".join(notes),
            )
        )
    return Preview(tuple(rows), tuple(sorted(flagged_tables.items())))
