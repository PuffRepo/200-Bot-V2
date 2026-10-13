"""Extensible Unicode font fallback for literal player-name rendering."""
from functools import lru_cache
from pathlib import Path
import unicodedata
from fontTools.ttLib import TTFont
from PIL import ImageFont

ROOT = Path(__file__).resolve().parents[1]

@lru_cache(maxsize=1)
def font_catalog():
    preferred = ['NotoSans.ttf', 'NotoSansCJKjp-Regular.otf', 'NotoSansArabic.ttf',
                 'NotoSansDevanagari.ttf', 'NotoSansThai.ttf', 'NotoSansSymbols2.ttf', 'NotoEmoji.ttf']
    paths = [ROOT/'fonts'/name for name in preferred]
    paths += sorted((ROOT/'fonts').glob('*.ttf')) + sorted((ROOT/'fonts').glob('*.otf'))
    catalog=[]
    for path in dict.fromkeys(paths):
        if path.is_file():
            with TTFont(path) as face:
                catalog.append((str(path), frozenset(face.getBestCmap() or {})))
    if not catalog:
        raise ValueError('Player-name fonts are missing; extract the fonts folder from the update.')
    return tuple(catalog)

@lru_cache(maxsize=256)
def face(path, size):
    return ImageFont.truetype(path, size)


def required_points(value):
    # Joiners and variation selectors modify adjacent glyphs; no standalone glyph needed.
    return {ord(c) for c in value if ord(c) not in (0x200c, 0x200d) and not 0xfe00 <= ord(c) <= 0xfe0f}


def name_runs(value):
    catalog=font_catalog()
    needed=required_points(value)
    for path, coverage in catalog:
        if needed <= coverage:
            return [(value, path)]
    clusters=[]
    for char in value:
        if clusters and (unicodedata.combining(char) or char in '\u200c\u200d' or '\ufe00' <= char <= '\ufe0f' or clusters[-1].endswith('\u200d')):
            clusters[-1]+=char
        else:
            clusters.append(char)
    runs=[]
    for cluster in clusters:
        points=required_points(cluster)
        path=next((p for p,c in catalog if points <= c),catalog[0][0])
        if runs and runs[-1][1]==path:
            runs[-1]=(runs[-1][0]+cluster,path)
        else:
            runs.append((cluster,path))
    return runs


def draw_player_name(draw, xy, value, size, available, fill):
    runs=name_runs(value)
    def width(n):return sum(draw.textlength(text,font=face(path,n)) for text,path in runs)
    while size>7 and width(size)>available:
        size-=1
    x,y=xy
    x-=width(size)/2
    for text,path in runs:
        f=face(path,size)
        draw.text((x,y),text,font=f,fill=fill,anchor='lm')
        x+=draw.textlength(text,font=f)
