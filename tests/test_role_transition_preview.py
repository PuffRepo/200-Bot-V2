import unittest
from v2.role_transition_preview import build_preview

class TransitionPreviewTest(unittest.TestCase):
    def setUp(self):
        self.config={'guild_id':'1','rank_role_ids':['10','11'],'placement_role_id':'12','restriction_role_ids':['13','14'],'reinstated_user_id':'200'}
    def player(self,uid,decision=None):return {'discord_id':uid,'name':'player','staff_decision':decision}
    def member(self,*roles):return {'role_ids':list(roles),'bot':False}
    def test_staff_and_historical_roles_preserved(self):
        p=build_preview([self.player('100')],{'100':self.member('1','10','11','99','98')},self.config)['rows'][0]
        self.assertEqual(p['remove_role_ids'],['10','11']);self.assertEqual(p['add_role_ids'],['12']);self.assertEqual(p['preserve_role_ids'],['1','98','99'])
    def test_placement_unchanged_and_absence_proposes_nothing(self):
        p=build_preview([self.player('100'),self.player('101')],{'100':self.member('12')},self.config)
        self.assertEqual([r['status'] for r in p['rows']],['unchanged','absent']);self.assertFalse(p['rows'][1]['add_role_ids'])
    def test_restrictions_block_changes_except_approved_corbin(self):
        players=[self.player('100'),self.player('200',{'remove_role_ids_on_transition':['13','14']})]
        p=build_preview(players,{'100':self.member('10','13'),'200':self.member('13','14','99')},self.config)
        self.assertEqual(p['rows'][0]['status'],'review');self.assertEqual(p['rows'][0]['remove_role_ids'],[])
        self.assertEqual(p['rows'][1]['remove_role_ids'],['13','14']);self.assertEqual(p['rows'][1]['preserve_role_ids'],['99'])
    def test_missing_reinstatement_audit_stops(self):
        with self.assertRaises(ValueError):build_preview([self.player('200')],{'200':self.member('13')},self.config)
    def test_outside_registry_reported_without_changes(self):
        p=build_preview([],{'100':self.member('10'),'101':self.member('12'),'102':self.member('99')},self.config)
        self.assertEqual(p['outside_registry_role_holders'],['100','101']);self.assertEqual(p['rows'],[])
    def test_temporary_name_note_no_nickname_operations(self):
        p=build_preview([self.player('100',{'name_resolution':{'temporary_registry_name':'gold'}})],{'100':self.member('10')},self.config)
        self.assertIn('formal name change',p['rows'][0]['notes'][0]);self.assertFalse(p['discord_changes_applied'])
