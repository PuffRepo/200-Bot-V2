"""Offline rating-policy experiments, not production rating implementation.

Compare unchanged legacy math, a lower-bound clamp, and rescaled variants.
All synthetic tables have 12 players and 984 individual points.
"""
import asyncio
import json
import math
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v2.rating_preview import calculate_pre_mmr, calculate_mmr

PARAMETERS = {1:(63,19,2.1),2:(142,39,3.0000001),3:(288,59,3.1),
              4:(402,79,3.35),6:(525,99,3.5)}
VARIANTS = {'legacy': (10999,False), 'lower_clamp':(10999,True),
            'scale_13500':(13500,True), 'scale_15000':(15000,True)}

def pair_delta(fmt, own, opponent, outcome, variant):
    scale, clamp = VARIANTS[variant]
    special, other, power = PARAMETERS[fmt]
    if outcome == 0:
        magnitude = special * (abs(own-opponent)/scale)**(4/3)
        return -magnitude if own >= opponent else magnitude
    base = 1 + ((opponent-own) if outcome == 1 else (own-opponent))/scale
    if clamp:
        base = max(0,base)
    magnitude = 1 + other*base**power
    return magnitude if outcome == 1 else -magnitude

def distribute(total, count):
    quotient, remainder = divmod(total,count)
    result = [quotient+(i<remainder) for i in range(count)]
    assert all(0<=v<=180 for v in result)
    return result

def scores_for(fmt, outcome):
    if outcome=='tie': return [82]*12
    total = 100*fmt if outcome=='win' else 60*fmt
    return distribute(total,fmt)+distribute(984-total,12-fmt)

