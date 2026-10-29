"""Model inputs computed from the games archive (replaces the StatMuse get_stats calls)."""
from collections import defaultdict

from .common import NBA_AB, NFL_AB, now_et

r1 = lambda x: None if x is None else round(x, 1)


def _f(v):
    try:
        return float(str(v).split('-')[0])
    except (TypeError, ValueError):
        return None


def _ma(v):
    try:
        a, b = str(v).replace('/', '-').split('-')[:2]
        return float(a), float(b)
    except (TypeError, ValueError):
        return None, None


# ---------- NBA ----------
def nba_side_totals(box, side):
    t = (box.get('teams') or {}).get(side) or {}
    s = t.get('stats') or {}
    ps = [p for p in box.get('players') or [] if p.get('side') == side and not p.get('dnp')]
    tot = lambda k: sum((p['stats'].get(k) or 0) for p in ps)
    fgm, fga = _ma(s.get('fieldGoalsMade-fieldGoalsAttempted'))
    _, fta = _ma(s.get('freeThrowsMade-freeThrowsAttempted'))
    fg3, _ = _ma(s.get('threePointFieldGoalsMade-threePointFieldGoalsAttempted'))
    return {'pts': t.get('score') if t.get('score') is not None else tot('pts'),
            'reb': _f(s.get('totalRebounds')) if s.get('totalRebounds') is not None else tot('reb'),
            'ast': _f(s.get('assists')) if s.get('assists') is not None else tot('ast'),
            'fg3': fg3 if fg3 is not None else tot('fg3'),
            'fga': fga if fga is not None else tot('fga'), 'fta': fta if fta is not None else tot('fta'),
            'oreb': _f(s.get('offensiveRebounds')) if s.get('offensiveRebounds') is not None else tot('oreb'),
            'tov': _f(s.get('totalTurnovers') or s.get('turnovers')) if (s.get('totalTurnovers') or s.get('turnovers')) is not None else tot('tov'),
            'min': tot('min') or 240}


def nba_team_doc(games):
    """'nba' doc: per team this season {gp, off:{pts,reb,ast,fg3,ortg,pace}, def:{pts,reb,ast,fg3,drtg}}."""
    acc = defaultdict(lambda: defaultdict(float))
    for g in games:
        b = g.get('box') or {}
        H, A = nba_side_totals(b, 'home'), nba_side_totals(b, 'away')
        poss = lambda x: x['fga'] + 0.44 * x['fta'] - x['oreb'] + x['tov']
        pav = (poss(H) + poss(A)) / 2
        mins = max(H['min'], 240) / 5
        for me, op, team in ((H, A, g['home']), (A, H, g['away'])):
            if not team:
                continue
            a = acc[team]
            a['gp'] += 1; a['poss'] += pav; a['pace'] += pav * 48 / mins
            for k in ('pts', 'reb', 'ast', 'fg3'):
                a['o_' + k] += me[k]; a['d_' + k] += op[k]
    teams = {}
    for t, a in acc.items():
        n = a['gp']
        teams[t] = {'gp': int(n), 'off': {'pts': r1(a['o_pts'] / n), 'reb': r1(a['o_reb'] / n), 'ast': r1(a['o_ast'] / n), 'fg3': r1(a['o_fg3'] / n),
                                          'ortg': r1(100 * a['o_pts'] / a['poss']), 'pace': r1(a['pace'] / n)},
                    'def': {'pts': r1(a['d_pts'] / n), 'reb': r1(a['d_reb'] / n), 'ast': r1(a['d_ast'] / n), 'fg3': r1(a['d_fg3'] / n),
                            'drtg': r1(100 * a['d_pts'] / a['poss'])}}
    return teams


def nba_players(rows, min_avg=8):
    """nbaPlayers rows: season averages + last-5 role, from player_games rows (this season). Also returns games played by player."""
    by = defaultdict(list)
    for r in rows:
        by[r['player_id']].append(r)
    players, gp = [], {}
    for pid, rs in by.items():
        rs.sort(key=lambda r: r['date'])
        played = [r for r in rs if not r.get('dnp') and (r['stats'].get('min') or 0) > 0]
        name, team = rs[-1]['name'], rs[-1]['team']
        gp[(name, team)] = len(played)
        if not played:
            continue
        avg = lambda xs, k: sum((x['stats'].get(k) or 0) for x in xs) / len(xs)
        if avg(played, 'min') < min_avg:
            continue
        last = played[-5:]
        players.append({'name': name, 'team': team, 'gp': len(played),
                        'pts': r1(avg(played, 'pts')), 'reb': r1(avg(played, 'reb')), 'ast': r1(avg(played, 'ast')), 'fg3': r1(avg(played, 'fg3')), 'min': r1(avg(played, 'min')),
                        'pts5': r1(avg(last, 'pts')), 'reb5': r1(avg(last, 'reb')), 'ast5': r1(avg(last, 'ast')), 'fg35': r1(avg(last, 'fg3')), 'min5': r1(avg(last, 'min')),
                        'gp5': len(last), 'gs5': sum(1 for x in last if x.get('starter'))})
    return players, gp


