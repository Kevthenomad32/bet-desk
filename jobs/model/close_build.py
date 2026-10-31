# Closing lines for the Bet Desk.
#   python3 -I close_build.py EVENTS.json [EVENTS2.json ...] --picks PICKS_DIR --out OUT_DIR --since START_UTC_ISO
# Reads finished SportsGameOdds events (fetched with includeOpposingOdds=true), writes:
#   OUT_DIR/closing.json        every closing prop/total/spread line: {league, game, player, market, close, closeOdds, closeUOdds, open, result}
#   OUT_DIR/updates/<id>.json   CLV fields for each pick in PICKS_DIR that matches a closing line
import json,sys,os,re,glob,unicodedata
MK={'points':'pts','rebounds':'reb','assists':'ast','threePointersMade':'fg3','points+rebounds+assists':'pra','passing_yards':'pass','rushing_yards':'rush','receiving_yards':'rec'}
def norm(s):
    s=unicodedata.normalize('NFKD',s or '').encode('ascii','ignore').decode().lower()
    s=re.sub(r'[^a-z ]','',s.replace('-',' '))
    return ' '.join(x for x in s.split() if x not in ('jr','sr','ii','iii','iv','v'))
num=lambda x: float(x) if x not in (None,'') else None
def imp(o):
    try: o=int(str(o).replace('+',''))
    except Exception: return None
    return 100/(o+100) if o>0 else -o/(-o+100)

def _pname(p,pid):  # SportsGameOdds sometimes leaves out 'name': first/last name, else the id (AARON_WIGGINS_1_NBA -> Aaron Wiggins)
    if p.get('name'): return p['name']
    fl=' '.join(x for x in (p.get('firstName'),p.get('lastName')) if x)
    if fl: return fl
    w=str(pid).split('_')
    while w and (w[-1].isdigit() or w[-1] in ('NBA','NFL','NCAAF','WNBA')): w.pop()
    return ' '.join(x.capitalize() if len(x)>2 else x.upper() for x in w)


def closing_lines(events_by_file):
    closing=[]
    for evs in events_by_file:
        for e in evs:
            if not e['status'].get('started'): continue
            lg=e.get('leagueID','').lower(); T={s:((e['teams'][s].get('names') or {}).get('medium') or (e['teams'][s].get('names') or {}).get('long') or e['teams'][s].get('teamID','')) for s in ('home','away')}; game=f"{T['away']} @ {T['home']}"
            O=e['odds']
            for k,o in O.items():
                if o.get('betTypeID')!='ou' or o.get('sideID')!='over' or o.get('periodID')!='game': continue
                pid=o.get('playerID'); m=MK.get(o.get('statID')) if pid else ('total' if o.get('statEntityID')=='all' and o.get('statID')=='points' else None)
                if not m or o.get('closeBookOverUnder') in (None,''): continue
                u=O.get(o.get('opposingOddID',''),{})
                closing.append({'league':lg.upper(),'game':game,'player':_pname(e['players'].get(pid,{}),pid) if pid else None,'market':m,
                  'close':num(o['closeBookOverUnder']),'closeOdds':o.get('closeBookOdds'),'closeUOdds':u.get('closeBookOdds'),'open':num(o.get('openBookOverUnder')),'result':o.get('score')})
            sp=O.get('points-home-game-sp-home',{})
            if sp.get('closeBookSpread') not in (None,''): closing.append({'league':lg.upper(),'game':game,'player':None,'market':'spread_home','close':num(sp['closeBookSpread']),'closeOdds':sp.get('closeBookOdds'),'open':num(sp.get('openBookSpread'))})
    return closing

def pick_updates(closing, picks, since=None):
    """{pick id: {closeLine, closeOdds, clv[, clvProb]}} for picks (list of dicts with _id) that match a closing line."""
    idx={(c['league'],norm(c['player']).replace(' ',''),c['market']):c for c in closing if c['player']}  # spaces ignored: names rebuilt from ids lose hyphens
    out={}
    for p in picks:
        if p.get('closeLine') is not None: continue
        if since and (p.get('gameStart') or '')[:19] < since[:19]: continue
        c=idx.get((p.get('league'),norm(p.get('player')).replace(' ',''),p.get('market')))
        if not c: continue
        side=p.get('side'); line=p.get('line'); close=c['close']
        clv=round((close-line) if side=='over' else (line-close),1)
        co=c['closeOdds'] if side=='over' else c.get('closeUOdds')
        clvProb=None
        if close==line and imp(p.get('odds')) is not None and imp(co) is not None: clvProb=round((imp(co)-imp(p.get('odds')))*100,1)  # price moved our way = positive
        upd={'closeLine':close,'closeOdds':co,'clv':clv}
        if clvProb is not None: upd['clvProb']=clvProb
        out[p['_id']]=upd
    return out
