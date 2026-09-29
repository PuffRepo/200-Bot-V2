import copy
import json
from pathlib import Path
import tempfile
import unittest

from v2.lab_store import LabStore
from v2.season_policy import load_policy, preview_lab_transition, rank_for_mmr

POLICY = Path(__file__).resolve().parents[1] / 'settings/s9-policy.json'


class SeasonPolicyTest(unittest.TestCase):
    def test_boundaries_and_unbounded_grandmaster(self):
        policy = load_policy(POLICY)
        self.assertEqual(rank_for_mmr(policy, None), 'Placement')
        for index, rank in enumerate(policy['ranks']):
            self.assertEqual(rank_for_mmr(policy, rank['minimum']), rank['name'])
            if index:
                self.assertEqual(rank_for_mmr(policy, rank['minimum'] - 1), policy['ranks'][index - 1]['name'])
        self.assertEqual(rank_for_mmr(policy, 100000), 'Grandmaster')
        with self.assertRaises(ValueError):
            rank_for_mmr(policy, -1)

    def test_preview_preserves_database_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lab.sqlite3'
            store = LabStore(path)
            request = store.request_verify(123, 'https://mkcentral.com/registry/players/profile?id=12')
            store.review(request, 456, True, 'Tester')
            store.strike(456, 123, 'test', 0)
            before = path.read_bytes()
            report = preview_lab_transition(path, load_policy(POLICY))
            self.assertEqual(report['currently_unexpired_strikes'], 1)
            self.assertEqual(report['strike_history_rows_to_preserve'], 1)
            self.assertEqual(report['proposed_new_season_strikes'], 0)
            self.assertFalse(report['apply_ready'])
            self.assertEqual(report['rating_reset'], 'pending')
            self.assertEqual(path.read_bytes(), before)

    def test_missing_database_is_not_created(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing.sqlite3'
            with self.assertRaises(sqlite3.OperationalError):
                preview_lab_transition(path, load_policy(POLICY))
            self.assertFalse(path.exists())

    def test_bad_configuration_rejected(self):
        policy = load_policy(POLICY)
        for mutate in (
            lambda p: p.update(rating_reset='guess'),
            lambda p: p['ranks'][1].update(minimum=0),
            lambda p: p['ranks'][1].update(name='Iron'),
        ):
            with tempfile.TemporaryDirectory() as directory:
                bad = copy.deepcopy(policy)
                mutate(bad)
                path = Path(directory) / 'policy.json'
                path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):
                    load_policy(path)
