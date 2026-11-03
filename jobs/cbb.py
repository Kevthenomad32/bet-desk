"""Men's college basketball: game archive (ESPN scores), team ratings, and spread/total calls against Hard Rock with
Pinnacle as the anchor (TheRundown).

Ratings: points model  pts(team) = mu + O(team) + D(opponent) + h/2 at home (0 on a neutral court), fitted by ridge least
squares on this season's games, each team pulled toward a prior (last season's rating, regressed 40% to average) with the
weight of LAMBDA games. Early in the season the prior carries most of the weight; by January the games do.
Calls: normal margin/total around the projection (SD fitted on last season), blended W with Pinnacle's no-vig chance at
Hard Rock's line (the market is sharp; the model only nudges it). A 'lean' needs pWin >= 53% and the model within 22% of
the market, at a normal price."""
import datetime as dt
import difflib
import math
import re

from . import espn
from .common import ET, iso, norm, now_et, now_utc, parse_iso

LAMBDA = 6.0        # prior strength, in games
REGRESS = 0.6       # keep 60% of last season's rating
W = 0.25            # how far the model may move the market's chance
SD_M, SD_T = 10.8, 13.5
YMD = lambda d: d.strftime('%Y%m%d')


def season_of(d):
    """ESPN numbers college seasons by the ending year: Nov 2026 -> 2027."""
    return d.year + 1 if d.month >= 8 else d.year


def season_start(season):
    return dt.date(season - 1, 11, 1)


# ---------- archive (scores only: the ratings need nothing else) ----------
def archive(db, start, end):
    n, d = 0, start
    while d <= end:
        try:
            sb = espn.scoreboard('cbb', YMD(d), groups=50, limit=500)
        except RuntimeError as e:
            print(f'::warning title=ESPN cbb {YMD(d)}::{e}')
            sb = []
        rows = [{'league': 'cbb', 'id': g['id'], 'date': g['date'], 'start_at': g['start'], 'season': season_of(d), 'season_type': g.get('season_type'),
                 'week': None, 'status': 'final', 'home': g['home'], 'away': g['away'], 'home_score': g['home_score'], 'away_score': g['away_score'],
                 'box': {'neutral': g.get('neutral'), 'home_id': g.get('home_id'), 'away_id': g.get('away_id'),
                         'home_mascot': g.get('home_mascot'), 'away_mascot': g.get('away_mascot')}}
                for g in sb if g['state'] == 'final' and g['home_score'] is not None and g['away_score'] is not None]
        if rows:
            db.upsert('games', rows, 'league,id'); n += len(rows)
        d += dt.timedelta(days=1)
    return n


def season_games(db, season):
    return db.select('games', league='eq.cbb', season=f'eq.{season}', status='eq.final', select='id,date,home,away,home_score,away_score,box')


