"""SportsGameOdds: book lines for the app (same builder the Claude tasks used, plus spread/total prices)."""
import datetime as dt
import time

import requests

from .common import ET, UTC, env, iso, key2, norm, now_utc, parse_iso

URL = 'https://api.sportsgameodds.com/v2/events'
GAME = 'points-home-game-sp-home,points-away-game-sp-away,points-all-game-ou-over,points-home-game-ml-home,points-away-game-ml-away'
ODDS = {
    'NBA': GAME + ',points-PLAYER_ID-game-ou-over,rebounds-PLAYER_ID-game-ou-over,assists-PLAYER_ID-game-ou-over,threePointersMade-PLAYER_ID-game-ou-over,points+rebounds+assists-PLAYER_ID-game-ou-over',
    'NFL': GAME + ',passing_yards-PLAYER_ID-game-ou-over,rushing_yards-PLAYER_ID-game-ou-over,receiving_yards-PLAYER_ID-game-ou-over',
    'NCAAF': GAME,
}
MK = {'nba': {'points': 'pts', 'rebounds': 'reb', 'assists': 'ast', 'threePointersMade': 'fg3', 'points+rebounds+assists': 'pra'},
      'nfl': {'passing_yards': 'pass', 'rushing_yards': 'rush', 'receiving_yards': 'rec'}, 'cfb': {}}


def fetch(league, a, b, odds=None):
    """One call, retried once on a network error. Free tier: 2,500 events/month, 10 requests/minute."""
    params = {'leagueID': league, 'startsAfter': a, 'startsBefore': b, 'limit': 50, 'includeOpposingOdds': 'true', 'oddID': odds or ODDS[league]}
    for attempt in range(2):
        try:
            r = requests.get(URL, params=params, headers={'x-api-key': env('SGO_API_KEY')}, timeout=60)
            j = r.json()
            if not j.get('success'):
                raise RuntimeError(f"SportsGameOdds said: {j.get('error') or r.status_code}")
            return j.get('data') or []
        except requests.RequestException:
            if attempt:
                raise
            time.sleep(5)


def usage():
    r = requests.get('https://api.sportsgameodds.com/v2/account/usage', headers={'x-api-key': env('SGO_API_KEY')}, timeout=30)
    lim = (r.json().get('data') or {}).get('rateLimits', {}).get('per-month', {})
    return {'used': lim.get('current-entities'), 'max': lim.get('max-entities')}


def window(kind):
    """UTC [start, end): nba = today ET; nfl = now .. next Tuesday 8am ET; pre = the next full clock hour; cfb = now .. Sunday 6am ET."""
    t = dt.datetime.now(ET)
    if kind == 'pre':
        a = t.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
        b = a + dt.timedelta(hours=1)
    elif kind == 'nba':
        a = t.replace(hour=0, minute=0, second=0, microsecond=0)
        b = a + dt.timedelta(days=1)
    elif kind == 'cfb':
        a = t
        b = (t + dt.timedelta(days=(6 - t.weekday()) % 7 or 7)).replace(hour=6, minute=0, second=0, microsecond=0)
    else:
        a = t
        b = (t + dt.timedelta(days=(1 - t.weekday()) % 7 or 7)).replace(hour=8, minute=0, second=0, microsecond=0)
    return iso(a), iso(b)


def names(events):
    out = set()
    for e in events:
        if e['status'].get('started'):
            continue
        for o in e['odds'].values():
            if o.get('playerID') and o['playerID'] in e.get('players', {}):
                out.add(pname(e['players'][o['playerID']], o['playerID']))
    return out


def pname(p, pid):
    """Player's display name. SportsGameOdds sometimes leaves out 'name' (NBA, Oct 2026): use first/last name, else the id
    (AARON_WIGGINS_1_NBA -> Aaron Wiggins)."""
    p = p or {}
    if p.get('name'):
        return p['name']
    fl = ' '.join(x for x in (p.get('firstName'), p.get('lastName')) if x)
    if fl:
        return fl
    parts = str(pid or '').split('_')
    while parts and (parts[-1].isdigit() or parts[-1].isupper() and parts[-1] in ('NBA', 'NFL', 'NCAAF', 'NCAAB', 'WNBA', 'MLB', 'NHL')):
        parts.pop()
    return ' '.join(w.capitalize() if len(w) > 2 or not w.isalpha() else w.upper() for w in parts) or str(pid)


