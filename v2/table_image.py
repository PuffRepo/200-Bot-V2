"""Legacy-inspired local results/MMR preview; no external rendering service."""
from io import BytesIO
from datetime import datetime
import logging
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
from v2.lab_store import parse_scores
from v2.name_fonts import draw_player_name
from v2.season_policy import load_policy, rank_for_mmr

ROOT=Path(__file__).resolve().parents[1]
FORMAT_COLORS={1:('#76D7C4','#A3E4D7'),2:('#76D7C4','#A3E4D7'),
               3:('#85C1E9','#AED6F1'),4:('#C39BD3','#D2B4DE'),6:('#F1948A','#F5B7B1')}
RANK_COLORS={'Placement':'#dddddd','Iron':'#a9a9a9','Bronze':'#ee7600',
             'Silver':'#add8e6','Gold':'#ffd700','Platinum':'#40c7c7',
             'Emerald':'#50c878','Ruby':'#ff5575','Diamond':'#b0e0e6',
             'Master':'#bb86fc','Grandmaster':'#69001b'}

def font(size, mono=False, bold=False, italic=False):
    if mono:
        candidates=('C:/Windows/Fonts/consola.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf')
    elif bold:
        candidates=('C:/Windows/Fonts/arialbi.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf','C:/Windows/Fonts/arialbd.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf') if italic else ('C:/Windows/Fonts/arialbd.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')
    else:
        candidates=('C:/Windows/Fonts/arial.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    for path in candidates:
        if Path(path).is_file():return ImageFont.truetype(path,size)
    return ImageFont.load_default()

def fit_text(draw, value, size, available, mono=False, bold=False):
    f=font(size,mono,bold)
    while draw.textlength(value,font=f)>available and size>10:
        size-=1;f=font(size,mono,bold)
    return f


def render_table(table_id, format_size, tier, scores, rows):
    """Compact legacy MMR card; scores appear on the separate results card."""
    groups=results_layout(format_size,scores,rows)
    by_name={r['name']:r for r in rows}
    width,height=1180,750
    background=ROOT/'images/mkbg.jpg'
    if background.is_file():
        with Image.open(background) as source:
            image=ImageOps.fit(source.convert('RGB'),(width,height)).convert('RGBA')
    else:image=Image.new('RGBA',(width,height),'#151515')
    # Retain the Mario Kart pattern without sacrificing text contrast.
    image=Image.alpha_composite(image,Image.new('RGBA',image.size,(0,0,0,100)))
    draw=ImageDraw.Draw(image)
    fmt='FFA' if format_size==1 else f'{format_size}v{format_size}'
    draw.text((18,12),f'{tier} {fmt}',font=font(30,True),fill='white')
    draw.text((1160,20),f'S9 | Table #{table_id}',font=font(18,True),fill='#dddddd',anchor='ra')
    edges=[12,85,425,595,725,910,1170]
    for i,label in enumerate(('PLACE','NAME','MMR','+/-','NEW MMR','RANK CHANGE')):
        center=(edges[i]+edges[i+1])/2
        draw.text((center,66),label,font=font(18,True),fill='#ffffff',anchor='mm')
    policy=load_policy(ROOT/'settings/s9-policy.json')
    y=94
    for group in groups:
        for i,member in enumerate(group['players']):
            row=by_name[member['name']]
            old_rank=rank_for_mmr(policy,row['before']);new_rank=row['rank']
            change='—' if row['kind']=='placement' else f"{row['applied_delta']:+d}"
            rank_change=new_rank if new_rank!=old_rank else '—'
            values=[str(group['place']) if i==0 else '',row['name'],
                    'Placement' if row['before'] is None else str(row['before']),change,str(row['after']),rank_change]
            delta_color='#7fff00' if row['kind']=='table' and row['applied_delta']>0 else '#ff4040' if row['kind']=='table' and row['applied_delta']<0 else '#ffffff'
            colors=['white','white','white',delta_color,'#ffff66' if rank_change!='—' else 'white',RANK_COLORS.get(new_rank,'white')]
            for index,(value,color) in enumerate(zip(values,colors)):
                center=(edges[index]+edges[index+1])/2
                if index==1:
                    draw_player_name(draw,(center,y+18),value,22,edges[index+1]-edges[index]-18,color)
                else:
                    f=fit_text(draw,value,22,edges[index+1]-edges[index]-18,True)
                    draw.text((center,y+18),value,font=f,fill=color,anchor='mm')
            for edge in edges[1:-1]:draw.line((edge,y+3,edge,y+33),fill='#76D7C4',width=1)
            y+=40
        y+=7
    draw.text((18,720),'Initial placement: — delta. Review both cards; Yes applies, No cancels.',font=font(17,True),fill='#eeeeee')
    output=BytesIO();image.convert('RGB').save(output,format='PNG');return output.getvalue()


def results_layout(format_size, scores, rows):
    """Competition places for teams and individuals; tied podiums share medals."""
    points=dict(parse_scores(scores,format_size))
    totals=[sum(points[r['name']] for r in rows[i:i+format_size]) for i in range(0,12,format_size)]
    all_points=list(points.values())
    groups=[]
    for index,total in enumerate(totals):
        members=sorted(rows[index*format_size:(index+1)*format_size],key=lambda r:-points[r['name']])
        groups.append({'total':total,'place':1+sum(t>total for t in totals),
                       'players':[{'name':r['name'],'score':points[r['name']],
                                   'place':1+sum(v>points[r['name']] for v in all_points)} for r in members]})
    return sorted(groups,key=lambda g:-g['total'])


def podium(draw, x, y, place, scale=1):
    """Local crown/medals avoid platform-dependent emoji font rendering."""
    def coords(points):return [(x+px*scale,y+py*scale) for px,py in points]
    if place==1:
        shape=[(-19,12),(-22,-13),(-11,-3),(0,-19),(11,-3),(22,-13),(19,12)]
        draw.polygon(coords(shape),fill='#ffe32c',outline='#222222',width=max(1,round(2*scale)))
        draw.rectangle((x-19*scale,y+9*scale,x+19*scale,y+15*scale),fill='#ffc52b',outline='#222222',width=max(1,round(2*scale)))
        for dx in (-10,0,10):draw.ellipse((x+(dx-2)*scale,y+2*scale,x+(dx+2)*scale,y+6*scale),fill='#ff4646')
    elif place in (2,3):
        ribbon='#39b9ed' if place==2 else '#41d33a'
        draw.polygon(coords([(-19,-22),(-5,-22),(7,2),(-5,6)]),fill=ribbon,outline='#222222',width=2)
        draw.polygon(coords([(19,-22),(5,-22),(-7,2),(5,6)]),fill=ribbon,outline='#222222',width=2)
        color='#d0d5db' if place==2 else '#cf8748'
        draw.ellipse((x-15*scale,y-7*scale,x+15*scale,y+23*scale),fill=color,outline='#222222',width=max(1,round(2*scale)))
        draw.text((x,y+8*scale),str(place),font=font(round(19*scale)),fill='#333333',anchor='mm')


def ordinal(place):
    return str(place)+('th' if 10<=place%100<=20 else {1:'st',2:'nd',3:'rd'}.get(place%10,'th'))


def results_date(created_at):
    """Use the stored table creation date (UTC), including after preview refresh."""
    return datetime.fromisoformat(created_at).strftime('%d %b %Y')


def render_results_local(table_id, format_size, tier, scores, rows, date_text=None):
    """Reference-style score card with dotted team bands and podium icons."""
    groups=results_layout(format_size,scores,rows)
    width,height=1180,750
    image=Image.new('RGB',(width,height),'white');draw=ImageDraw.Draw(image)
    draw.rectangle((0,0,width,55),fill='black')
    draw.text((width/2,27),date_text or '200 Lounge S9',font=font(28,bold=True),fill='white',anchor='mm')
    fmt='FFA' if format_size==1 else f'{format_size}v{format_size}'
    draw.text((1160,28),f'{tier} {fmt} | #{table_id}',font=font(17),fill='#bbbbbb',anchor='rm')
    y=55
    for index,group in enumerate(groups):
        group_height=len(group['players'])*53
        color=FORMAT_COLORS[format_size][index%2]
        draw.rectangle((0,y,width,y+group_height),fill=color)
        # Dots become denser near the edges of each team band, as in the reference.
        rgb=tuple(int(color[j:j+2],16) for j in (1,3,5))
        dot_color=tuple(round(c*.84) for c in rgb)
        for yy in range(y+6,y+group_height,12):
            distance=min(yy-y,y+group_height-yy)
            radius=4 if distance<28 else 2
            for xx in range(6+(6 if (yy-y)//12%2 else 0),width,12):
                draw.ellipse((xx-radius,yy-radius,xx+radius,yy+radius),fill=dot_color)
        center=y+group_height/2
        icon_scale=min(1.35,(group_height-8)/48)
        if group['place']<=3:podium(draw,81,center,group['place'],icon_scale)
        else:draw.text((81,center),ordinal(group['place']),font=font(25),fill='#47636a',anchor='mm')
        place_size=min(90,group_height-6)
        draw.text((225,center),str(group['place']),font=font(place_size,bold=True),fill='black',anchor='mm')
        total_size=min(94,group_height-8)
        draw.text((1005,center-8 if format_size>1 else center),str(group['total']),font=font(total_size,bold=True,italic=True),fill='black',anchor='mm')
        if format_size>1 and index+1<len(groups):
            gap=group['total']-groups[index+1]['total']
            draw.text((1080,y+group_height-17),f'+{gap}',font=font(23,bold=True),fill='black',anchor='mm')
        for member in group['players']:
            row_y=y+26
            draw.rounded_rectangle((392,y+4,855,y+48),radius=5,fill=tuple(round(c*.91) for c in rgb))
            draw_player_name(draw,(560,row_y),member['name'],29,315,'black')
            draw.text((763,row_y),str(member['score']),font=font(29,bold=True),fill='black',anchor='mm')
            if member['place']<=3:podium(draw,824,row_y-3,member['place'],.68)
            else:draw.text((824,row_y),ordinal(member['place']),font=font(18,bold=True),fill='#47636a',anchor='mm')
            y+=53
    draw.text((18,714),'12 races | Total 984 | Review scores and MMR before confirming.',font=font(19),fill='#333333')
    output=BytesIO();image.save(output,format='PNG');return output.getvalue()


LORENZI_TIMEOUT=4
LORENZI_MAX_BYTES=4*1024*1024


def lorenzi_query(format_size, scores, rows):
    """Use the original bot's query grammar and format palette."""
    groups=results_layout(format_size,scores,rows)
    lines=['-'] if format_size==1 else []
    for index,group in enumerate(groups):
        if format_size!=1:
            lines.append(f"{group['place']} {FORMAT_COLORS[format_size][index%2]}")
        lines.extend(f"{player['name']} {player['score']}" for player in group['players'])
    return '\n'.join(lines)+'\n'


def fetch_lorenzi_results(format_size, scores, rows):
    import re
    if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', r['name']) for r in rows):
        raise ValueError('Literal names require the local renderer')
    query=lorenzi_query(format_size,scores,rows)
    url='https://gb.hlorenzi.com/table.png?'+urlencode({'data':query})
    request=Request(url,headers={'User-Agent':'200-Lounge-V2/1.0','Accept':'image/png'})
    with urlopen(request,timeout=LORENZI_TIMEOUT) as response:
        if response.status!=200:raise ValueError('Lorenzi did not return HTTP 200')
        data=response.read(LORENZI_MAX_BYTES+1)
    if len(data)>LORENZI_MAX_BYTES:raise ValueError('Lorenzi image exceeds size limit')
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('Lorenzi did not return a PNG')
    with Image.open(BytesIO(data)) as image:
        if image.format!='PNG' or image.width<1 or image.height<1 or image.width>4096 or image.height>4096:
            raise ValueError('Unexpected Lorenzi image dimensions')
        image.verify()
    return data


def render_results(table_id, format_size, tier, scores, rows, date_text=None):
    """Original graphics first; safely fall back without blocking table submission."""
    try:
        return fetch_lorenzi_results(format_size,scores,rows)
    except Exception as error:
        # Do not log the query URL or player data. The fallback uses the same scores.
        logging.getLogger(__name__).warning('Lorenzi results unavailable (%s); using local results card',type(error).__name__)
        return render_results_local(table_id,format_size,tier,scores,rows,date_text)
