"""TheRundown (Starter plan, key in THERUNDOWN_API_KEY): Hard Rock lines and prices, Pinnacle as the sharp anchor, best price
across the big US books. Builds the same odds documents as sgo.build, so the model and the app read them unchanged.

    GET https://therundown.io/api/v2/sports/{sport}/events/{date}?market_ids=..&affiliate_ids=..&main_line=true&offset=<ET minutes>
    header X-TheRundown-Key

Prices are American odds; 0.0001 means off the board. One returned price row = one data point (Starter: 25M a month)."""
import datetime as dt
import os
import re
import time

import requests

from .common import ET, fix_ab, iso, key2, nick, norm, now_utc, parse_iso

BASE = 'https://therundown.io/api/v2'
SPORT = {'nfl': 2, 'nba': 4, 'cbb': 5, 'cfb': 1}
HARD_ROCK, PINNACLE = 28, 3
BOOKS = {28: 'Hard Rock', 3: 'Pinnacle', 19: 'DraftKings', 23: 'FanDuel', 22: 'BetMGM', 35: 'Caesars'}
AFFILIATES = ','.join(str(b) for b in BOOKS)
GAME_MARKETS = [1, 2, 3]  # moneyline, spread, total (full game)
NBA_PROPS = {29: 'pts', 35: 'reb', 39: 'ast', 38: 'fg3', 93: 'pra'}
NFL_PROP_NAMES = {'pass': r'pass(ing)?[ _]?y(ar)?ds', 'rush': r'rush(ing)?[ _]?y(ar)?ds', 'rec': r'rec(eiving)?[ _]?y(ar)?ds'}
_S = requests.Session()
usage = {'points': 0}


def key():
    return (os.environ.get('THERUNDOWN_API_KEY') or '').strip()


def enabled():
    return bool(key())


class RundownError(RuntimeError):
    pass


def get(path, **params):
    for attempt in range(4):
        try:
            r = _S.get(BASE + path, params=params, headers={'X-TheRundown-Key': key()}, timeout=60)
        except requests.RequestException as e:
            if attempt == 3:
                raise RundownError(f'TheRundown unreachable: {e}') from e
            time.sleep(2 * (attempt + 1)); continue
        if r.headers.get('X-Datapoints'):
            try:
                usage['points'] += int(r.headers['X-Datapoints'])
            except ValueError:
                pass
        if r.status_code == 429:  # Starter: 2 requests/second
            time.sleep(1.5 * (attempt + 1)); continue
        if r.status_code in (401, 403):
            raise RundownError(f'TheRundown refused the key ({r.status_code}). Check the THERUNDOWN_API_KEY secret.')
        if r.status_code >= 500 and attempt < 3:
            time.sleep(2 * (attempt + 1)); continue
        if r.status_code >= 400:
            raise RundownError(f'TheRundown {r.status_code}: {r.text[:200]}')
        return r.json()
    raise RundownError('TheRundown kept rate-limiting')


