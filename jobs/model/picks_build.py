# App best bets, model v6 (same model as the Bet Desk page): v5 (Hard Rock line/price, Pinnacle anchor, Optimal blend, opponent injuries) plus
# game-to-game spread fitted on 1,580 2025-26 NBA and 1,243 2025 NFL box scores, injury boosts only for NEW absences, this-season-only recent role,
# more weight on Optimal early in the season, and a shadow log of every evaluated prop for self-grading. Ranks props by WIN CHANCE (Kev's choice; EV is ignored): the model's win
# probability (role, minutes, injured teammates, rest, game script, matchup), blended with the book's no-vig price
# by the tuned trust weight, at the listed odds. Up to N overs + N unders per NBA day / NFL week, each locked
# shortly before its game with the latest line; each pick stores its model components for self-grading.
#   python3 -I picks_build.py nba|nfl ODDS.json TEAMS.json PLAYERS.json INJ.json WEIGHTS.json SLATE.json OPT.json LOCKED_DIR OUT_DIR WIN_START WIN_END [N=3]
#   Also writes OUT_DIR/shadow/<game id>.json: the model's view of EVERY prop in the due games (graded later by the page, used for tuning).
#   OPT.json = the Optimal query result (list of {a,h,s,r}); missing/empty is fine (falls back to consensus lines).
#   TEAMS = "nba" doc (NBA) or "defense" doc (NFL); PLAYERS = nbaPlayers / nflPlayers doc; WEIGHTS = "modelWeights" doc;
#   SLATE = "nbaSlate" doc (back-to-back teams) or "slate" doc. Missing files are fine (defaults are used).
# (Standalone app: OPT.json is always [], so lines and prices are SportsGameOdds consensus.)
import json,sys,re,unicodedata,datetime,os,glob,zoneinfo,math
def load(f):
    try: j=json.load(open(f))
    except Exception: return {}
    return j['data'] if isinstance(j,dict) and isinstance(j.get('data'),dict) else j

DEFAULT_W = {'nba': {'wRole': 0.6, 'wScript': 1.0, 'wMatch': 1.0, 'wModel': 0.3, 'wOpt': 0.5, 'wOpp': 1.0},
             'nfl': {'wRole': 0.5, 'wScript': 1.0, 'wMatch': 1.0, 'wModel': 0.3, 'wOpt': 0.5}}

MAX_GAP = 0.22  # model vs book win chance gap above which the model is probably missing news (role change, injury): no pick

def weights(doc, lg):
    w = dict(DEFAULT_W[lg]); w.update({k: v for k, v in ((doc or {}).get(lg) or {}).items() if k in w and isinstance(v, (int, float))}); return w

