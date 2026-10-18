"""Local S9 MMR graph from recorded balances, never cumulative filtered deltas."""
from io import BytesIO
import math
from decimal import Decimal, ROUND_FLOOR
from PIL import Image,ImageDraw
from v2.table_image import font,RANK_COLORS
from v2.season_policy import load_policy
from v2.lab_ratings import POLICY_PATH


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
    image=Image.new('RGB',(1100,600),'#0a2d61');draw=ImageDraw.Draw(image)
    draw.text((40,24),'MMR History | S9',font=font(29,bold=True),fill='white')
    draw.text((40,67),'Recorded balances at selected applied tables',font=font(19),fill='#dddddd')
    left,top,right,bottom=90,108,945,470
    values=[p['mmr'] for p in points]
    span=max(values)-min(values);padding=max(100,span*.12)
    low=max(0,math.floor(min(values)-padding));high=math.ceil(max(values)+padding)
    def y(value):return bottom-(value-low)*(bottom-top)/(high-low)
    ranks=load_policy(POLICY_PATH)['ranks']
    for i,rank in enumerate(ranks):
        minimum=rank['minimum'];maximum=ranks[i+1]['minimum'] if i+1<len(ranks) else high
        a,b=max(low,minimum),min(high,maximum)
        if a>=b:continue
        color=RANK_COLORS[rank['name']]
        rgb=tuple(round(int(color[j:j+2],16)*.48) for j in (1,3,5))
        draw.rectangle((left,y(b),right,y(a)),fill=rgb)
        draw.text((right+10,(y(a)+y(b))/2),rank['name'],font=font(14),fill='white',anchor='lm')
    for i in range(6):
        value=low+(high-low)*i/5;yy=y(value)
        draw.line((left,yy,right,yy),fill='#8396ad',width=1)
        draw.text((left-12,yy),f'{round(value):,}',font=font(16),fill='white',anchor='rm')
    coords=[(left+(right-left)*i/max(1,len(points)-1),y(p['mmr'])) for i,p in enumerate(points)]
    for i in range(1,len(points)):
        start,end=coords[i-1],coords[i]
        if points[i]['connection']=='table':draw.line((*start,*end),fill='white',width=3)
        else:
            length=math.dist(start,end);steps=max(1,math.ceil(length/9))
            for step in range(0,steps,2):
                a=step/steps;b=min(1,(step+1)/steps)
                draw.line((start[0]+(end[0]-start[0])*a,start[1]+(end[1]-start[1])*a,
                           start[0]+(end[0]-start[0])*b,start[1]+(end[1]-start[1])*b),fill='#ffcf70',width=2)
    for point,(xx,yy) in zip(points,coords):
        if len(points)<=80 or point['kind']=='current':
            color='#ffcf70' if point['kind']=='current' else 'white'
            draw.ellipse((xx-4,yy-4,xx+4,yy+4),fill=color)
    tick_indices=sorted({round(i*(len(points)-1)/min(5,len(points)-1)) for i in range(min(5,len(points)-1)+1)}) if len(points)>1 else [0]
    for index in tick_indices:
        draw.text((coords[index][0],bottom+18),points[index]['label'],font=font(14),fill='white',anchor='mt')
    draw.text((40,535),'Solid: table MMR | Dashed: gaps/current balance (other tables, refunds or penalties)',font=font(18),fill='white')
    draw.text((40,565),'Original table balances stay unchanged; the current point includes posted adjustments.',font=font(17),fill='#dddddd')
    output=BytesIO();image.save(output,format='PNG');return output.getvalue()
