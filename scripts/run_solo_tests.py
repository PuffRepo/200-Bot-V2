"""Run only the V2 release test groups; no Discord connection or lab DB needed."""
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
PATTERNS=('test_flag_requests.py','test_stats_graph.py','test_countries.py','test_international_names.py','test_registry_import.py','test_launch_scope.py','test_s9_ratings.py','test_lab_store.py','test_lab_seasons.py',
          'test_season_policy.py','test_rating_preview.py','test_solo_lab_audit.py',
          'test_connection_cleanup.py','test_reporter_flow.py','test_lab_reporter_access.py',
          'test_table_image.py','test_table_buttons.py','test_lorenzi_results.py','test_table_publication.py','test_restored_moderation.py','test_loss_reductions.py','test_statistics.py','test_public_feed.py','test_member_sync.py','test_runtime_config.py','test_runtime_commands.py','test_correction_buttons.py')
if __name__=='__main__':
    suite=unittest.TestSuite()
    for pattern in PATTERNS:
        group=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern=pattern,top_level_dir=str(ROOT/'tests'))
        if not group.countTestCases():
            raise SystemExit(f'No tests discovered for {pattern}; update is incomplete.')
        suite.addTests(group)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
