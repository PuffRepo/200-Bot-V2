import unittest
from v2.lab_access import can_report
class ReporterAccessTest(unittest.TestCase):
    def test_live_and_wrong_channel_denied_even_for_staff(self):
        self.assertFalse(can_report(99,2,True,[3],3,1,2))
        self.assertFalse(can_report(1,99,True,[3],3,1,2))
    def test_designated_reporter_without_manage_server(self):
        self.assertTrue(can_report(1,2,False,[3],3,1,2))
        self.assertFalse(can_report(1,2,False,[4],3,1,2))
    def test_missing_configuration_does_not_grant_reporter(self):
        self.assertFalse(can_report(1,2,False,[3],None,1,2))
    def test_staff_can_run_solo_without_reporter_role(self):
        self.assertTrue(can_report(1,2,True,[],None,1,2))
