"""Local S9 MMR graph from recorded balances, never cumulative filtered deltas."""
from io import BytesIO
from decimal import Decimal, ROUND_FLOOR
from threading import RLock
import os
from tempfile import gettempdir
from pathlib import Path

# Keep Matplotlib's font cache in a writable temporary location in Railway.
os.environ.setdefault('MPLCONFIGDIR',str(Path(gettempdir())/'200-lounge-matplotlib'))
from matplotlib import rc_context,rc_params_from_file
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator
from v2.season_policy import load_policy
from v2.lab_ratings import POLICY_PATH

ROOT=Path(__file__).resolve().parents[1]
RENDER_LOCK=RLock()
LEGACY_COLORS={'Iron':'#817876','Bronze':'#E67E22','Silver':'#7D8396',
               'Gold':'#F1C40F','Platinum':'#3FABB8','Emerald':'#50c878',
               'Ruby':'#ff5575','Diamond':'#9CCBD6','Master':'#0E0B0B','Grandmaster':'#A3022C'}


def format_score_average(value):
    return '—' if value is None else format(Decimal(str(value)).quantize(Decimal('.01'),rounding=ROUND_FLOOR),'.2f')


def graph_points(result):
    history=sorted(result['history'],key=lambda row:row['sequence'])
    if not history:return []
    points=[]
    for row in history:
        before=row['before']
        if before is not None and (not points or points[-1]['mmr']!=before):
            points.append(dict(mmr=before,label=f"Before #{row['table_id']}",kind='before',connection='gap' if points else None))
        points.append(dict(mmr=row['after'],label=f"#{row['table_id']}",kind='table',
                           connection='table' if before is not None else 'gap' if points else None))
    current=result['player']['mmr']
    if current is not None:
        points.append(dict(mmr=current,label='Current',kind='current',connection='gap'))
    return points


def render_stats_graph(result):
    points=graph_points(result)
    if not points:return None
    # Before/current observations share a match index rather than inventing games.
    xs=[];played=0
    for point in points:
        if point['kind']=='table':played+=1
        xs.append(played)
    values=[point['mmr'] for point in points]
    style=rc_params_from_file(ROOT/'plotting/lounge_style.mplstyle',use_default_template=False)
    # rc_context uses global Matplotlib settings: serialize it across command threads.
    with RENDER_LOCK,rc_context(style):
        figure=Figure(figsize=(6.4,4.8),dpi=100)
        FigureCanvasAgg(figure)
        axes=figure.subplots()
        try:
            # This first pass sets the same automatic limits as the original plot.
            axes.plot(xs,values,color='snow',linewidth=0,alpha=0)
            xmin,xmax=axes.get_xlim();ymin,ymax=axes.get_ylim()
            ranks=load_policy(POLICY_PATH)['ranks']
            for i,rank in enumerate(ranks):
                lower=max(ymin,rank['minimum'])
                upper=min(ymax,ranks[i+1]['minimum'] if i+1<len(ranks) else ymax)
                if lower<upper:
                    axes.fill_between(xs if len(set(xs))>1 else [xmin,xmax],lower,upper,color=LEGACY_COLORS[rank['name']])
            axes.fill_between(xs,ymin,values,color='#212121',alpha=.3)
            has_gap=False
            for i in range(1,len(points)):
                if points[i]['connection']=='table':
                    axes.plot(xs[i-1:i+1],values[i-1:i+1],color='snow',linewidth=1.0)
                elif values[i]!=values[i-1]:
                    axes.plot(xs[i-1:i+1],values[i-1:i+1],color='#ffcf70',linewidth=1.0,
                              linestyle='--',label='Other activity / current MMR' if not has_gap else None)
                    has_gap=True
            if len(set(zip(xs,values)))==1:
                axes.plot(xs[-1],values[-1],marker='o',color='snow',markersize=3)
            if has_gap:
                axes.legend(loc='best',fontsize=8,facecolor='#0a2d61',labelcolor='white')
            axes.set_xlim(xmin,xmax);axes.set_ylim(ymin,ymax)
            axes.set_xlabel('Matches played');axes.set_ylabel('MMR')
            axes.xaxis.set_major_locator(MaxNLocator(integer=True))
            axes.grid(True,which='both',axis='both',color='snow',linestyle=':')
            output=BytesIO();figure.savefig(output,format='png',bbox_inches='tight')
            return output.getvalue()
        finally:
            figure.clear()
