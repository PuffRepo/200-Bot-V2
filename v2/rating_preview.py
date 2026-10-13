"""Read-only lab comparison against the inherited MMR formula.

This is a calculation preview, not a player rating ledger or S9 scoring policy.
"""

import importlib.util
from pathlib import Path

from v2.lab_store import LabError, parse_scores


def _legacy_math_module(filename: str):
    """Load only a legacy math file; helpers/__init__.py imports the old DB."""
    path = Path(__file__).resolve().parents[1] / 'helpers' / filename
    spec = importlib.util.spec_from_file_location(f'v2_reference_{path.stem}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


calculate_mmr = _legacy_math_module('calculate_mmr.py').calculate_mmr
calculate_pre_mmr = _legacy_math_module('calculate_pre_mmr.py').calculate_pre_mmr


async def preview_fixture_table(scores: str, format_size: int, starting_mmr: int) -> list[dict]:
    if not isinstance(starting_mmr, int) or not 1 <= starting_mmr <= 10999:
        raise LabError("Synthetic starting MMR must be 1–10999.")
    pairs = parse_scores(scores, format_size)
    if {name.upper() for name, _ in pairs} != {f"LAB{i:02d}" for i in range(1, 13)}:
        raise LabError("Rating preview is limited to the 12 fictional LAB01–LAB12 players.")
    teams = []
    for start in range(0, 12, format_size):
        members = pairs[start:start + format_size]
        teams.append([*members, sum(score for _, score in members), float(starting_mmr)])
    teams.sort(key=lambda team: team[-2], reverse=True)
    prior_score = None
    prior_place = 1
    for index, team in enumerate(teams, 1):
        place = prior_place if team[-2] == prior_score else index
        team.append(place)
        prior_score, prior_place = team[-3], place
    values = await calculate_pre_mmr(format_size, teams)
    await calculate_mmr(teams, values)
    return [{"place": team[-2], "score": team[-4], "delta": team[-1],
             "before": starting_mmr, "after": max(1, starting_mmr + team[-1]),
             "players": [name for name, _ in team[:format_size]]} for team in teams]
