"""StatMuse (your developer API key, STATMUSE_API_KEY): the same tools the old Claude tasks used, called server-to-server.
MCP over Streamable HTTP at https://api.statmuse.com/mcp with the key in the x-statmuse-api-key header.
Every builder returns the same document shapes as the ESPN builders in stats.py, so the model can't tell the difference.
If the key is missing or StatMuse fails, callers fall back to ESPN."""
import itertools
import json
import os
import time

import requests

from .common import NBA_AB, NFL_AB, fix_ab

URL = 'https://api.statmuse.com/mcp'
_S = requests.Session()
_ids = itertools.count(1)
_session = {'id': None, 'ready': False}
quota = {}  # last X-Quota-* headers seen

# StatMuse team ids (NFL injuries need them; there is no league-wide NFL report)
NFL_TEAM_IDS = [4, 7, 18, 35, 46, 48, 49, 50, 53, 58, 63, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86]


def key():
    return (os.environ.get('STATMUSE_API_KEY') or '').strip()


def enabled():
    return bool(key())


class StatMuseError(RuntimeError):
    pass


def _headers():
    h = {'x-statmuse-api-key': key(), 'content-type': 'application/json', 'accept': 'application/json, text/event-stream'}
    if _session['id']:
        h['mcp-session-id'] = _session['id']
    return h


def _parse(r):
    """JSON body, or the last JSON-RPC message in an event stream."""
    ct = r.headers.get('content-type', '')
    if 'text/event-stream' in ct:
        msg = None
        for line in r.text.splitlines():
            if line.startswith('data:'):
                try:
                    msg = json.loads(line[5:].strip())
                except ValueError:
                    pass
        return msg or {}
    return r.json() if r.text.strip() else {}


def _post(body):
    for attempt in range(3):
        try:
            r = _S.post(URL, headers=_headers(), json=body, timeout=60)
        except requests.RequestException as e:
            if attempt == 2:
                raise StatMuseError(f'StatMuse unreachable: {e}') from e
            time.sleep(2 * (attempt + 1)); continue
        for k, v in r.headers.items():
            if k.lower().startswith('x-quota-'):
                quota[k.lower()[8:]] = v
        if r.headers.get('mcp-session-id'):
            _session['id'] = r.headers['mcp-session-id']
        if r.status_code == 429:  # 5 requests/second by default
            time.sleep(1.5 * (attempt + 1)); continue
        if r.status_code in (401, 403):
            raise StatMuseError(f'StatMuse refused the API key ({r.status_code}). Check the STATMUSE_API_KEY secret.')
        if r.status_code == 402:
            raise StatMuseError('StatMuse quota used up for this billing period (402).')
        if r.status_code >= 400:
            if attempt == 2 or r.status_code < 500:
                raise StatMuseError(f'StatMuse {r.status_code}: {r.text[:200]}')
            time.sleep(2 * (attempt + 1)); continue
        return _parse(r)
    raise StatMuseError('StatMuse kept rate-limiting')


def _init():
    if _session['ready']:
        return
    _post({'jsonrpc': '2.0', 'id': next(_ids), 'method': 'initialize',
           'params': {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'bet-desk', 'version': '1'}}})
    try:
        _S.post(URL, headers=_headers(), json={'jsonrpc': '2.0', 'method': 'notifications/initialized'}, timeout=30)
    except requests.RequestException:
        pass
    _session['ready'] = True


def call(tool, **args):
    """Calls a StatMuse tool and returns its JSON result (dict)."""
    if not enabled():
        raise StatMuseError('No STATMUSE_API_KEY')
    _init()
    msg = _post({'jsonrpc': '2.0', 'id': next(_ids), 'method': 'tools/call', 'params': {'name': tool, 'arguments': args}})
    if msg.get('error'):
        raise StatMuseError(f"StatMuse {tool}: {msg['error'].get('message', msg['error'])}")
    res = msg.get('result') or {}
    if res.get('structuredContent') is not None:
        out = res['structuredContent']
    else:
        text = ''.join(c.get('text', '') for c in res.get('content') or [] if c.get('type') == 'text')
        try:
            out = json.loads(text)
        except ValueError:
            raise StatMuseError(f'StatMuse {tool}: {text[:200] or "empty answer"}') from None
    if res.get('isError'):
        raise StatMuseError(f'StatMuse {tool}: {str(out)[:200]}')
    return out


def rows(res):
    """{columns, rows} -> list of dicts."""
    cols = res.get('columns') or []
    return [dict(zip(cols, r)) for r in res.get('rows') or []]


def num(v):
    if v in (None, ''):
        return None
    try:
        return float(str(v).replace('%', '').replace(',', '').replace('+', ''))
    except ValueError:
        return None


r1 = lambda x: None if x is None else round(x, 1)


def team_name(lg, ab):
    return (NBA_AB if lg == 'nba' else NFL_AB).get(fix_ab(lg, ab))