def et_offset(d):
    """Minutes to add to ET to get UTC on that date (300 in winter, 240 in summer): the 'offset' TheRundown wants."""
    t = dt.datetime.combine(d, dt.time(12), ET)
    return int(-t.utcoffset().total_seconds() // 60)


_markets_cache = {}


def nfl_prop_markets(date):
    """NFL passing/rushing/receiving yards market ids, discovered from TheRundown's market list for the date."""
    if 'nfl' in _markets_cache:
        return _markets_cache['nfl']
    out = {}
    try:
        j = get(f'/sports/markets/{date.isoformat()}', offset=et_offset(date))
        ms = j.get(str(SPORT['nfl'])) or j.get(SPORT['nfl']) or []
    except RundownError:
        ms = []
    for m in ms:
        if m.get('period_id') not in (0, None):
            continue
        text = ' '.join(str(m.get(k) or '') for k in ('name', 'short_description', 'description')).lower()
        if any(w in text for w in ('longest', 'alt', 'first', '1st', 'half', 'quarter', 'combined', '+')):
            continue
        for mk, pat in NFL_PROP_NAMES.items():
            if mk not in out and re.search(pat, text):
                out[mk] = m['id']
    if not out:  # discovery failed: fall back to the full market list
        try:
            for m in get('/markets'):
                text = ' '.join(str(m.get(k) or '') for k in ('name', 'short_description', 'description')).lower()
                if m.get('period_id') not in (0, None) or any(w in text for w in ('longest', 'alt', 'first', 'half', 'quarter', '+')):
                    continue
                for mk, pat in NFL_PROP_NAMES.items():
                    if mk not in out and re.search(pat, text) and 'player' in text:
                        out[mk] = m['id']
        except RundownError:
            pass
    _markets_cache['nfl'] = {v: k for k, v in out.items()}
    return _markets_cache['nfl']


def events(lg, d, market_ids):
    """Events (with markets) on ET date d."""
    ids = list(market_ids)
    out = {}
    for k in range(0, len(ids), 12):  # max 12 market ids per request
        j = get(f"/sports/{SPORT[lg]}/events/{d.isoformat()}", market_ids=','.join(map(str, ids[k:k + 12])),
                affiliate_ids=AFFILIATES, main_line='true', offset=et_offset(d))
        for e in j.get('events') or []:
            have = out.setdefault(e['event_id'], e)
            if have is not e:
                have.setdefault('markets', []).extend(e.get('markets') or [])
    return list(out.values())


# ---------- parsing ----------
def amer(p):
    try:
        v = float(p)
    except (TypeError, ValueError):
        return None
    if abs(v - 0.0001) < 1e-9 or v == 0:
        return None  # off the board
    return int(round(v))


def fmt(v):
    return None if v is None else (f'+{v}' if v > 0 else str(v))


def team_name(lg, t):
    ab = t.get('abbreviation')
    if lg in ('nba', 'nfl'):
        n = nick(lg, ab)
        if n:
            return n
    return t.get('mascot') if lg in ('nba', 'nfl') else (t.get('name') or t.get('mascot') or ab)


def _price_rows(line):
    """{affiliate_id: american odds} for a line, off-board prices dropped."""
    out = {}
    for aff, p in (line.get('prices') or {}).items():
        v = amer((p or {}).get('price'))
        if v is not None:
            out[int(aff)] = v
    return out


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def pick_book(two_sided):
    """two_sided: {aff: (line, over_price, under_price)}. Hard Rock first, else the most common line among the US books."""
    if HARD_ROCK in two_sided:
        return HARD_ROCK
    us = [a for a in two_sided if a != PINNACLE]
    if not us:
        return None
    lines = [two_sided[a][0] for a in us]
    common = max(set(lines), key=lines.count)
    return next(a for a in us if two_sided[a][0] == common)


def side_markets(market):
    """For an over/under market: {(player key, player name): {aff: (line, over, under)}} using main lines."""
    by = {}
    for part in market.get('participants') or []:
        name = part.get('name') or ''
        typ = (part.get('type') or '').upper()
        side = 'over' if typ == 'TYPE_OVER' or re.search(r'\bover$', name, re.I) else 'under' if typ == 'TYPE_UNDER' or re.search(r'\bunder$', name, re.I) else None
        base = re.sub(r'(^|\s+)(over|under)$', '', name, flags=re.I).strip()  # 'Jalen Brunson Over' -> name; plain 'Over' -> ''
        if side is None:
            continue
        for line in part.get('lines') or []:
            val = _num(line.get('value'))
            if val is None:
                continue
            for aff, pr in _price_rows(line).items():
                slot = by.setdefault(base, {}).setdefault(aff, {})
                slot.setdefault(val, {})[side] = pr
    out = {}
    for base, affs in by.items():
        for aff, lines in affs.items():
            both = [(v, s['over'], s['under']) for v, s in lines.items() if 'over' in s and 'under' in s]
            if both:  # main line = the most balanced two-sided pair
                imp = lambda o: 100 / (o + 100) if o > 0 else -o / (-o + 100)
                out.setdefault(base, {})[aff] = min(both, key=lambda t: abs(imp(t[1]) - imp(t[2])))
    return out


def game_entry(lg, e):
    teams = e.get('teams') or []
    H = next((t for t in teams if t.get('is_home')), None)
    A = next((t for t in teams if t.get('is_away')), None)
    if not H or not A:
        return None, None
    home, away = team_name(lg, H), team_name(lg, A)
    g = {'away': away, 'home': home, 'start': e.get('event_date'), 'spread': {}, 'total': {}, 'ml': {}, 'book': None, 'rundownId': e.get('event_id'),
         'awayMascot': A.get('mascot'), 'homeMascot': H.get('mascot')}
    for m in e.get('markets') or []:
        mid = m.get('market_id')
        if mid == 1 or mid == 2:
            per = {}  # aff -> {home:(val, price), away:(val, price)}
            for part in m.get('participants') or []:
                who = 'home' if part.get('id') == H.get('team_id') else 'away' if part.get('id') == A.get('team_id') else None
                if not who:
                    continue
                for line in part.get('lines') or []:
                    for aff, pr in _price_rows(line).items():
                        per.setdefault(aff, {})[who] = (_num(line.get('value')), pr)
            two = {a: (v['home'][0], v['home'][1], v['away'][1]) for a, v in per.items() if 'home' in v and 'away' in v}
            b = pick_book(two)
            if b is None:
                continue
            if mid == 1:
                g['ml'] = {'home': fmt(two[b][1]), 'away': fmt(two[b][2])}
                if PINNACLE in two:
                    g['ml'].update(pinHome=fmt(two[PINNACLE][1]), pinAway=fmt(two[PINNACLE][2]))
            else:
                g['spread'] = {'home': two[b][0], 'homeOdds': fmt(two[b][1]), 'awayOdds': fmt(two[b][2])}
                if PINNACLE in two:
                    g['spread'].update(pinHome=two[PINNACLE][0], pinHomeOdds=fmt(two[PINNACLE][1]), pinAwayOdds=fmt(two[PINNACLE][2]))
                g['book'] = BOOKS.get(b)
        elif mid == 3:
            sm = side_markets(m)
            two = next(iter(sm.values()), {}) if sm else {}
            b = pick_book(two)
            if b is None:
                continue
            g['total'] = {'line': two[b][0], 'overOdds': fmt(two[b][1]), 'underOdds': fmt(two[b][2])}
            if PINNACLE in two:
                g['total'].update(pinLine=two[PINNACLE][0], pinOver=fmt(two[PINNACLE][1]), pinUnder=fmt(two[PINNACLE][2]))
    return g, (H, A)


def build(lg, evs, players, team_of=None, prop_markets=None):
    """Odds doc in the sgo.build shape, plus: book (Hard Rock (FL) when Hard Rock has it), pinO/pinU (Pinnacle at the same
    line: the model's sharp anchor), bestO/bestU (best price at that line across the books)."""
    team_of = team_of or (lambda name: None)
    avg, avg2, avg3 = {}, {}, {}
    for v in players or []:
        v = dict(v)
        if lg == 'nba' and None not in (v.get('pts'), v.get('reb'), v.get('ast')):
            v['pra'] = round(v['pts'] + v['reb'] + v['ast'], 1)
        n = norm(v['name']); avg[n] = v; avg2.setdefault(key2(n), v); avg3.setdefault(n.replace(' ', ''), v)
    find = lambda name: avg.get(norm(name)) or avg2.get(key2(norm(name))) or avg3.get(norm(name).replace(' ', ''))
    games, props = [], []
    for e in evs:
        st = ((e.get('score') or {}).get('event_status') or '').upper()
        if any(x in st for x in ('IN_PROGRESS', 'FINAL', 'CANCEL', 'POSTPONE', 'HALFTIME')):
            continue
        g, HA = game_entry(lg, e)
        if not g:
            continue
        games.append(g)
        for m in e.get('markets') or []:
            mk = (prop_markets or {}).get(m.get('market_id'))
            if not mk:
                continue
            for name, two in side_markets(m).items():
                b = pick_book(two)
                if b is None:
                    continue
                line, o, u = two[b]
                v = find(name)
                nm = v['name'] if v else name
                team = (v or {}).get('team') or team_of(nm)
                if team not in (g['home'], g['away']):
                    continue  # can't place the player in this game
                p = {'player': nm, 'team': team, 'market': mk, 'line': line, 'odds': fmt(o), 'uodds': fmt(u),
                     'book': 'Hard Rock (FL)' if b == HARD_ROCK else BOOKS.get(b), 'avg': (v or {}).get(mk)}
                pin = two.get(PINNACLE)
                if pin and abs(pin[0] - line) < 1e-9:
                    p['pinO'], p['pinU'] = fmt(pin[1]), fmt(pin[2])
                elif pin:
                    p['pinLine'] = pin[0]
                same = [t for a, t in two.items() if a not in (PINNACLE,) and abs(t[0] - line) < 1e-9]
                if same:
                    p['bestO'], p['bestU'] = fmt(max(t[1] for t in same)), fmt(max(t[2] for t in same))
                props.append(p)
    return {'source': 'TheRundown: Hard Rock + Pinnacle', 'updated': iso(now_utc()), 'games': games, 'props': props}


def fetch_doc(lg, a, b, players, team_of=None):
    """Odds doc for games starting in [a, b) (UTC ISO strings)."""
    A, B = parse_iso(a), parse_iso(b)
    d0, d1 = A.astimezone(ET).date(), (B - dt.timedelta(seconds=1)).astimezone(ET).date()
    mk = dict(NBA_PROPS) if lg == 'nba' else nfl_prop_markets(d0) if lg == 'nfl' else {}
    evs, d = [], d0
    while d <= d1:
        evs += [e for e in events(lg, d, GAME_MARKETS + list(mk)) if e.get('event_date') and A <= parse_iso(e['event_date']) < B]
        d += dt.timedelta(days=1)
    return build(lg, evs, players, team_of, mk)


def probe(db):
    """Diagnostics: stores a trimmed raw sample of today's NBA and NFL responses in the 'rundownProbe' doc."""
    today = dt.datetime.now(ET).date(); out = {}
    for lg, mk in (('nba', [1, 2, 3, 29, 93]), ('nfl', [1, 2, 3])):
        try:
            evs = events(lg, today, mk)
        except RundownError as e:
            out[lg] = {'error': str(e)}; continue
        def trim(e):
            e = dict(e); ms = []
            for m in (e.get('markets') or [])[:6]:
                m = dict(m); m['participants'] = [dict(p, lines=(p.get('lines') or [])[:2]) for p in (m.get('participants') or [])[:4]]
                ms.append(m)
            e['markets'] = ms
            return e
        out[lg] = {'events': len(evs), 'sample': [trim(e) for e in evs[:2]],
                   'statuses': sorted({((e.get('score') or {}).get('event_status') or '') for e in evs}),
                   'market_ids': sorted({m.get('market_id') for e in evs for m in e.get('markets') or []})}
    try:
        ms = get(f'/sports/markets/{today.isoformat()}', offset=et_offset(today))
        out['nfl_markets'] = [{k: m.get(k) for k in ('id', 'name', 'short_description', 'period_id', 'proposition')} for m in (ms.get('2') or [])][:80]
    except RundownError as e:
        out['nfl_markets'] = str(e)
    out['points'] = usage['points']
    db.set('rundownProbe', out)
    return f"probe: nba {out.get('nba', {}).get('events')} events, nfl {out.get('nfl', {}).get('events')} events"