def tname(t):
    """Team display name: 'medium' (Packers), else short/long/id (college teams sometimes only have some of these)."""
    n = (t or {}).get('names') or {}
    return n.get('medium') or n.get('short') or n.get('long') or str((t or {}).get('teamID', '')).replace('_NCAAF', '').replace('_', ' ').title()


def build(lg, events, players):
    """Odds doc {source, updated, games, props} from SportsGameOdds events and the player-averages list."""
    num = lambda x: float(x) if x not in (None, '') else None
    avg, avg2, avg3 = {}, {}, {}
    for v in players or []:
        v = dict(v)
        if lg == 'nba' and None not in (v.get('pts'), v.get('reb'), v.get('ast')):
            v['pra'] = round(v['pts'] + v['reb'] + v['ast'], 1)
        n = norm(v['name']); avg[n] = v; avg2.setdefault(key2(n), v); avg3.setdefault(n.replace(' ', ''), v)

    def find(name):  # exact, then first-3-letters + last name, then ignoring spaces/hyphens (GILGEOUSALEXANDER, DE_AARON)
        n = norm(name)
        return avg.get(n) or avg2.get(key2(n)) or avg3.get(n.replace(' ', ''))

    def lookup(name, m):
        v = find(name)
        return None if not v else v.get(m)
    games, props = [], []
    for e in events:
        st = e['status']
        if st.get('started') or st.get('cancelled'):
            continue
        T = {s: e['teams'][s] for s in ('home', 'away')}
        nk = {T[s]['teamID']: tname(T[s]) for s in T}
        O = e['odds']; g = lambda k: O.get(k, {})
        sp, spa, ou = g('points-home-game-sp-home'), g('points-away-game-sp-away'), g('points-all-game-ou-over')
        ouu = O.get(ou.get('opposingOddID', ''), {})
        mh, ma = g('points-home-game-ml-home'), g('points-away-game-ml-away')
        games.append({'away': tname(T['away']), 'home': tname(T['home']), 'start': st.get('startsAt'),
                      'spread': {'home': num(sp.get('bookSpread')), 'homeOpen': num(sp.get('openBookSpread')), 'homeOdds': sp.get('bookOdds'), 'awayOdds': spa.get('bookOdds')},
                      'total': {'line': num(ou.get('bookOverUnder')), 'open': num(ou.get('openBookOverUnder')), 'overOdds': ou.get('bookOdds'), 'underOdds': ouu.get('bookOdds')},
                      'ml': {'home': mh.get('bookOdds'), 'away': ma.get('bookOdds'), 'homeOpen': mh.get('openBookOdds'), 'awayOpen': ma.get('openBookOdds')}})
        for o in O.values():
            pid = o.get('playerID'); m = MK[lg].get(o.get('statID'))
            if not pid or not m or o.get('betTypeID') != 'ou' or o.get('sideID') != 'over' or o.get('periodID') != 'game':
                continue
            if not o.get('bookOddsAvailable') or o.get('bookOverUnder') in (None, ''):
                continue
            p = e['players'].get(pid, {}); team = nk.get(p.get('teamID'))
            if not team:
                continue
            nm = pname(p, pid)
            if not p.get('name') and find(nm):
                nm = find(nm)['name']  # the name rebuilt from an id: use the stats feed's spelling so the model matches it
            props.append({'player': nm, 'team': team, 'market': m, 'line': num(o['bookOverUnder']), 'open': num(o.get('openBookOverUnder')),
                          'odds': o.get('bookOdds'), 'uodds': O.get(o.get('opposingOddID', ''), {}).get('bookOdds'), 'avg': lookup(nm, m)})
    return {'source': 'SportsGameOdds consensus', 'updated': iso(now_utc()), 'games': games, 'props': props}


def merge(old, new):
    """Games/props in NEW replace those teams' entries in OLD."""
    old = old or {'games': [], 'props': []}
    T = {t for g in new['games'] for t in (g['away'], g['home'])}
    return dict(new, games=[g for g in old.get('games', []) if g['away'] not in T and g['home'] not in T] + new['games'],
                props=[p for p in old.get('props', []) if p['team'] not in T] + new['props'])


def due(doc, a, b):
    A, B = parse_iso(a), parse_iso(b)
    return [x for x in (doc or {}).get('games', []) if x.get('start') and A <= parse_iso(x['start']) < B]