def stats(league, groups, stats_, filters=(), having=None, sort=None, limit=500):
    args = {'league': league, 'groups': groups, 'stats': stats_, 'filters': list(filters), 'limit': limit}
    if having:
        args['having'] = having
    if sort:
        args['sort'] = sort
    return rows(call('get_stats', **args))


# ---------- injuries ----------
def injuries(lg, week=None):
    """Same shape as espn.injuries: [{name, team, status, desc}] (+ practice for NFL). NFL keeps only the given week."""
    if lg == 'nba':
        out = []
        for x in rows(call('get_player_injuries', league='nba')):
            t = team_name('nba', x.get('team'))
            if t:
                out.append({'name': x.get('player_name', ''), 'team': t, 'status': x.get('status') or '', 'desc': x.get('description') or ''})
        return out
    out = []
    rs = rows(call('get_player_injuries', league='nfl', team_ids=NFL_TEAM_IDS))
    # StatMuse's report trails the schedule until the new week's practice reports come out (Wed/Thu),
    # so keep the latest week it has rather than requiring the slate's week.
    latest = max([x['week'] for x in rs if isinstance(x.get('week'), int)] or [None]) if rs else None
    for x in rs:
        if latest and x.get('week') not in (None, latest):
            continue
        gs, ps = x.get('game_status'), x.get('practice_status')
        if not gs and (ps or '').lower() in ('', 'full', 'full participation in practice'):
            continue
        t = team_name('nfl', x.get('team'))
        if not t:
            continue
        status = gs or ('DNP' if (ps or '').lower().startswith('did not') else ps)
        out.append({'name': x.get('player_name', ''), 'team': t, 'status': status, 'practice': ps,
                    'desc': ', '.join(v for v in (x.get('primary_issue'), x.get('secondary_issue')) if v)})
    return out


# ---------- NBA ----------
def nba_team_doc(season):
    """'nba' teams this season from StatMuse (season = ending year, e.g. 2027)."""
    rs = stats('nba', [{'type': 'team'}, {'type': 'seasonYear'}],
               ['GamesPlayed', 'Points', 'Rebounds', 'Assists', 'ThreePointsMade', 'OffensiveRating', 'DefensiveRating', 'PaceFactor',
                'OpponentPoints', 'OpponentRebounds', 'OpponentAssists', 'OpponentThreePointsMade'],
               [{'type': 'seasonYear', 'seasonYear': season}, {'type': 'seasonType', 'seasonType': 'RegularSeason'}], limit=100)
    teams = {}
    for x in rs:
        t = team_name('nba', x.get('team')); gp = num(x.get('GamesPlayed'))
        if not t or not gp:
            continue
        pg = lambda k: r1(num(x.get(k)) / gp) if num(x.get(k)) is not None else None
        teams[t] = {'gp': int(gp), 'off': {'pts': pg('Points'), 'reb': pg('Rebounds'), 'ast': pg('Assists'), 'fg3': pg('ThreePointsMade'),
                                           'ortg': r1(num(x.get('OffensiveRating'))), 'pace': r1(num(x.get('PaceFactor')))},
                    'def': {'pts': pg('OpponentPoints'), 'reb': pg('OpponentRebounds'), 'ast': pg('OpponentAssists'), 'fg3': pg('OpponentThreePointsMade'),
                            'drtg': r1(num(x.get('DefensiveRating')))}}
    return teams


def nba_players(season):
    """Season averages + last-5 role this season, same fields as stats.nba_players. Returns (players, gp by (name, team))."""
    base = [{'type': 'seasonYear', 'seasonYear': season}, {'type': 'seasonType', 'seasonType': 'RegularSeason'}, {'type': 'activeStatus', 'active': True}]
    having = [{'statKey': 'MinutesPlayedPerGame', 'operator': '>=', 'value': 8}]
    sort = {'type': 'stat', 'statKey': 'MinutesPlayedPerGame', 'direction': 'descending'}
    season_rows = stats('nba', [{'type': 'player'}], ['GamesPlayed', 'PointsPerGame', 'ReboundsPerGame', 'AssistsPerGame', 'ThreePointsMadePerGame', 'MinutesPlayedPerGame'],
                        base, having, sort, 450)
    last5 = {x.get('playerId'): x for x in stats('nba', [{'type': 'player'}],
                                                  ['GamesPlayed', 'GamesStarted', 'PointsPerGame', 'ReboundsPerGame', 'AssistsPerGame', 'ThreePointsMadePerGame', 'MinutesPlayedPerGame'],
                                                  base + [{'type': 'playerGameIndex', 'count': 5, 'direction': 'Last'}], having, sort, 450)}
    players, gp = [], {}
    for x in season_rows:
        team = team_name('nba', x.get('team'))
        if not team:  # TOT / blank
            continue
        l5 = last5.get(x.get('playerId')) or {}
        p = {'name': x.get('player'), 'team': team, 'gp': int(num(x.get('GamesPlayed')) or 0),
             'pts': num(x.get('PointsPerGame')), 'reb': num(x.get('ReboundsPerGame')), 'ast': num(x.get('AssistsPerGame')),
             'fg3': num(x.get('ThreePointsMadePerGame')), 'min': num(x.get('MinutesPlayedPerGame')),
             'pts5': num(l5.get('PointsPerGame')), 'reb5': num(l5.get('ReboundsPerGame')), 'ast5': num(l5.get('AssistsPerGame')),
             'fg35': num(l5.get('ThreePointsMadePerGame')), 'min5': num(l5.get('MinutesPlayedPerGame')),
             'gp5': num(l5.get('GamesPlayed')), 'gs5': num(l5.get('GamesStarted'))}
        players.append(p); gp[(p['name'], team)] = p['gp']
    return players, gp


