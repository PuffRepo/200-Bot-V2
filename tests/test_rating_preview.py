import asyncio
import unittest

from v2.lab_store import LabError
from v2.rating_preview import calculate_mmr, calculate_pre_mmr, preview_fixture_table


class RatingPreviewTest(unittest.TestCase):
    def test_s8_september_23_real_table(self):
        # Transcribed from the supplied score and MMR images. Member order
        # follows the score image; ratings are matched by name, not row order.
        reference = [
            [('trae', 129, 10304, 10428), ('Fluberx', 70, 2741, 2865),
             ('Pom2tere', 66, 2223, 2347)],
            [('KLOuFRENS', 96, 5189, 5317), ('ths', 95, 3809, 3937),
             ('Beltran900', 54, 218, 346)],
            [('Popoff', 108, 6050, 5992), ('IDS94_', 80, 4120, 4062),
             ('musu', 53, 1954, 1896)],
            [('KIRA', 87, 2531, 2335), ('Day', 81, 6833, 6637),
             ('mktristan', 65, 3550, 3354)],
        ]
        teams = [[sum(p[1] for p in members),
                  sum(p[2] for p in members) / 3, place]
                 for place, members in enumerate(reference, 1)]
        self.assertEqual(sum(t[0] for t in teams), 984)

        async def calculate():
            await calculate_mmr(teams, await calculate_pre_mmr(3, teams))

        asyncio.run(calculate())
        self.assertEqual([t[-1] for t in teams], [124, 128, -58, -196])
        for team, members in zip(teams, reference):
            for name, score, before, after in members:
                with self.subTest(player=name):
                    self.assertEqual(before + team[-1], after)

    def test_synthetic_equal_mmr_format_2_reference_deltas(self):
        scores = [100, 100, 90, 90, 85, 85, 80, 80, 70, 70, 67, 67]
        raw = ' '.join(f'LAB{i:02d} {score}' for i, score in enumerate(scores, 1))
        teams = asyncio.run(preview_fixture_table(raw, 2, 6000))
        self.assertEqual([t['delta'] for t in teams], [200, 120, 40, -40, -120, -200])
        self.assertEqual([t['after'] for t in teams], [6200, 6120, 6040, 5960, 5880, 5800])
        self.assertEqual([t['place'] for t in teams], [1, 2, 3, 4, 5, 6])

    def test_ties_and_fixture_only_gate(self):
        raw = ' '.join(f'LAB{i:02d} 82' for i in range(1, 13))
        teams = asyncio.run(preview_fixture_table(raw, 2, 6000))
        self.assertEqual([t['delta'] for t in teams], [0] * 6)
        self.assertEqual([t['place'] for t in teams], [1] * 6)
        with self.assertRaises(LabError):
            asyncio.run(preview_fixture_table(raw.replace('LAB01', 'Real'), 2, 6000))


if __name__ == '__main__':
    unittest.main()
