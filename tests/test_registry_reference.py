import unittest
from scripts.export_registry_reference import candidate,reference

class RegistryReferenceTest(unittest.TestCase):
    def record(self,text):return {'content':text,'embeds':[],'url':'source'}
    def test_explicit_single_pair_is_only_a_candidate(self):
        pair=candidate(self.record('<@123456789012345678> https://www.mkcentral.com/registry/players/profile?id=1776'))
        self.assertEqual(pair['discord_id'],'123456789012345678')
        self.assertEqual(pair['status'],'candidate_requires_staff_review')
    def test_ambiguous_mentions_profiles_and_unlabelled_ids_are_not_matched(self):
        url='https://mkcentral.com/registry/players/profile?id=1776'
        for text in ['Name '+url,'123456789012345678 '+url,'<@123456789012345678> <@223456789012345678> '+url,'<@123456789012345678> '+url+' '+url.replace('1776','1777')]:
            self.assertIsNone(candidate(self.record(text)))
    def test_embed_links_are_candidates(self):
        r=self.record('');r['embeds']=[{'description':'<@123456789012345678>','url':'https://mkcentral.com/registry/players/profile?id=1776'}]
        self.assertIsNotNone(candidate(r))
    def test_duplicate_profile_and_missing_links_are_flagged_without_excluding_ranked_members(self):
        members=[{'discord_id':str(i),'rank_roles':['Gold'],'placement_roles':[]} for i in range(3)]
        rows=reference(members,[{'discord_id':'0','mkc_id':'42'},{'discord_id':'1','mkc_id':'42'}])
        self.assertEqual(rows[0]['link_status'],'conflict')
        self.assertEqual(rows[2]['link_status'],'missing')
        self.assertEqual(rows[2]['registration_reference'],'rank_role_existing_player')
