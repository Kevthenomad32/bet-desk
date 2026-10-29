# NBA spreads & totals calls (same math as the Bet Desk page): this-season team ratings + 2 pts home court,
# normal margin/total (SD from Optimal, else 14.3 / 18.8), blended 30% with the book's no-vig chance (Pinnacle at the same line, else Hard Rock).
#   python3 -I gl_build.py GL.json NBA_TEAMS.json WIN_START WIN_END OUT_DIR
# (Standalone app: GL.json rows are built from the SportsGameOdds consensus lines in the nbaOdds doc, in the same {a, h, s, r} format.)
import json,sys,math,re,unicodedata,datetime,zoneinfo,os

def load(f):
    try: j=json.load(open(f))
    except Exception: return {}
    return j['data'] if isinstance(j,dict) and isinstance(j.get('data'),dict) else j

W,HCA,SDS,SDT=.3,2.0,14.3,18.8
P=lambda s: datetime.datetime.fromisoformat(s.replace('Z','+00:00'))
nick=lambda s: 'Trail Blazers' if 'Blazers' in (s or '') else (s or '').split()[-1]
slug=lambda s: re.sub(r'[^a-z0-9]+','-',unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode().lower()).strip('-')
def tri(s):
    try: l,o,u=[float(x) for x in s.split(',')]; return {'l':l,'o':int(o),'u':int(u)}
    except Exception: return None
imp=lambda o: 100/(o+100) if o>0 else -o/(-o+100)
dec=lambda o: 1+o/100 if o>0 else 1+100/-o
def pov(m,sd,l):
    F=lambda x:.5*(1+math.erf((x-m)/(sd*math.sqrt(2))))
    if abs(l-round(l))<1e-9: win=1-F(l+.5); push=F(l+.5)-F(l-.5); return win/(1-push)
    return 1-F(l)
r4=lambda x: None if x is None else round(x,4)

def odds_rows(odds_doc):
    """nbaOdds doc (SportsGameOdds) -> rows in the Optimal query format the builder reads."""
    t=lambda l,a,b: '' if l is None else ','.join(str(x) for x in (l, a if a not in (None,'') else -110, b if b not in (None,'') else -110))
    out=[]
    for g in (odds_doc or {}).get('games',[]):
        sp,tot,ml=g.get('spread') or {},g.get('total') or {},g.get('ml') or {}
        r=[t(sp.get('home'),sp.get('homeOdds'),sp.get('awayOdds')),t(tot.get('line'),tot.get('overOdds'),tot.get('underOdds')),
           (','.join(['0',str(ml['home']),str(ml['away'])]) if ml.get('home') and ml.get('away') else '')]+['']*8
        out.append({'a':g['away'],'h':g['home'],'s':g.get('start'),'r':'|'.join(r)})
    return out

def build(rows,T,wa,wb,out):
    A_,B_=P(wa),P(wb); now=datetime.datetime.now(datetime.timezone.utc); tz=zoneinfo.ZoneInfo('America/New_York'); n=0
    os.makedirs(out,exist_ok=True)
    for row in rows:
        s=row.get('s')
        if not s or not (A_<=P(s)<B_): continue
        a,h=nick(row['a']),nick(row['h']); TA,TH=T.get(a),T.get(h)
        if not TA or not TH: continue
        f=(str(row.get('r',''))+'|'*11).split('|'); num=lambda x: float(x) if x not in ('',None) else None
        hs,ht,ps,pt=tri(f[0]),tri(f[1]),tri(f[3]),tri(f[4]); ss,ts=num(f[8]),num(f[10])
        pace=(TA['off']['pace']+TH['off']['pace'])/2; pa=(TA['off']['ortg']+TH['def']['drtg'])/2*pace/100; ph=(TH['off']['ortg']+TA['def']['drtg'])/2*pace/100
        sm,tm=ph-pa+HCA,pa+ph; calls={}
        for k,hr,pin,mean,sd in (('spread',hs,ps,sm,ss if ss and ss>0 else SDS),('total',ht,pt,tm,ts if ts and ts>0 else SDT)):
            if not hr: continue
            if pin and abs(pin['l']-hr['l'])>1e-9: pin=None
            src=pin or hr; pb=imp(src['o'])/(imp(src['o'])+imp(src['u']))
            pm=pov(mean,sd,-hr['l']) if k=='spread' else pov(mean,sd,hr['l'])
            pf=min(.99,max(.01,pb+W*(pm-pb))); A,B=('home','away') if k=='spread' else ('over','under')
            side=A if pf>=.5 else B; pw=pf if side==A else 1-pf; price=hr['o'] if side==A else hr['u']
            ev=pw*(dec(price)-1)-(1-pw); gap=abs(pm-pb)
            calls[k]={'line':hr['l'],'odds':hr['o'],'uodds':hr['u'],'book':'consensus','pinO':pin['o'] if pin else None,'pinU':pin['u'] if pin else None,
                      'pb':r4(pb),'pm':r4(pm),'pf':r4(pf),'side':side,'pWin':r4(pw),'price':price,'ev':r4(ev),'lean':bool(pw>=.53 and gap<=.22 and (-150<=price<=-100 or 100<=price<=130))}
        if not calls: continue
        day=P(s).astimezone(tz).strftime('%Y-%m-%d'); did=f"nba-{day}-{slug(a)}-at-{slug(h)}"
        json.dump({'league':'NBA','model':1,'date':day,'game':f'{a} @ {h}','away':a,'home':h,'gameStart':P(s).strftime('%Y-%m-%dT%H:%M:%SZ'),'createdAt':now.strftime('%Y-%m-%dT%H:%M:%SZ'),
                   'lockedBy':'task','w':W,'hca':HCA,'proj':{'sm':r4(sm),'tm':r4(tm),'pa':r4(pa),'ph':r4(ph),'gp':min(TA.get('gp') or 0,TH.get('gp') or 0)},'calls':calls,'graded':False},
                  open(os.path.join(out,did+'.json'),'w'),separators=(',',':')); n+=1
        print(did, {k:(c['side'],c['line'],c['price'],c['pWin']) for k,c in calls.items()})
    print('game-line calls:',n)
    return n

if __name__ == '__main__':
    glf,tf,wa,wb,out=sys.argv[1:6]
    rows=load(glf); rows=rows if isinstance(rows,list) else []
    build(rows,load(tf).get('teams',{}),wa,wb,out)