# ---------- ratings ----------
def fit(games, prior=None, lam=LAMBDA, iters=80):
    """{mu, h, teams: {espn id: {name, mascot, O, D, n}}}."""
    prior = prior or {}
    G = []
    names = {}
    for g in games:
        b = g.get('box') or {}
        hi, ai = str(b.get('home_id') or g['home']), str(b.get('away_id') or g['away'])
        names[hi] = (g['home'], b.get('home_mascot')); names[ai] = (g['away'], b.get('away_mascot'))
        G.append((hi, ai, float(g['home_score']), float(g['away_score']), 0.0 if b.get('neutral') else 1.0))
    if not G:
        return {'mu': 72.0, 'h': 3.0, 'teams': {}}
    O = {t: (prior.get(t) or {}).get('O', 0.0) for t in names}
    D = {t: (prior.get(t) or {}).get('D', 0.0) for t in names}
    mu = sum(p for g in G for p in g[2:4]) / (2 * len(G)); h = 3.0
    off = {t: [] for t in names}; dfn = {t: [] for t in names}
    for k, (hi, ai, ph, pa, home) in enumerate(G):
        off[hi].append((k, 'h')); off[ai].append((k, 'a')); dfn[ai].append((k, 'h')); dfn[hi].append((k, 'a'))
    for _ in range(iters):
        hg = [(ph - pa) - (O[hi] + D[ai] - O[ai] - D[hi]) for hi, ai, ph, pa, home in G if home]
        h = sum(hg) / len(hg) if hg else 3.0
        mu = sum((ph - O[hi] - D[ai] - home * h / 2) + (pa - O[ai] - D[hi] + home * h / 2) for hi, ai, ph, pa, home in G) / (2 * len(G))
        for t in names:
            pO, pD = (prior.get(t) or {}).get('O', 0.0), (prior.get(t) or {}).get('D', 0.0)
            s = 0.0
            for k, side in off[t]:
                hi, ai, ph, pa, home = G[k]
                s += (ph - mu - D[ai] - home * h / 2) if side == 'h' else (pa - mu - D[hi] + home * h / 2)
            O[t] = (s + lam * pO) / (len(off[t]) + lam)
            s = 0.0
            for k, side in dfn[t]:
                hi, ai, ph, pa, home = G[k]
                s += (ph - mu - O[hi] - home * h / 2) if side == 'h' else (pa - mu - O[ai] + home * h / 2)
            D[t] = (s + lam * pD) / (len(dfn[t]) + lam)
        # keep the average team at zero (mu carries the level)
        mo = sum(O.values()) / len(O); md = sum(D.values()) / len(D)
        O = {t: v - mo for t, v in O.items()}; D = {t: v - md for t, v in D.items()}; mu += mo + md
    return {'mu': round(mu, 3), 'h': round(h, 3),
            'teams': {t: {'name': names[t][0], 'mascot': names[t][1], 'O': round(O[t], 3), 'D': round(D[t], 3), 'n': len(off[t])} for t in names}}


def residual_sd(fitdoc, games):
    """Spread and total SD of the fit's errors (in-sample, inflated 10% for out-of-sample)."""
    T, mu, h = fitdoc['teams'], fitdoc['mu'], fitdoc['h']
    em, et = [], []
    for g in games:
        b = g.get('box') or {}
        hi, ai = str(b.get('home_id') or g['home']), str(b.get('away_id') or g['away'])
        if hi not in T or ai not in T:
            continue
        ph, pa = project(fitdoc, hi, ai, b.get('neutral'))
        em.append((g['home_score'] - g['away_score']) - (ph - pa)); et.append((g['home_score'] + g['away_score']) - (ph + pa))
    sd = lambda xs: math.sqrt(sum(x * x for x in xs) / len(xs)) * 1.1 if len(xs) > 200 else None
    return sd(em) or SD_M, sd(et) or SD_T


def project(R, home_id, away_id, neutral=False):
    T = R['teams']; H, A = T[home_id], T[away_id]
    hh = 0 if neutral else R['h'] / 2
    return R['mu'] + H['O'] + A['D'] + hh, R['mu'] + A['O'] + H['D'] - hh


def ratings(db, today=None):
    """Fit this season with last season as the prior; returns the cbbTeams doc."""
    today = today or now_et().date()
    s = season_of(today)
    prev = fit(season_games(db, s - 1), lam=1.0)
    prior = {t: {'O': REGRESS * v['O'], 'D': REGRESS * v['D']} for t, v in prev['teams'].items()}
    cur_games = season_games(db, s)
    cur = fit(cur_games, prior=prior)
    # teams with no game yet this season still get their prior
    for t, v in prev['teams'].items():
        cur['teams'].setdefault(t, {'name': v['name'], 'mascot': v['mascot'], 'O': round(REGRESS * v['O'], 3), 'D': round(REGRESS * v['D'], 3), 'n': 0})
    if not cur_games:
        cur['mu'], cur['h'] = prev['mu'], prev['h']
    sdm, sdt = residual_sd(prev, season_games(db, s - 1)) if prev['teams'] else (SD_M, SD_T)
    return {'updated': iso(now_utc()), 'season': s, 'games': len(cur_games), 'mu': cur['mu'], 'h': cur['h'], 'sdMargin': round(sdm, 2), 'sdTotal': round(sdt, 2),
            'teams': cur['teams']}


