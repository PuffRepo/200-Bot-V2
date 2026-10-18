import unittest
from io import BytesIO
from PIL import Image
from v2.table_image import render_table, render_results_local as render_results, results_layout, results_date
from v2.rating import calculate_table
from v2.lab_ratings import POLICY_PATH
from v2.season_policy import load_policy
class TableImageTest(unittest.TestCase):
    def test_all_five_formats_render_exact_input_without_network(self):
        p=load_policy(POLICY_PATH)
        for fmt in (1,2,3,4,6):
            players=[{'user_id':i,'name':f'LAB{i:02d}','score':82,'mmr':None} for i in range(1,13)]
            rows=calculate_table(p,players,fmt,'tier-all')
            raw=' '.join(f"{r['name']} 82" for r in rows)
            png=render_table(1,fmt,'tier-all',raw,rows)
            with Image.open(BytesIO(png)) as image:
                self.assertEqual(image.size,(1180,750));self.assertEqual(image.format,'PNG');image.verify()
    def test_extreme_rank_and_long_names_render(self):
        p=load_policy(POLICY_PATH)
        players=[{'user_id':i,'name':'W'*14+str(i),'score':82,'mmr':30000} for i in range(1,13)]
        rows=calculate_table(p,players,6,'tier-all')
        png=render_table(2,6,'tier-all',' '.join(f"{r['name']} 82" for r in rows),rows)
        self.assertTrue(png.startswith(b'\x89PNG'))

    def test_reference_results_team_and_individual_podiums(self):
        p=load_policy(POLICY_PATH)
        scores=[129,70,66,96,95,54,108,80,53,87,81,65]
        players=[{'user_id':i,'name':f'LAB{i:02d}','score':score,'mmr':3750} for i,score in enumerate(scores,1)]
        rows=calculate_table(p,players,3,'tier-all')
        raw=' '.join(f"{r['name']} {r['score']}" for r in rows)
        groups=results_layout(3,raw,rows)
        self.assertEqual([(g['place'],g['total']) for g in groups],[(1,265),(2,245),(3,241),(4,233)])
        podium={r['name']:r['place'] for g in groups for r in g['players'] if r['place']<=3}
        self.assertEqual(podium,{'LAB01':1,'LAB07':2,'LAB04':3})
        for fmt in (1,2,3,4,6):
            with Image.open(BytesIO(render_results(3,fmt,'tier-all',raw,rows))) as image:
                self.assertEqual(image.size,(1180,750));self.assertEqual(image.getpixel((0,0)),(0,0,0));image.load()

    def test_tied_podiums_use_competition_places(self):
        rows=[{'name':f'LAB{i:02d}'} for i in range(1,13)]
        values=[100,100,90,90,80,80,74,74,74,74,74,74]
        groups=results_layout(1,' '.join(f"{r['name']} {v}" for r,v in zip(rows,values)),rows)
        self.assertEqual([g['place'] for g in groups[:5]],[1,1,3,3,5])

    def test_reference_date_is_stable_from_table_creation(self):
        self.assertEqual(results_date('2026-09-26 23:59:59'),'26 Sep 2026')

    def test_legacy_mmr_headings_signed_rank_changes_and_placement(self):
        from unittest.mock import patch
        from PIL import ImageDraw
        rows=[{'name':f'P{i}','before':1499,'after':1500,'applied_delta':1,'kind':'table','rank':'Bronze'} for i in range(1,13)]
        rows[1].update(before=1500,after=1499,applied_delta=-1,rank='Iron')
        rows[2].update(before=None,after=3750,applied_delta=None,kind='placement',rank='Silver')
        raw=' '.join(f"{r['name']} 82" for r in rows)
        original=ImageDraw.ImageDraw.text
        with patch.object(ImageDraw.ImageDraw,'text',autospec=True,side_effect=original) as text:
            render_table(7,3,'tier-all',raw,rows)
        labels=[c.args[2] for c in text.call_args_list]
        for label in ['tier-all 3v3','RANKUPS','+ Bronze','- Iron','+ Silver','Placement','—']:
            self.assertIn(label,labels)
        self.assertNotIn('RANK CHANGE',labels)
        self.assertFalse(any('Yes applies' in value for value in labels))