def nba_pool(players, gp, teams_today, injuries):
    """Injury pool: every rotation player on a team playing today, plus injured Out/Doubtful players on those teams (with games played this season)."""
    pool, seen = [], set()
    look = {p['name']: p for p in players}
    for p in players:
        if p['team'] in teams_today:
            pool.append({'name': p['name'], 'team': p['team'], 'min': p['min'], 'pts': p['pts'], 'reb': p['reb'], 'ast': p['ast'], 'fg3': p['fg3'], 'gpc': p['gp']})
            seen.add((p['name'], p['team']))
    for x in injuries:
        if x.get('team') not in teams_today or not str(x.get('status', '')).lower().startswith(('out', 'doubt')):
            continue
        p = look.get(x['name'])
        if p and (p['name'], x['team']) not in seen:
            pool.append({'name': p['name'], 'team': x['team'], 'min': p['min'], 'pts': p['pts'], 'reb': p['reb'], 'ast': p['ast'], 'fg3': p['fg3'], 'gpc': p['gp']})
            seen.add((p['name'], x['team']))
    return pool


# ---------- NFL ----------
def nfl_defense_doc(games):
    """'defense' teams: what each defense allows per game (pass = net passing yards), TDs allowed, 3rd-down and red-zone rates."""
    acc = defaultdict(lambda: defaultdict(float))
    week = 0
    for g in games:
        b = g.get('box') or {}
        week = max(week, g.get('week') or 0)
        for side, team, opp_side in (('home', g['home'], 'away'), ('away', g['away'], 'home')):
            if not team:
                continue
            o = ((b.get('teams') or {}).get(opp_side) or {})
            s = o.get('stats') or {}
            ps = [p for p in b.get('players') or [] if p.get('side') == opp_side]
            tot = lambda k: sum((p['stats'].get(k) or 0) for p in ps)
            a = acc[team]
            a['gp'] += 1
            a['pts'] += (g['away_score'] if side == 'home' else g['home_score']) or 0
            a['pass'] += _f(s.get('netPassingYards')) if s.get('netPassingYards') is not None else tot('pass')
            a['rush'] += _f(s.get('rushingYards')) if s.get('rushingYards') is not None else tot('rush')
            a['tot'] += _f(s.get('totalYards')) if s.get('totalYards') is not None else tot('pass') + tot('rush')
            a['passTd'] += tot('passTd'); a['rushTd'] += tot('rushTd')
            m, at = _ma(s.get('thirdDownEff'))
            if at:
                a['3m'] += m; a['3a'] += at
            m, at = _ma(s.get('redZoneAttempts'))
            if at:
                a['rzm'] += m; a['rza'] += at
    teams = {}
    for t, a in acc.items():
        n = a['gp']; td = a['passTd'] + a['rushTd']
        teams[t] = {'tot': r1(a['tot'] / n), 'totTd': int(td), 'pass': r1(a['pass'] / n), 'passTd': int(a['passTd']), 'rush': r1(a['rush'] / n),
                    'rushTd': int(a['rushTd']), 'pts': r1(a['pts'] / n), 'ptsTd': int(td),
                    'third': r1(100 * a['3m'] / a['3a']) if a['3a'] else None, 'rz': r1(100 * a['rzm'] / a['rza']) if a['rza'] else None}
    return teams, week


def nfl_players(rows):
    """nflPlayers rows: per-game passing/rushing/receiving yards (2+ games) and the last 3 games."""
    by = defaultdict(list)
    for r in rows:
        by[r['player_id']].append(r)
    out = []
    for pid, rs in by.items():
        rs.sort(key=lambda r: r['date'])
        if len(rs) < 2:
            continue
        per = lambda xs, k: (sum((x['stats'].get(k) or 0) for x in xs) / len(xs)) or None
        last = rs[-3:]
        out.append({'name': rs[-1]['name'], 'team': rs[-1]['team'], 'gp': len(rs),
                    'pass': r1(per(rs, 'pass')), 'rush': r1(per(rs, 'rush')), 'rec': r1(per(rs, 'rec')),
                    'pass3': r1(per(last, 'pass')), 'rush3': r1(per(last, 'rush')), 'rec3': r1(per(last, 'rec')), 'g3': len(last)})
    return out


def season_years():
    """(NBA season year as ESPN numbers it = ending year, NFL season year)."""
    t = now_et()
    nba = t.year + 1 if t.month >= 8 else t.year
    nfl = t.year if t.month >= 3 else t.year - 1
    return nba, nfl


ALL = {'nba': set(NBA_AB.values()), 'nfl': set(NFL_AB.values())}