def table(fmt, scores, ratings, temporary, variant):
    effective=[temporary if r is None else r for r in ratings]
    teams=[{'offset':i,'score':sum(scores[i:i+fmt]),'mmr':sum(effective[i:i+fmt])/fmt}
           for i in range(0,12,fmt)]
    deltas={}
    complex_count=0
    for own in teams:
        value=0
        for opponent in teams:
            if own is opponent: continue
            outcome=(own['score']>opponent['score'])-(own['score']<opponent['score'])
            delta=pair_delta(fmt,own['mmr'],opponent['mmr'],outcome,variant)
            complex_count+=isinstance(delta,complex) and abs(delta.imag)>1e-9
            value+=delta
        deltas[own['offset']]=math.floor(value.real)
    applied=[max(1,r+deltas[(i//fmt)*fmt])-r if r is not None else None
             for i,r in enumerate(ratings)]
    return {'focal_delta':applied[0],'focal_raw_delta':deltas[0],
            'ranked_total_delta':sum(v for v in applied if v is not None),
            'complex_values':complex_count}

async def main():
    checks=0
    # Cross-check this independent implementation against the inherited code.
    for fmt in PARAMETERS:
        for outcome in ('win','loss','tie'):
            scores=scores_for(fmt,outcome)
            ratings=[1000+(i*977) for i in range(12)]
            teams=[[i,sum(scores[i:i+fmt]),sum(ratings[i:i+fmt])/fmt]
                   for i in range(0,12,fmt)]
            teams.sort(key=lambda t:t[1],reverse=True)
            previous=None; place=None
            for index,t in enumerate(teams,1):
                place=place if t[1]==previous else index
                previous=t[1];t.append(place)
            await calculate_mmr(teams,await calculate_pre_mmr(fmt,teams))
            actual=table(fmt,scores,ratings,5250,'legacy')
            assert actual['focal_raw_delta']==next(t[-1] for t in teams if t[0]==0)
            checks+=1
    # Mathematical properties before integer rounding or the player floor.
    ratings=[1,1000,2250,3750,5250,6000,7500,10999,12000,13500,15000,20000,30000]
    for variant in ('lower_clamp','scale_13500','scale_15000'):
        for fmt in PARAMETERS:
            for own in ratings:
                for opponent in ratings:
                    for outcome in (-1,0,1):
                        value=pair_delta(fmt,own,opponent,outcome,variant)
                        reverse=pair_delta(fmt,opponent,own,-outcome,variant)
                        assert isinstance(value,(int,float)) and math.isfinite(value)
                        assert math.isclose(value,-reverse,abs_tol=1e-8)
                        if outcome==1: assert value>=1
                        if outcome==-1: assert value<=-1
                        if outcome==0 and own==opponent: assert value==0
                        checks+=1
                wins=[pair_delta(fmt,own,o,1,variant) for o in ratings]
                losses=[pair_delta(fmt,own,o,-1,variant) for o in ratings]
                assert all(a<=b for a,b in zip(wins,wins[1:]))
                assert all(a<=b for a,b in zip(losses,losses[1:]))
                checks+=2
            # Clamp is identical wherever the old base is nonnegative.
            for own in ratings:
                for opponent in ratings:
                    if abs(own-opponent)<=10999:
                        for outcome in (-1,0,1):
                            assert math.isclose(pair_delta(fmt,own,opponent,outcome,'legacy'),
                                                pair_delta(fmt,own,opponent,outcome,'lower_clamp'),abs_tol=1e-8)
                            checks+=1
    cases=[]
    for fmt in PARAMETERS:
        for mmr in (1000,3750,6000,13500,20000):
            for lineup in ('unplaced_teammates','unplaced_opponents','only_one_ranked'):
                if lineup=='unplaced_teammates': player_ratings=[mmr]+[None]*(fmt-1)+[6000]*(12-fmt)
                elif lineup=='unplaced_opponents': player_ratings=[mmr]+[6000]*(fmt-1)+[None]*(12-fmt)
                else: player_ratings=[mmr]+[None]*11
                for outcome in ('win','loss','tie'):
                    scores=scores_for(fmt,outcome)
                    assert sum(scores)==984
                    cases.append({'format':fmt,'rating':mmr,'lineup':lineup,'outcome':outcome,
                                  'results':{variant:{str(temp):table(fmt,scores,player_ratings,temp,variant)
                                                       for temp in (2250,3750,5250)} for variant in VARIANTS}})
    # Minimal low-end floor and unbounded upper-rank examples.
    extreme=[]
    for fmt in PARAMETERS:
        scores=scores_for(fmt,'win')
        player_ratings=[30000]*fmt+[1000]*(12-fmt)
        result={v:table(fmt,scores,player_ratings,5250,v) for v in VARIANTS}
        assert result['lower_clamp']['focal_raw_delta']==12//fmt-1
        extreme.append({'format':fmt,'winner_mmr':30000,'opponent_mmr':1000,'results':result})
        for outcome in ('win','loss','tie'):
            scores=scores_for(fmt,outcome)
            all_unplaced=[table(fmt,scores,[None]*12,temp,'lower_clamp')['ranked_total_delta'] for temp in (2250,3750,5250)]
            assert all_unplaced==[0,0,0]
            checks+=1
        # Exact boundary around the formula scale; no imaginary component.
        for gap in (10998,10999,11000):
            result=pair_delta(fmt,1+gap,1,1,'lower_clamp')
            assert isinstance(result,(int,float)) and result>=1
            checks+=1
    floor=table(1,scores_for(1,'loss'),[1]+[6000]*11,5250,'lower_clamp')
    assert floor['focal_delta']==0 and floor['focal_raw_delta']<0
    output={'property_checks_passed':checks,'synthetic_tables':len(cases)*12,
            'rating_floor_example':floor,'extreme_ranked_tables':extreme,'cases':cases}
    path=Path(__file__).resolve().parents[1]/'output/rating-edge-simulation.json'
    path.write_text(json.dumps(output,indent=2))
    print('Checks:',checks,'Table calculations:',len(cases)*12)
    for fmt in (1,2,3,4,6):
        for outcome in ('win','loss'):
            c=next(c for c in cases if c['format']==fmt and c['rating']==20000 and c['lineup']=='only_one_ranked' and c['outcome']==outcome)
            print('20k',fmt,outcome,{v:{t:(r['focal_delta'],r['complex_values']) for t,r in rs.items()} for v,rs in c['results'].items()})
    print('Floor:',floor)

if __name__=='__main__':asyncio.run(main())