def norm(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
    s = re.sub(r'[^a-z ]', '', s.replace('-', ' '))
    return ' '.join(x for x in s.split() if x not in ('jr', 'sr', 'ii', 'iii', 'iv', 'v'))

def key2(n):
    w = n.split(); return (w[0][:3] + ' ' + w[-1]) if len(w) >= 2 else n

def player_index(plist):
    a, b = {}, {}
    for v in plist or []:
        n = norm(v.get('name')); a[n] = v; b.setdefault(key2(n), v)
    return lambda name: a.get(norm(name)) or b.get(key2(norm(name)))

OPT_MKT = {'points': 'pts', 'rebounds': 'reb', 'assists': 'ast', 'threePointersMade': 'fg3', 'pointsAssistsRebounds': 'pra',
           'passingYards': 'pass', 'rushingYards': 'rush', 'receivingYards': 'rec'}
def parse_opt(rows):
    """rows = the Optimal query result: [{a, h, s, r}] where r = 'name|prop|hrLine|hrOver|hrUnder|pinLine|pinOver|pinUnder|optMean|bestOver|bestUnder;...'.
    Returns {(norm name, market): {hl, ho, hu, pl, po, pu, m, bo, bu}}."""
    out = {}
    for ev in rows or []:
        for row in (ev.get('r') or '').split(';'):
            f = row.split('|')
            if len(f) < 11 or f[1] not in OPT_MKT: continue
            n = lambda x: None if x in ('', None) else float(x)
            out[(norm(f[0]), OPT_MKT[f[1]])] = {'hl': n(f[2]), 'ho': f[3] or None, 'hu': f[4] or None, 'pl': n(f[5]), 'po': f[6] or None, 'pu': f[7] or None,
                                               'm': n(f[8]), 'bo': f[9] or None, 'bu': f[10] or None}
    return out

def clamp(x, lo, hi): return max(lo, min(hi, x))
def num(x):
    try: return None if x in (None, '') else float(x)
    except Exception: return None

def team_lines(games, team):
    for g in games or []:
        if g.get('away') == team or g.get('home') == team:
            sh = num((g.get('spread') or {}).get('home')); tot = num((g.get('total') or {}).get('line'))
            return (None if sh is None else (sh if g['home'] == team else -sh)), tot
    return None, None

# ---------- probabilities ----------
def amer(x):
    try: return int(str(x).replace('+', ''))
    except Exception: return None
def imp(o):
    o = amer(o)
    if o is None or o == 0: return None
    return 100 / (o + 100) if o > 0 else -o / (-o + 100)
def dec(o):
    o = amer(o); return None if o is None or o == 0 else (1 + o / 100 if o > 0 else 1 + 100 / -o)

NB_R = 11.8
NFL_FIT = {'rush': (1.552, 0.748), 'rec': (5.163, 0.443)}  # sd = a*mean^b, 2025 box scores
SD_FIT = {'pts': (2.439, 0.408), 'pra': (3.757, 0.283)}
# same-player correlations between markets (2025-26 box scores); different players are ~0 (teammates <= 0.09, opponents <= 0.04)
CORR_SAME = {('pts','reb'): .25, ('pts','ast'): .23, ('pts','fg3'): .67, ('pts','pra'): .92, ('reb','ast'): .21, ('reb','fg3'): .11, ('reb','pra'): .53, ('ast','fg3'): .15, ('ast','pra'): .47, ('fg3','pra'): .60}

def p_over(market, exp, line):
    if exp is None or exp <= 0: return None
    k = math.floor(line)
    if market in ('reb', 'ast'):  # negative binomial, size fitted on 2025-26 box scores (r = 11.8)
        r = NB_R; pr = r / (r + exp); cdf = 0.0
        for i in range(0, k + 1): cdf += math.exp(math.lgamma(i + r) - math.lgamma(r) - math.lgamma(i + 1) + r * math.log(pr) + i * math.log(1 - pr))
        return clamp(1 - cdf, 0.0, 1.0)
    if market == 'fg3':  # Poisson fits threes best
        term = math.exp(-exp); cdf = term
        for i in range(1, k + 1): term *= exp / i; cdf += term
        return clamp(1 - cdf, 0.0, 1.0)
    if market in ('rush', 'rec'):  # NFL yards are right-skewed: gamma with spread fitted on 1,243 2025 player-games
        sd = NFL_FIT[market][0] * exp ** NFL_FIT[market][1]
        return clamp(gamma_sf(line, (exp / sd) ** 2, sd * sd / exp), 0.0, 1.0)
    if market == 'pass': sd = 75.0 if line < exp else 0.25 * exp  # QBs: wider below the average (bad games, early exits) than above
    elif market in SD_FIT: sd = SD_FIT[market][0] * exp ** SD_FIT[market][1]   # fitted: pts sd = 2.44*mean^0.41, pra 3.76*mean^0.28
    else: sd = max(2, exp * .35)
    return 1 - 0.5 * (1 + math.erf((line - exp) / (sd * math.sqrt(2))))

def gamma_sf(x, k, th):
    """P(X > x) for a gamma(shape k, scale th) variable (regularized upper incomplete gamma)."""
    if x <= 0: return 1.0
    a = k; z = x / th; lg = -z + a * math.log(z) - math.lgamma(a)
    if z < a + 1:
        s = t = 1 / a; n = a
        for _ in range(1000):
            n += 1; t *= z / n; s += t
            if t < s * 1e-12: break
        return 1 - s * math.exp(lg)
    b = z + 1 - a; c = 1e300; d = 1 / b; h = d
    for i in range(1, 1000):
        an = -i * (i - a); b += 2; d = an * d + b; d = 1 / d if abs(d) > 1e-300 else 1e300; c = b + an / c; c = c if abs(c) > 1e-300 else 1e-300
        dl = d * c; h *= dl
        if abs(dl - 1) < 1e-12: break
    return math.exp(lg) * h

def book_over(odds, uodds):
    po, pu = imp(odds), imp(uodds)
    if po is not None and pu is not None: return po / (po + pu)
    if po is not None: return clamp(po / 1.045, .02, .98)
    if pu is not None: return clamp(1 - pu / 1.045, .02, .98)
    return 0.5

def apply_opt(p, o):
    """Use the Hard Rock (FL) line and prices when Optimal has them; keep the Pinnacle price as the sharp anchor."""
    if not o or o.get('hl') is None or not o.get('ho') or not o.get('hu'): return p
    q = dict(p, line=o['hl'], odds=o['ho'], uodds=o['hu'], book='Hard Rock (FL)')
    if o.get('pl') is not None and abs(o['pl'] - o['hl']) < 1e-9 and o.get('po') and o.get('pu'): q['pinO'], q['pinU'] = o['po'], o['pu']
    q['bestO'], q['bestU'] = o.get('bo'), o.get('bu')
    return q

def evaluate(p, comps_exp, W):
    """Win probability and EV for both sides. comps_exp = projected mean. The 'book' chance is Pinnacle's no-vig price at this line when available."""
    line = p['line']; pm = p_over(p['market'], comps_exp, line)
    if pm is None: return None
    pb = book_over(p['pinO'], p['pinU']) if p.get('pinO') and p.get('pinU') else book_over(p.get('odds'), p.get('uodds'))
    pf = clamp(pb + W['wModel'] * (pm - pb), .01, .99)
    # a missing price is estimated from the book's fair chance plus a normal ~4.5% margin (never assume -110 on a lopsided line)
    do = dec(p.get('odds')) or 1 / (pb * 1.045); du = dec(p.get('uodds')) or 1 / ((1 - pb) * 1.045)
    ev_o = pf * (do - 1) - (1 - pf); ev_u = (1 - pf) * (du - 1) - pf
    side = 'over' if pf >= 0.5 else 'under'  # the side more likely to hit
    ds = do if side == 'over' else du
    price = (p.get('odds') if side == 'over' else p.get('uodds')) or ('%+d' % round((ds - 1) * 100) if ds >= 2 else '%d' % round(-100 / (ds - 1)))
    return {'pModel': pm, 'pBook': pb, 'pOver': pf, 'side': side, 'ev': ev_o if side == 'over' else ev_u, 'pWin': pf if side == 'over' else 1 - pf, 'price': str(price)}

# ---------- NBA ----------
NBA_PARTS = {'pts': ['pts'], 'reb': ['reb'], 'ast': ['ast'], 'fg3': ['fg3'], 'pra': ['pts', 'reb', 'ast']}
STAT_EXP = {'pts': ('iF', 1.0, 0.0), 'ast': ('iF', 0.8, 0.2), 'fg3': ('iF', 0.5, 0.5), 'reb': ('pace', 0.4, 0.6)}

def nba_context(p, P, teams, games, opp, inj, pool, b2b, opt=None, plist=None):
    """Everything about this prop that doesn't depend on the tunable weights; stored with each pick as 'comps'."""
    T = teams; names = list(T); D = T[opp]['def']
    avgD = {k: sum(T[n]['def'][k] for n in names) / len(names) for k in ('pts', 'reb', 'ast', 'fg3')}
    lgPts = sum(T[n]['off']['pts'] for n in names) / len(names)
    tm = T.get(p['team'], {}).get('off') or {'pts': lgPts, 'reb': 44, 'ast': 26, 'fg3': 13}
    sp, tot = team_lines(games, p['team'])
    why = []
    smin = num(P.get('min')) if P else None; rmin = num(P.get('min5')) if P else None; g5 = num(P.get('gp5')) if P else None
    rec = bool(P and rmin and rmin > 0 and smin and smin > 0 and (g5 or 0) >= 3)
    # minutes adjustments (weight-independent): blowout risk, back-to-back, injured teammates
    mref = (0.4 * smin + 0.6 * rmin) if rec else smin
    mAdj = 1.0
    if mref:
        if sp is not None:
            blow = clamp(abs(sp) - 6, 0, 10)
            if blow > 0 and mref >= 26: mAdj *= 1 - 0.01 * blow; why.append(f'blowout risk −{round(blow)}%')
            elif blow > 0 and mref < 20: mAdj *= 1 + 0.008 * blow
        if p['team'] in (b2b or []) and mref >= 28: mAdj *= 0.97; why.append('back-to-back')
    # injured teammates
    def fresh(q, team, recent):
        # share of the team's games this season the injured player missed; those games are already in the recent numbers
        gpc = num(q.get('gpc')); tg = num((T.get(team) or {}).get('gp'))
        if not recent or gpc is None or not tg: return 1.0
        return 1 - 0.6 * clamp(1 - gpc / tg, 0, 1)
    me = norm(p['player']); look = player_index(pool); outs = []; Mout = 0.0; lost = {'pts': 0.0, 'reb': 0.0, 'ast': 0.0, 'fg3': 0.0}
    for x in (inj or {}).get('players', []):
        if x.get('team') != p['team'] or norm(x.get('name')) == me: continue
        st = x.get('status') or ''
        wt = 1.0 if re.match(r'(?i)out', st) else 0.75 if re.match(r'(?i)doubt', st) else 0.0
        q = look(x.get('name')) if wt else None
        if not q or not num(q.get('min')) or num(q['min']) < 12: continue
        wt *= fresh(q, p['team'], rec)  # an absence the recent games already reflect counts less
        Mout += wt * num(q['min']); outs.append(x['name'].split()[-1])
        for k in lost: lost[k] += wt * (num(q.get(k)) or 0)
    uAdj = {k: 1.0 for k in lost}
    if Mout > 0 and mref and mref >= 10:
        mAdj *= 1 + 0.8 * Mout / 240
        for k in lost: uAdj[k] = 1 + clamp(0.35 * lost[k] / max(tm.get(k) or 1, 1), 0, 0.15)
        why.append(f"{', '.join(outs[:3])} out: +{(0.8 * Mout / 240) * 100:.0f}% min, +{(uAdj['pts'] - 1) * 100:.0f}% usage")
    # opponent's injuries: a thinner rotation defends worse (small, since the spread/total already move on injury news),
    # and missing rebounders leave more boards. Scaled by the tunable wOpp weight in project().
    look2 = player_index(plist); oOuts = []; oMout = 0.0; oLost = {'pts': 0.0, 'reb': 0.0, 'ast': 0.0, 'fg3': 0.0}
    for x in (inj or {}).get('players', []):
        if x.get('team') != opp: continue
        st = x.get('status') or ''
        wt = 1.0 if re.match(r'(?i)out', st) else 0.75 if re.match(r'(?i)doubt', st) else 0.0
        q = (look(x.get('name')) or look2(x.get('name'))) if wt else None
        if not q or not num(q.get('min')) or num(q['min']) < 12: continue
        wt *= fresh(q, opp, True)
        oMout += wt * num(q['min']); oOuts.append(x['name'].split()[-1])
        for k in oLost: oLost[k] += wt * (num(q.get(k)) or 0)
    oAdj = {k: 1.0 for k in oLost}
    if oMout > 0:
        g = clamp(0.12 * oMout / 240, 0, 0.04); oo = T[opp].get('off') or {}
        oAdj.update(pts=1 + g, ast=1 + g, fg3=1 + g, reb=1 + clamp(0.3 * oLost['reb'] / max(oo.get('reb') or 44, 1), 0, 0.08))
        why.append(f"{opp} missing {', '.join(oOuts[:3])}: +{g * 100:.0f}% scoring, +{(oAdj['reb'] - 1) * 100:.0f}% reb")
    iF = pace = None
    if sp is not None and tot is not None:
        implied = tot / 2 - sp / 2
        iF = clamp(implied / tm['pts'], 0.85, 1.15); pace = clamp(tot / (2 * lgPts), 0.9, 1.1)
        why.append(f'team total {implied:.0f}')
    parts = []
    for k in NBA_PARTS[p['market']]:
        s = num(P.get(k)) if P else None
        if s is None and p['market'] == k: s = num(p.get('avg'))
        if s is None: return None
        r5 = num(P.get(k + '5')) if P else None
        sf_name, a, b = STAT_EXP[k]
        parts.append({'k': k, 's': s, 'rs': (s / smin) if smin else None, 'rr': (r5 / rmin) if (rec and r5 is not None) else None,
                      'ms': smin, 'mr': rmin if rec else None, 'mAdj': mAdj, 'uAdj': uAdj[k], 'oAdj': oAdj[k],
                      'sf': (iF if sf_name == 'iF' else pace), 'dF': D[k] / avgD[k], 'a': a, 'b': b})
    if rec:
        tag = 'role up' if rmin > smin * 1.08 else 'role down' if rmin < smin * 0.92 else None
        if tag: why.insert(0, f'{tag}: {rmin:.0f} min last 5 vs {smin:.0f}')
    if opt is not None: why.append(f'Optimal proj {opt:.1f}')
    early = bool(P) and (num(P.get('gp5')) or 0) < 3  # fewer than 3 games this season: trust Optimal more
    if early and opt is not None: why.append('early season: leaning on Optimal')
    return {'lg': 'nba', 'parts': parts, 'why': why, 'opt': opt, 'optW': 0.75 if early else None}

def nfl_context(p, P, defense, games, opp, opt=None):
    m = p['market']; D = defense[opp]; names = list(defense); key = 'rush' if m == 'rush' else 'pass'
    lg = sum(defense[n].get(key) or 0 for n in names) / len(names); lgPts = sum(defense[n].get('pts') or 0 for n in names) / len(names)
    s = num(P.get(m)) if P else None
    if s is None: s = num(p.get('avg'))
    if s is None: return None
    r = num(P.get(m + '3')) if P else None; g3 = num(P.get('g3')) if P else None
    if not (r is not None and (g3 or 0) >= 2): r = None
    why = []
    if r is not None:
        if r > s * 1.15: why.append(f'role up: {r:.0f} last 3 vs {s:.0f}')
        elif r < s * 0.85: why.append(f'role down: {r:.0f} last 3 vs {s:.0f}')
    sp, tot = team_lines(games, p['team']); sf = None
    if sp is not None and tot is not None:
        implied = tot / 2 - sp / 2; env = clamp(implied / lgPts, 0.8, 1.25) ** 0.5
        script = clamp(1 - 0.015 * sp, .85, 1.15) if m == 'rush' else clamp(1 + 0.012 * sp, .88, 1.12)
        sf = env * script
        if abs(sp) >= 3: why.append(f"{'favored' if sp < 0 else 'underdog'} by {abs(sp):g}: {'run' if sp < 0 else 'pass'}-heavy script")
        why.append(f'team total {implied:.0f}')
    if opt is not None: why.append(f'Optimal proj {opt:.1f}')
    return {'lg': 'nfl', 'parts': [{'k': m, 's': s, 'r': r, 'sf': sf, 'dF': D[key] / lg, 'a': 1.0, 'b': 0.5}], 'why': why, 'opt': opt}

def project(ctx, W):
    """Projected mean from stored components and weights."""
    exp = 0.0
    for c in ctx['parts']:
        if ctx['lg'] == 'nba':
            if c['rs'] is not None and c['ms']:
                rate = c['rs'] if c['rr'] is None else (1 - W['wRole']) * c['rs'] + W['wRole'] * c['rr']
                M = (c['ms'] if c['mr'] is None else (1 - W['wRole']) * c['ms'] + W['wRole'] * c['mr']) * c['mAdj']
                base = rate * min(M, 42) * c['uAdj']
            else: base = c['s'] * c['uAdj']
            base *= c.get('oAdj', 1.0) ** W.get('wOpp', 1.0)  # opponent injuries (older picks have none)
        else:
            base = c['s'] if c['r'] is None else (1 - W['wRole']) * c['s'] + W['wRole'] * c['r']
        if c['sf'] is not None: f = c['dF'] ** (c['b'] * W['wMatch']) * c['sf'] ** (c['a'] * W['wScript'])
        else: f = c['dF'] ** ((0.5 if ctx['lg'] == 'nfl' else 1.0) * W['wMatch'])  # no game lines yet: matchup only
        exp += base * f
    if ctx.get('opt') is not None:
        wo = max(W.get('wOpt', 0.5), ctx.get('optW') or 0)
        exp = (1 - wo) * exp + wo * ctx['opt']
    return exp

def log_lik(picks, W):
    """Sum of log-likelihoods of graded picks (won/lost) under weights W; picks need comps, line, market, side, pBook."""
    ll = 0.0; n = 0
    for x in picks:
        if x.get('status') not in ('won', 'lost') or not x.get('comps'): continue
        e = project(x['comps'], W); pm = p_over(x['market'], e, x['line'])
        if pm is None: continue
        pb = x.get('pBook', 0.5); pf = clamp(pb + W['wModel'] * (pm - pb), .01, .99)
        ps = pf if x['side'] == 'over' else 1 - pf
        ll += math.log(ps if x['status'] == 'won' else 1 - ps); n += 1
    return ll, n

if __name__ == '__main__':
    lg,oddf,teamf,plf,injf,wf,slf,optf,lockd,outf,wa,wb=sys.argv[1:13]; N=int(sys.argv[13]) if len(sys.argv)>13 else 3
    try: OPT=parse_opt(json.load(open(optf)))
    except Exception: OPT={}
    odds,teams,inj=load(oddf),load(teamf).get('teams',{}),load(injf); pdoc=load(plf); look=player_index(pdoc.get('players',[]))
    W=weights(load(wf),lg); b2b=load(slf).get('b2b',[]) if lg=='nba' else []; pool=pdoc.get('pool',[])
    tz=zoneinfo.ZoneInfo('America/New_York'); now=datetime.datetime.now(datetime.timezone.utc); et=now.astimezone(tz)
    P=lambda s: datetime.datetime.fromisoformat(s.replace('Z','+00:00'))
    WA,WB=P(wa),P(wb)
    day=et.strftime('%Y-%m-%d')
    pstart=day if lg=='nba' else (et-datetime.timedelta(days=(et.weekday()-1)%7)).strftime('%Y-%m-%d')
    locked=[load(f) for f in glob.glob(os.path.join(lockd,'**','*.json'),recursive=True)] if os.path.isdir(lockd) else []
    locked=[x for x in locked if x.get('league')==lg.upper() and x.get('date','')>=pstart and x.get('source')!='asked']  # picks Kev asked for don't use the app's slots
    used={'over':sum(x.get('side')=='over' for x in locked),'under':sum(x.get('side')=='under' for x in locked)}
    usedp={norm(x.get('player')) for x in locked}
    out_set={norm(p['name']) for p in inj.get('players',[]) if re.match(r'(?i)out',p.get('status',''))}
    games=odds.get('games',[]); opp={}; start={}
    for g in games:
        opp[g['away']]=g['home']; opp[g['home']]=g['away']; start[g['away']]=start[g['home']]=g.get('start')
    MIN={'pts':8.5,'reb':3.5,'ast':2.5,'fg3':1.5,'pra':15.5,'pass':150,'rush':25,'rec':20}
    def placeholder(p):
        return str(p.get('odds')).replace('+','')=='100' and str(p.get('uodds')).replace('+','')=='100'
    def mainodds(x):
        v=amer(x)
        return v is not None and (-150<=v<=-100 or 100<=v<=130)
    rows=[]; shadow={}
    for p in odds.get('props',[]):
        o_=OPT.get((norm(p['player']),p['market'])); p=apply_opt(p,o_)
        if p.get('book')!='Hard Rock (FL)' and placeholder(p): continue  # +100/+100 = the feed has no real price
        a,l=p.get('avg'),p.get('line')
        if a is None or l is None or a<=0 or not (0.5*a<=l<=2*a): continue
        if l < MIN.get(p['market'],0) or not mainodds(p.get('odds')) or (p.get('uodds') not in (None,'') and not mainodds(p.get('uodds'))): continue
        n=norm(p['player'])
        if n in out_set: continue
        o=opp.get(p['team']); st=start.get(p['team'])
        if not o or o not in teams or not st or P(st)<=now: continue
        om=(o_ or {}).get('m')
        ctx=nba_context(p,look(p['player']),teams,games,o,inj,pool,b2b,om,pdoc.get('players',[])) if lg=='nba' else nfl_context(p,look(p['player']),teams,games,o,om)
        if not ctx: continue
        exp=project(ctx,W); e=evaluate(p,exp,W)
        if e and WA<=P(st)<WB:  # shadow log: every evaluated prop in the due games, kept for grading/tuning
            g=[x for x in games if p['team'] in (x['away'],x['home'])][0]
            sid=f"{lg}-{day}-"+re.sub(r'[^a-z0-9]+','-',norm(g['away'])+'-at-'+norm(g['home'])).strip('-')
            sh=shadow.setdefault(sid,{'date':day,'league':lg.upper(),'game':f"{g['away']} @ {g['home']}",'gameStart':st,'model':6,'createdAt':now.strftime('%Y-%m-%dT%H:%M:%SZ'),'graded':False,'rows':[]})
            sh['rows'].append({'player':p['player'],'team':p['team'],'market':p['market'],'line':p['line'],'odds':p.get('odds'),'uodds':p.get('uodds'),'book':p.get('book','consensus'),
                'pBook':round(e['pBook'],4),'pModel':round(e['pModel'],4),'pOver':round(e['pOver'],4),'exp':round(exp,2),'comps':ctx})
        if n in usedp: continue  # already has a locked pick today/this week
        if not e or e['pWin']<0.55 or abs(e['pModel']-e['pBook'])>MAX_GAP: continue  # ranked by win chance; 55% minimum
        rows.append(dict(p,opp=o,exp=round(exp,1),edge=round(exp-l,1),rel=round((exp-l)/max(exp,0.5 if lg=='nba' else 1),3),gameStart=st,why='; '.join(ctx['why']),ctx=ctx,**e))
    def top(side,k):
        seen=set(); out=[]
        if k<=0: return out
        for r in sorted([r for r in rows if r['side']==side],key=lambda r:(-r['pWin'],-r['ev'])):
            n=norm(r['player'])
            if n in seen: continue
            seen.add(n); out.append(r)
            if len(out)>=k: break
        return out
    overs=top('over',max(0,N-used['over']))
    unders=[u for u in top('under',max(0,N-used['under'])+len(overs)) if norm(u['player']) not in {norm(o['player']) for o in overs}][:max(0,N-used['under'])]
    picks=[]
    for r in overs+unders:
        if not (WA<=P(r['gameStart'])<WB): continue
        slug=re.sub(r'[^a-z0-9]+','-',norm(r['player'])).strip('-')
        picks.append({'id':f"{lg}-{day}-{slug}-{r['market']}-{r['side']}",'data':{'date':day,'league':lg.upper(),'player':r['player'],'team':r['team'],'opp':r['opp'],'market':r['market'],'side':r['side'],
          'line':r['line'],'odds':r['price'],'avg':r['avg'],'exp':r['exp'],'edge':r['edge'],'rel':r['rel'],
          'book':r.get('book','consensus'),'bestOver':r.get('bestO'),'bestUnder':r.get('bestU'),'pWin':round(r['pWin'],3),'pModel':round(r['pModel'],3),'pBook':round(r['pBook'],3),'ev':round(r['ev'],3),'why':r['why'],'model':6,'rank':'win','weights':W,'comps':r['ctx'],
          'gameStart':r['gameStart'],'status':'open','createdAt':now.strftime('%Y-%m-%dT%H:%M:%SZ')}})
    os.makedirs(outf,exist_ok=True)
    for x in picks: json.dump(x['data'],open(os.path.join(outf,x['id']+'.json'),'w'),ensure_ascii=False)
    os.makedirs(os.path.join(outf,'shadow'),exist_ok=True)
    for sid,sh in shadow.items(): json.dump(sh,open(os.path.join(outf,'shadow',sid+'.json'),'w'),ensure_ascii=False,separators=(',',':'))
    print(f"shadow log: {sum(len(v['rows']) for v in shadow.values())} props across {len(shadow)} games -> {os.path.join(outf,'shadow')}")
    print(f"{lg}: weights {W}; slots used {used['over']} overs / {used['under']} unders this {'day' if lg=='nba' else 'week'}; locking {len(picks)} now")
    for x in picks: d=x['data']; print(' ',x['id'], d['side'], d['line'], d['odds'], f"exp {d['exp']} win {d['pWin']:.0%} (book {d['pBook'] if d['side']=='over' else 1-d['pBook']:.0%}) EV {d['ev']:+.1%}", '|', d['why'])