# ---------- names: TheRundown team -> ESPN team ----------
_ALIASES = {'uconn': 'connecticut', 'ole miss': 'mississippi', 'umass': 'massachusetts', 'pitt': 'pittsburgh', 'unc': 'north carolina',
            'saint marys': 'st marys', 'miami fl': 'miami', 'nc state': 'north carolina st', 'usc': 'southern california'}


def _key(s):
    s = norm(s or '').replace(' state', ' st').replace('saint ', 'st ').replace(' university', '')
    return _ALIASES.get(s, s)


def matcher(teams):
    """name/mascot -> ESPN team id (or None): same school name, or best close match sharing the mascot."""
    by_key, rows = {}, []
    for t, v in teams.items():
        k = _key(v['name']); by_key.setdefault(k, []).append(t); rows.append((t, k, norm(v.get('mascot') or '')))

    def find(name, mascot=None):
        m = norm(mascot or '')
        n = norm(name or '')
        if m and n.endswith(' ' + m):  # 'Duke Blue Devils' -> 'Duke'
            n = n[: -len(m) - 1]
        k = _key(n)
        hit = by_key.get(k) or []
        if len(hit) == 1:
            return hit[0]
        best, score = None, 0.0
        for t, tk, tm in rows:
            sc = difflib.SequenceMatcher(None, k, tk).ratio() + (0.35 if m and tm and m == tm else 0)
            if sc > score:
                best, score = t, sc
        return best if score >= 1.1 or (score >= 0.86 and not m) else None
    return find


# ---------- calls ----------
imp = lambda o: 100 / (o + 100) if o > 0 else -o / (-o + 100)
dec = lambda o: 1 + o / 100 if o > 0 else 1 + 100 / -o


def _am(x):
    try:
        return int(str(x).replace('+', ''))
    except (TypeError, ValueError):
        return None


def p_cover(mean, sd, line):
    F = lambda x: 0.5 * (1 + math.erf((x - mean) / (sd * math.sqrt(2))))
    if abs(line - round(line)) < 1e-9:
        win = 1 - F(line + 0.5); push = F(line + 0.5) - F(line - 0.5)
        return win / (1 - push) if push < 1 else 0.5
    return 1 - F(line)


def call(kind, mean, sd, line, o, u, pin_line=None, pin_o=None, pin_u=None):
    o, u = _am(o), _am(u)
    if line is None or o is None or u is None:
        return None
    po, pu = _am(pin_o), _am(pin_u)
    use_pin = po is not None and pu is not None and pin_line is not None and abs(float(pin_line) - float(line)) < 1e-9
    a, b = (po, pu) if use_pin else (o, u)
    pb = imp(a) / (imp(a) + imp(b))
    pm = p_cover(mean, sd, -line) if kind == 'spread' else p_cover(mean, sd, line)
    pf = min(.99, max(.01, pb + W * (pm - pb)))
    A, B = ('home', 'away') if kind == 'spread' else ('over', 'under')
    side = A if pf >= .5 else B; pw = pf if side == A else 1 - pf; price = o if side == A else u
    gap = abs(pm - pb)
    return {'line': line, 'odds': o, 'uodds': u, 'pinO': po if use_pin else None, 'pinU': pu if use_pin else None, 'anchor': 'Pinnacle' if use_pin else 'book',
            'pb': round(pb, 4), 'pm': round(pm, 4), 'pf': round(pf, 4), 'side': side, 'pWin': round(pw, 4), 'price': price,
            'ev': round(pw * (dec(price) - 1) - (1 - pw), 4), 'lean': bool(pw >= .53 and gap <= .22 and (-150 <= price <= -100 or 100 <= price <= 130))}


