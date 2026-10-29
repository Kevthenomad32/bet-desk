"""ESPN public site API: schedules, scores, box scores, injuries. Feeds the games archive (replaces StatMuse)."""
import time

import requests

from .common import ET, et_date, fix_ab, nick, norm, parse_iso

BASE = 'https://site.api.espn.com/apis/site/v2/sports/'
PATH = {'nba': 'basketball/nba', 'nfl': 'football/nfl', 'cfb': 'football/college-football'}
_S = requests.Session()
_S.headers['User-Agent'] = 'Mozilla/5.0 (BetDesk jobs)'


HOSTS = ('https://site.api.espn.com/apis/site/v2/sports/', 'https://site.web.api.espn.com/apis/site/v2/sports/')


def get(path, **params):
    """ESPN JSON. Tries the main host, then the web host, then again without 'limit' (ESPN sometimes rejects it)."""
    seen = []
    tries = [(h, params) for h in HOSTS]
    if 'limit' in params:
        tries += [(h, {k: v for k, v in params.items() if k != 'limit'}) for h in HOSTS]
    for host, ps in tries:
        for attempt in range(2):
            try:
                r = _S.get(host + path, params=ps, timeout=30)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 404:
                    return {}
                seen.append(f'{r.status_code}')
            except requests.RequestException as e:  # noqa: PERF203
                seen.append(type(e).__name__)
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f'ESPN unavailable: {path} {params} (got {", ".join(seen)})')


def num(v):
    try:
        return float(str(v).split('-')[0])
    except (TypeError, ValueError):
        return None


def made_att(v):
    """'5-12' -> (5, 12)"""
    try:
        a, b = str(v).split('-')[:2]
        return float(a), float(b)
    except (TypeError, ValueError):
        return None, None


# ---------- schedule / scores ----------
def scoreboard(lg, dates=None, **extra):
    params = dict(limit=400, **extra)
    if dates:
        params['dates'] = dates
    j = get(f'{PATH[lg]}/scoreboard', **params)
    out = []
    for e in j.get('events', []):
        comp = (e.get('competitions') or [{}])[0]
        cs = comp.get('competitors') or []
        H = next((c for c in cs if c.get('homeAway') == 'home'), {})
        A = next((c for c in cs if c.get('homeAway') == 'away'), {})
        st = (e.get('status') or comp.get('status') or {}).get('type', {})
        state = 'final' if st.get('state') == 'post' else 'pre' if st.get('state') == 'pre' else 'live'
        if any(x in (st.get('name') or '') for x in ('POSTPONED', 'CANCELED', 'SUSPENDED')):
            state = 'off'
        hab, aab = fix_ab(lg, H.get('team', {}).get('abbreviation')), fix_ab(lg, A.get('team', {}).get('abbreviation'))
        out.append({'id': e['id'], 'league': lg, 'start': e.get('date'), 'date': et_date(e['date']) if e.get('date') else None, 'state': state,
                    'detail': st.get('shortDetail', ''), 'season': (e.get('season') or {}).get('year'), 'season_type': (e.get('season') or {}).get('type'),
                    'week': (e.get('week') or {}).get('number'),
                    'home_ab': hab, 'away_ab': aab,
                    'home': nick(lg, hab) if lg != 'cfb' else H.get('team', {}).get('shortDisplayName'),
                    'away': nick(lg, aab) if lg != 'cfb' else A.get('team', {}).get('shortDisplayName'),
                    'home_score': int(H['score']) if H.get('score') not in (None, '') and state != 'pre' else None,
                    'away_score': int(A['score']) if A.get('score') not in (None, '') and state != 'pre' else None,
                    'home_ls': [num(x.get('displayValue', x.get('value'))) for x in H.get('linescores') or []],
                    'away_ls': [num(x.get('displayValue', x.get('value'))) for x in A.get('linescores') or []]})
    return out


def current_nfl_week():
    j = get(f"{PATH['nfl']}/scoreboard")
    return (j.get('week') or {}).get('number'), (j.get('season') or {}).get('year'), (j.get('season') or {}).get('type')


# ---------- box scores ----------
NBA_COL = {'minutes': 'min', 'points': 'pts', 'rebounds': 'reb', 'assists': 'ast', 'turnovers': 'tov', 'offensiveRebounds': 'oreb',
           'threePointFieldGoalsMade-threePointFieldGoalsAttempted': 'fg3', 'fieldGoalsMade-fieldGoalsAttempted': 'fgm', 'freeThrowsMade-freeThrowsAttempted': 'ftm',
           'steals': 'stl', 'blocks': 'blk'}
NFL_COL = {('passing', 'passingYards'): 'pass', ('passing', 'passingTouchdowns'): 'passTd', ('passing', 'interceptions'): 'int',
           ('passing', 'completions/passingAttempts'): 'cmp', ('rushing', 'rushingAttempts'): 'car', ('rushing', 'rushingYards'): 'rush',
           ('rushing', 'rushingTouchdowns'): 'rushTd', ('rushing', 'longRushing'): 'longRush', ('receiving', 'receptions'): 'recs',
           ('receiving', 'receivingYards'): 'rec', ('receiving', 'receivingTouchdowns'): 'recTd', ('receiving', 'longReception'): 'longRec',
           ('receiving', 'receivingTargets'): 'tgt'}


