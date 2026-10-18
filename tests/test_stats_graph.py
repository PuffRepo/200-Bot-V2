import copy
from io import BytesIO
import unittest
from PIL import Image
from v2.stats_graph import graph_points,render_stats_graph,format_score_average


def result(history,current):return dict(history=history,player=dict(mmr=current))
def event(table,before,after,sequence=None):return dict(table_id=table,sequence=sequence or table,before=before,after=after)

class StatsGraphTest(unittest.TestCase):
    def test_placement_is_not_an_invented_zero_mmr(self):
        points=graph_points(result([event(1,None,3750)],3750))
        self.assertEqual([p['mmr'] for p in points],[3750,3750])
        self.assertIsNone(points[0]['connection'])
    def test_refund_and_later_table_preserve_original_recorded_balances(self):
        data=result([event(2,5900,6100),event(1,6000,5800)],6100)
        original=copy.deepcopy(data);points=graph_points(data)
        self.assertEqual([p['mmr'] for p in points],[6000,5800,5900,6100,6100])
        self.assertEqual([p['connection'] for p in points],[None,'table','gap','table','gap'])
        self.assertEqual(data,original)
    def test_filtered_tables_use_actual_before_not_sum_of_selected_deltas(self):
        data=result([event(10,9000,9100),event(2,5000,5200)],8900)
        points=graph_points(data)
        self.assertEqual([p['mmr'] for p in points],[5000,5200,9000,9100,8900])
        self.assertEqual(points[-1]['kind'],'current')
    def test_latest_refund_is_a_current_balance_gap_not_rewritten_table(self):
        points=graph_points(result([event(1,6000,5800)],5900))
        self.assertEqual([p['mmr'] for p in points],[6000,5800,5900])
        self.assertEqual(points[-1]['connection'],'gap')
    def test_no_selected_history_returns_no_attachment_even_if_rated(self):
        for current in (None,6000):
            self.assertEqual(graph_points(result([],current)),[])
            self.assertIsNone(render_stats_graph(result([],current)))
    def test_png_handles_flat_boundary_high_and_dense_series(self):
        histories=[[event(1,None,1)],[event(1,1500,1500)],[event(1,30000,31000)],
                   [event(i,6000+i,6001+i) for i in range(1,1001)]]
        for history in histories:
            with self.subTest(tables=len(history)):
                png=render_stats_graph(result(history,history[-1]['after']))
                with Image.open(BytesIO(png)) as image:
                    self.assertGreater(image.width,500);self.assertGreater(image.height,350);self.assertEqual(image.format,'PNG');image.verify()

    def test_embed_score_averages_floor_instead_of_round_up(self):
        for value,expected in [(82.999,'82.99'),(82,'82.00'),(0,'0.00'),(1.15,'1.15'),(None,'—')]:
            self.assertEqual(format_score_average(value),expected)

    def test_legacy_axes_labels_grid_and_real_match_count(self):
        from unittest.mock import patch
        from matplotlib.axes import Axes
        original_plot=Axes.plot
        labels=[];calls=[]
        def plot(axes,*args,**kwargs):
            calls.append((args,kwargs));return original_plot(axes,*args,**kwargs)
        with patch.object(Axes,'plot',new=plot),patch.object(Axes,'set_xlabel',autospec=True,side_effect=lambda ax,label,*a,**k:labels.append(label)),patch.object(Axes,'set_ylabel',autospec=True,side_effect=lambda ax,label,*a,**k:labels.append(label)):
            render_stats_graph(result([event(1,6000,5800),event(2,5900,6100)],6100))
        self.assertEqual(labels,['Matches played','MMR'])
        self.assertEqual(calls[0][0][0],[0,1,1,2,2])
        self.assertTrue(any(kwargs.get('color')=='snow' and kwargs.get('linewidth')==1 for _,kwargs in calls))
        self.assertTrue(any(kwargs.get('linestyle')=='--' for _,kwargs in calls))