def slug(s):
    import unicodedata
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower().replace('&', 'and').replace("'", '')
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')


def board(R, odds_doc, find=None):
    """Projections and calls for every game in the odds doc that both teams can be matched for."""
    find = find or matcher(R['teams'])
    out = []
    for g in (odds_doc or {}).get('games', []):
        hi, ai = find(g['home'], g.get('homeMascot')), find(g['away'], g.get('awayMascot'))
        if not hi or not ai or hi == ai:
            continue
        neutral = bool(g.get('neutral'))
        ph, pa = project(R, hi, ai, neutral)
        sp, tot = g.get('spread') or {}, g.get('total') or {}
        calls = {}
        c = call('spread', ph - pa, R['sdMargin'], sp.get('home'), sp.get('homeOdds'), sp.get('awayOdds'), sp.get('pinHome'), sp.get('pinHomeOdds'), sp.get('pinAwayOdds'))
        if c:
            calls['spread'] = c
        c = call('total', ph + pa, R['sdTotal'], tot.get('line'), tot.get('overOdds'), tot.get('underOdds'), tot.get('pinLine'), tot.get('pinOver'), tot.get('pinUnder'))
        if c:
            calls['total'] = c
        H, A = R['teams'][hi], R['teams'][ai]
        out.append({'away': A['name'], 'home': H['name'], 'start': g.get('start'), 'neutral': neutral, 'book': g.get('book'),
                    'proj': {'home': round(ph, 1), 'away': round(pa, 1), 'margin': round(ph - pa, 1), 'total': round(ph + pa, 1), 'gp': min(H['n'], A['n'])},
                    'calls': calls, 'ids': [ai, hi]})
    return out


def lock_docs(rows, a, b):
    """ledger/cbbgl docs for games starting in [a, b)."""
    A, B = parse_iso(a), parse_iso(b); now = iso(now_utc()); docs = {}
    for r in rows:
        if not r['calls'] or not r.get('start') or not (A <= parse_iso(r['start']) < B):
            continue
        day = parse_iso(r['start']).astimezone(ET).strftime('%Y-%m-%d')
        docs[f"cbb-{day}-{slug(r['away'])}-at-{slug(r['home'])}"] = {
            'league': 'CBB', 'model': 1, 'date': day, 'game': f"{r['away']} @ {r['home']}", 'away': r['away'], 'home': r['home'], 'ids': r['ids'],
            'gameStart': r['start'], 'createdAt': now, 'lockedBy': 'task', 'w': W, 'neutral': r['neutral'], 'proj': r['proj'], 'calls': r['calls'], 'graded': False}
    return docs


def grade(doc, home_score, away_score):
    """Fill in results on a locked doc."""
    margin, total = home_score - away_score, home_score + away_score
    for k, c in doc['calls'].items():
        L = float(c['line'])
        if k == 'spread':
            v = margin + L  # home covers when margin + home line > 0
            res = 'push' if v == 0 else ('won' if (v > 0) == (c['side'] == 'home') else 'lost')
        else:
            res = 'push' if total == L else ('won' if (total > L) == (c['side'] == 'over') else 'lost')
        c['result'] = res
    doc.update(graded=True, final={'home': home_score, 'away': away_score}, gradedAt=iso(now_utc()))
    return doc


# ---------- jobs ----------
def _espn_today(d):
    """ESPN's slate for an ET date: {(home id, away id): neutral}."""
    try:
        return {(str(g.get('home_id')), str(g.get('away_id'))): bool(g.get('neutral')) for g in espn.scoreboard('cbb', YMD(d), groups=50, limit=500)}
    except RuntimeError:
        return {}