# ---------- NFL ----------
def nfl_defense(season):
    """What each defense allows (StatMuse has no 3rd-down / red-zone rates: callers keep those from the box scores)."""
    rs = stats('nfl', [{'type': 'team'}, {'type': 'seasonYear'}],
               ['OpponentTotalYards', 'Passing-OpponentNetYards', 'Rushing-OpponentYards', 'OpponentPoints', 'OpponentPointsPerGame',
                'OpponentTouchdowns', 'Passing-OpponentTouchdowns', 'Rushing-OpponentTouchdowns'],
               [{'type': 'seasonYear', 'seasonYear': season}, {'type': 'seasonType', 'seasonType': 'RegularSeason'}], limit=40)
    teams = {}
    for x in rs:
        t = team_name('nfl', x.get('team')); ppg = num(x.get('OpponentPointsPerGame')); pts = num(x.get('OpponentPoints'))
        if not t or not ppg or pts is None:
            continue
        g = round(pts / ppg) or 1
        pg = lambda k: r1(num(x.get(k)) / g) if num(x.get(k)) is not None else None
        td = int(num(x.get('OpponentTouchdowns')) or 0)
        teams[t] = {'tot': pg('OpponentTotalYards'), 'totTd': td, 'pass': pg('Passing-OpponentNetYards'), 'passTd': int(num(x.get('Passing-OpponentTouchdowns')) or 0),
                    'rush': pg('Rushing-OpponentYards'), 'rushTd': int(num(x.get('Rushing-OpponentTouchdowns')) or 0), 'pts': r1(ppg), 'ptsTd': td}
    return teams


def nfl_players(season):
    """Per-game passing/rushing/receiving yards (2+ games) and the last 3 games, same fields as stats.nfl_players."""
    base = [{'type': 'seasonYear', 'seasonYear': season}, {'type': 'seasonType', 'seasonType': 'RegularSeason'}]
    st = ['GamesPlayed', 'Passing-YardsPerGame', 'Rushing-YardsPerGame', 'Receiving-YardsPerGame']
    sort = {'type': 'stat', 'statKey': 'Fantasy-HalfPointsPerReceptionPoints', 'direction': 'descending'}
    season_rows = stats('nfl', [{'type': 'player'}], st, base, [{'statKey': 'GamesPlayed', 'operator': '>=', 'value': 2}], sort, 300)
    last3 = {x.get('playerId'): x for x in stats('nfl', [{'type': 'player'}], st, base + [{'type': 'playerGameIndex', 'count': 3, 'direction': 'Last'}],
                                                  [{'statKey': 'GamesPlayed', 'operator': '>=', 'value': 1}], sort, 300)}
    z = lambda v: None if not v else v  # StatMuse shows 0.0 for a QB's receiving yards etc.: treat as no average
    out = []
    for x in season_rows:
        l3 = last3.get(x.get('playerId')) or {}
        out.append({'name': x.get('player'), 'team': team_name('nfl', x.get('team')), 'gp': int(num(x.get('GamesPlayed')) or 0),
                    'pass': z(num(x.get('Passing-YardsPerGame'))), 'rush': z(num(x.get('Rushing-YardsPerGame'))), 'rec': z(num(x.get('Receiving-YardsPerGame'))),
                    'pass3': z(num(l3.get('Passing-YardsPerGame'))), 'rush3': z(num(l3.get('Rushing-YardsPerGame'))), 'rec3': z(num(l3.get('Receiving-YardsPerGame'))),
                    'g3': num(l3.get('GamesPlayed'))})
    return out


def usage():
    """{used, max} from the last response's quota headers, if StatMuse sent them."""
    lim, rem = num(quota.get('limit')), num(quota.get('remaining'))
    if lim is None:
        return None
    return {'used': int(lim - (rem or 0)), 'max': int(lim), **({'resetsAt': quota['reset']} if quota.get('reset') else {})}
