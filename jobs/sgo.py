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
                out.add(e['players'][o['playerID']]['name'])
    return out


def build(lg, events, players):
    """Odds doc {source, updated, games, props} from SportsGameOdds events and the player-averages list."""
    num = lambda x: float(x) if x not in (None, '') else None
    avg, avg2 = {}, {}
    for v in players or []:
        v = dict(v)
        if lg == 'nba' and None not in (v.get('pts'), v.get('reb'), v.get('ast')):
            v['pra'] = round(v['pts'] + v['reb'] + v['ast'], 1)
        n = norm(v['name']); avg[n] = v; avg2.setdefault(key2(n), v)

    def lookup(name, m):
        n = norm(name); v = avg.get(n) or avg2.get(key2(n))
        return None if not v else v.get(m)
    games, props = [], []
    for e in events:
        st = e['status']
        if st.get('started') or st.get('cancelled'):
            continue
        T = {s: e['teams'][s] for s in ('home', 'away')}
        nk = {T[s]['teamID']: T[s]['names']['medium'] for s in T}
        O = e['odds']; g = lambda k: O.get(k, {})
        sp, spa, ou = g('points-home-game-sp-home'), g('points-away-game-sp-away'), g('points-all-game-ou-over')
        ouu = O.get(ou.get('opposingOddID', ''), {})
        mh, ma = g('points-home-game-ml-home'), g('points-away-game-ml-away')
        games.append({'away': T['away']['names']['medium'], 'home': T['home']['names']['medium'], 'start': st.get('startsAt'),
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
            props.append({'player': p.get('name', pid), 'team': team, 'market': m, 'line': num(o['bookOverUnder']), 'open': num(o.get('openBookOverUnder')),
                          'odds': o.get('bookOdds'), 'uodds': O.get(o.get('opposingOddID', ''), {}).get('bookOdds'), 'avg': lookup(p.get('name', ''), m)})
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