def box(lg, event_id):
    """Compact box score: {'home': {...team}, 'away': {...}, 'players': [{id,name,side,starter,dnp,stats}], 'state'}."""
    j = get(f'{PATH[lg]}/summary', event=event_id)
    comp = ((j.get('header') or {}).get('competitions') or [{}])[0]
    st = (comp.get('status') or {}).get('type', {})
    sides = {}
    for c in comp.get('competitors') or []:
        ab = fix_ab(lg, c.get('team', {}).get('abbreviation'))
        sides[ab] = c.get('homeAway')
    teams = {}
    for t in (j.get('boxscore') or {}).get('teams') or []:
        ab = fix_ab(lg, t.get('team', {}).get('abbreviation'))
        stats = {}
        for s in t.get('statistics') or []:
            stats[s.get('name')] = s.get('displayValue')
        teams[sides.get(ab, t.get('homeAway'))] = {'ab': ab, 'stats': stats}
    for c in comp.get('competitors') or []:
        side = c.get('homeAway')
        teams.setdefault(side, {'ab': fix_ab(lg, c.get('team', {}).get('abbreviation')), 'stats': {}})
        teams[side]['score'] = int(c['score']) if c.get('score') not in (None, '') else None
        teams[side]['ls'] = [num(x.get('displayValue', x.get('value'))) for x in c.get('linescores') or []]
    players = {}
    for blk in (j.get('boxscore') or {}).get('players') or []:
        ab = fix_ab(lg, blk.get('team', {}).get('abbreviation'))
        side = sides.get(ab)
        for grp in blk.get('statistics') or []:
            gname = (grp.get('name') or '').lower()
            keys = grp.get('keys') or []
            for a in grp.get('athletes') or []:
                ath = a.get('athlete') or {}
                pid = str(ath.get('id') or ath.get('displayName'))
                p = players.setdefault(pid, {'id': pid, 'name': ath.get('displayName', ''), 'side': side, 'ab': ab, 'starter': bool(a.get('starter')), 'dnp': False, 'stats': {}})
                vals = a.get('stats') or []
                if a.get('didNotPlay') or not vals:
                    if lg == 'nba':
                        p['dnp'] = True
                    continue
                for i, k in enumerate(keys):
                    col = NBA_COL.get(k) if lg == 'nba' else NFL_COL.get((gname, k))
                    if not col or i >= len(vals):
                        continue
                    v = vals[i]
                    if col in ('fgm', 'ftm', 'cmp'):
                        m, at = made_att(str(v).replace('/', '-'))
                        if col == 'fgm':
                            p['stats']['fga'] = at
                        elif col == 'ftm':
                            p['stats']['fta'] = at
                        else:
                            p['stats']['att'] = at
                        p['stats'][col] = m
                    else:
                        p['stats'][col] = num(v)
    return {'state': 'final' if st.get('state') == 'post' else 'pre' if st.get('state') == 'pre' else 'live', 'detail': st.get('shortDetail', ''),
            'teams': teams, 'players': list(players.values())}


# ---------- archive ----------
def archive_games(db, lg, games, force=False):
    """Store final games (box score + player lines). Skips games already archived as final unless force."""
    finals = [g for g in games if g['state'] == 'final' and (g.get('season_type') in (2, 3) or lg == 'cfb')]
    if not finals:
        return 0
    have = set()
    if not force:
        ids = ','.join(g['id'] for g in finals)
        have = {r['id'] for r in db.select('games', league=f'eq.{lg}', id=f'in.({ids})', status='eq.final', select='id')}
    n = 0
    for g in finals:
        if g['id'] in have:
            continue
        b = box(lg, g['id'])
        if b['state'] != 'final':
            continue
        db.upsert('games', [{'league': lg, 'id': g['id'], 'date': g['date'], 'start_at': g['start'], 'season': g['season'], 'season_type': g['season_type'],
                             'week': g['week'], 'status': 'final', 'home': g['home'], 'away': g['away'], 'home_score': g['home_score'], 'away_score': g['away_score'],
                             'box': b}], 'league,id')
        rows = []
        for p in b['players']:
            mine = g['home'] if p['side'] == 'home' else g['away']
            opp = g['away'] if p['side'] == 'home' else g['home']
            rows.append({'league': lg, 'game_id': g['id'], 'player_id': p['id'], 'name': p['name'], 'name_norm': norm(p['name']), 'team': mine,
                         'team_ab': g['home_ab'] if p['side'] == 'home' else g['away_ab'], 'opp': opp, 'opp_ab': g['away_ab'] if p['side'] == 'home' else g['home_ab'],
                         'date': g['date'], 'season': g['season'], 'starter': p['starter'], 'dnp': p['dnp'], 'stats': p['stats']})
        rows = list({r['player_id']: r for r in rows if r['player_id']}.values())  # one row per player (a batch can't touch a row twice)
        if rows:
            db.upsert('player_games', rows, 'league,game_id,player_id')
        n += 1
        time.sleep(0.3)
    return n


# ---------- injuries ----------
def injuries(lg):
    """[{name, team, status, desc}] from ESPN's league-wide injury report."""
    j = get(f'{PATH[lg]}/injuries')
    out = []
    for t in j.get('injuries') or []:
        for x in t.get('injuries') or []:
            ath = x.get('athlete') or {}
            team = nick(lg, (ath.get('team') or {}).get('abbreviation')) or nick(lg, t.get('abbreviation')) or (t.get('displayName') or '').split()[-1]
            d = x.get('details') or {}
            desc = ' '.join(v for v in (d.get('side'), d.get('type'), d.get('detail')) if v) or (x.get('shortComment') or '')[:80]
            out.append({'name': ath.get('displayName', ''), 'team': team, 'status': x.get('status') or (x.get('type') or {}).get('description', ''), 'desc': desc,
                        'returnDate': d.get('returnDate')})
    return out


def et_time(start, with_day=False):
    t = parse_iso(start).astimezone(ET)
    s = t.strftime('%-I:%M%p').lower()
    return (t.strftime('%a ') + s) if with_day else s