def daily(db):
    """9:31am ET, November-April: archive yesterday (and backfill on first run), refit ratings, today's board."""
    from . import rundown, sgo  # noqa: F401
    today = now_et().date(); s = season_of(today); out = []
    if not db.select('games', league='eq.cbb', season=f'eq.{s - 1}', select='id', limit='1'):
        out.append(f'backfilled last season: {archive(db, season_start(s - 1), dt.date(s - 1, 4, 10))} games')
    last = db.select('games', league='eq.cbb', season=f'eq.{s}', select='date', order='date.desc', limit='1')
    start = dt.date.fromisoformat(last[0]['date']) - dt.timedelta(days=1) if last else season_start(s)
    out.append(f'archived {archive(db, max(start, season_start(s)), today - dt.timedelta(days=1))} games')
    R = ratings(db, today)
    db.set('cbbTeams', R)
    out.append(f"ratings: {len(R['teams'])} teams, {R['games']} games this season, home court {R['h']:.1f}")
    out.append(refresh_board(db, R, today))
    return '; '.join(out)


def refresh_board(db, R=None, today=None, lock_window=None):
    """Today's lines (TheRundown only: the college slate is too big for the free odds feed) -> cbbOdds, cbbBoard; lock calls in the window."""
    from . import rundown
    if not rundown.enabled():
        return 'college lines need TheRundown (THERUNDOWN_API_KEY)'
    today = today or now_et().date(); R = R or db.get('cbbTeams')
    if not R or not R.get('teams'):
        return 'no college ratings yet'
    a = dt.datetime.combine(today, dt.time(0), ET).astimezone(dt.timezone.utc); b = a + dt.timedelta(days=1)
    odds = rundown.fetch_doc('cbb', iso(a), iso(b), [])
    db.set('cbbOdds', odds)
    neutral = _espn_today(today)
    find = matcher(R['teams'])
    for g in odds['games']:
        hi, ai = find(g['home'], g.get('homeMascot')), find(g['away'], g.get('awayMascot'))
        g['neutral'] = neutral.get((str(hi), str(ai)), False)
    rows = board(R, odds, find)
    db.set('cbbBoard', {'updated': iso(now_utc()), 'date': today.isoformat(), 'games': rows, 'unmatched': len(odds['games']) - len(rows)})
    msg = f"{len(rows)} of {len(odds['games'])} college games priced"
    if lock_window:
        docs = lock_docs(rows, *lock_window)
        have = {d['_id'] for d in db.list('ledger/cbbgl')}
        new = {k: v for k, v in docs.items() if k not in have}
        for k, v in new.items():
            db.set(f'ledger/cbbgl/{k}', v)
        msg += f'; locked {len(new)}'
    return msg


def pregame(db):
    """Hourly: lock calls for games tipping in the next full clock hour (with fresh lines)."""
    from . import sgo
    a, b = sgo.window('pre')
    board_doc = db.get('cbbBoard') or {}
    if not any(g.get('start') and parse_iso(a) <= parse_iso(g['start']) < parse_iso(b) for g in board_doc.get('games', [])):
        return 'Nothing due.'
    return refresh_board(db, lock_window=(a, b))


def settle(db):
    """Archive the last two days and grade locked calls."""
    today = now_et().date()
    archive(db, today - dt.timedelta(days=2), today)
    n = 0
    for d in db.list('ledger/cbbgl'):
        if d.get('graded') or not d.get('gameStart') or parse_iso(d['gameStart']) > now_utc() - dt.timedelta(hours=3):
            continue
        day = d['date']
        gs = db.select('games', league='eq.cbb', date=f'eq.{day}', select='id,home,away,home_score,away_score,box')
        ai, hi = (d.get('ids') or [None, None])
        g = next((g for g in gs if str((g.get('box') or {}).get('home_id')) == str(hi) and str((g.get('box') or {}).get('away_id')) == str(ai)), None)
        if not g:
            continue
        doc = grade({k: v for k, v in d.items() if k != '_id'}, g['home_score'], g['away_score'])
        db.set(f"ledger/cbbgl/{d['_id']}", doc); n += 1
    return f'{n} college games graded' if n else ''
