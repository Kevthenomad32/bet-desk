"""Offline tests for the jobs (no network): a fake database, real exported docs when BETDESK_BACKUP points at a backup file."""
import copy
import datetime as dt
import json
import os
import re

import pytest

from jobs import data_jobs, espn, ledger_jobs, model_jobs, sgo, stats
from jobs.common import ET, iso, now_utc
from jobs.legs import grade, parse_label, stat_value
from jobs.run import due_now

BACKUP = os.environ.get('BETDESK_BACKUP')


class FakeDB:
    def __init__(self, docs=(), tables=None):
        self.docs = {(d['coll'], d['id']): copy.deepcopy(d['data']) for d in docs}
        self.tables = tables or {}
        self.runs = []

    def _k(self, path):
        i = path.rfind('/')
        return ('', path) if i < 0 else (path[:i], path[i + 1:])

    def get(self, path, default=None):
        return copy.deepcopy(self.docs.get(self._k(path), default))

    def set(self, path, data):
        self.docs[self._k(path)] = copy.deepcopy(data)

    def merge(self, path, patch):
        cur = self.docs.setdefault(self._k(path), {})
        for k, v in patch.items():
            if isinstance(v, dict) and v.get('__delete__'):
                cur.pop(k, None)
            else:
                cur[k] = v

    def list(self, coll):
        return [dict(copy.deepcopy(v), _id=i) for (c, i), v in self.docs.items() if c == coll]

    def upsert(self, table, rows, conflict):
        self.tables.setdefault(table, []).extend(rows)

    def select(self, table, **params):
        rows = self.tables.get(table, [])
        def ok(r):
            for k, v in params.items():
                if k in ('select', 'order', 'limit'):
                    continue
                if k == 'and':
                    m = re.match(r'\((\w+)\.lte\.(.+)\)', v); col, val = m.groups()
                    if str(r.get(col)) > val: return False
                    continue
                op, val = v.split('.', 1)
                x = r.get(k)
                if op == 'eq' and str(x) != val: return False
                if op == 'gte' and str(x) < val: return False
                if op == 'in' and str(x) not in val.strip('()').split(','): return False
            return True
        return [r for r in rows if ok(r)]

    def log_run(self, *a):
        self.runs.append(a)


def load_backup():
    if not BACKUP or not os.path.exists(BACKUP):
        pytest.skip('set BETDESK_BACKUP to a backup file')
    return json.load(open(BACKUP))['docs']


# ---------- legs ----------
@pytest.mark.parametrize('label,expect', [
    ('Darius Garland o24.5 pts', [('Darius Garland', 'over', 24.5, 'pts')]),
    ('Derrick white O8.5 Reb+Ast', [('Derrick white', 'over', 8.5, 'ra')]),
    ('Kevin Durant o31.5 P+R', [('Kevin Durant', 'over', 31.5, 'pr')]),
    ('Bam Adebayo u1.5 3pm', [('Bam Adebayo', 'under', 1.5, 'fg3')]),
    ('Cook o64.5 rush yds, Kraft o4.5 receptions, Jacobs anytime td', [('Cook', 'over', 64.5, 'rush'), ('Kraft', 'over', 4.5, 'recs'), ('Jacobs', 'over', 0.5, 'td')]),
])
def test_parse(label, expect):
    legs = parse_label(label)
    assert [(l['player'], l['side'], l['line'], l['market']) for l in legs] == expect


@pytest.mark.parametrize('label', ['Lakers/Portland Over 233.5', 'Cade Triple double', 'Suns team total o106.5 (PHX @ OKC)', '76ers ML (vs Pacers)'])
def test_parse_gives_up(label):
    assert parse_label(label) is None


def test_grade():
    s = {'pts': 14, 'reb': 3, 'ast': 5, 'fg3': 1, 'rushTd': 1}
    assert grade({'side': 'over', 'line': 24.5, 'market': 'pts'}, stat_value(s, 'pts')) == 'lost'
    assert grade({'side': 'under', 'line': 1.5, 'market': 'fg3'}, stat_value(s, 'fg3')) == 'won'
    assert stat_value(s, 'pra') == 22 and stat_value(s, 'ra') == 8
    assert grade({'side': 'over', 'line': 0.5, 'market': 'td'}, stat_value(s, 'td')) == 'won'
    assert stat_value({}, 'pts') is None  # DNP


# ---------- schedule ----------
def at(y, m, d, h):
    return dt.datetime(y, m, d, h, 31, tzinfo=ET)


def test_schedule():
    assert due_now(at(2026, 10, 28, 9)) == ['injuries', 'nba_daily', 'nfl_weekly', 'pregame']       # Wed 9:31
    assert due_now(at(2026, 10, 29, 9)) == ['injuries', 'nba_daily', 'nfl_lines', 'pregame']        # Thu
    assert 'recap' in due_now(at(2026, 11, 2, 9)) and 'recap' not in due_now(at(2026, 11, 3, 9))  # Mon, not Tue
    assert 'cfb_lines' in due_now(at(2026, 10, 30, 9))                                               # Fri
    assert due_now(at(2026, 10, 29, 0)) == ['settle'] and due_now(at(2026, 10, 29, 2)) == ['settle']
    assert due_now(at(2026, 10, 29, 4)) == ['closing']
    assert due_now(at(2026, 11, 1, 13)) == ['pregame', 'nbagl', 'live']                              # Sunday 1:31pm
    assert due_now(at(2026, 10, 29, 23)) == ['nbagl']
    assert due_now(at(2026, 10, 29, 6)) == []


# ---------- ESPN box parsing + stats ----------
NBA_SUM = {'header': {'competitions': [{'status': {'type': {'state': 'post', 'shortDetail': 'Final'}}, 'competitors': [
    {'homeAway': 'home', 'score': '110', 'team': {'abbreviation': 'IND'}, 'linescores': [{'displayValue': '30'}]},
    {'homeAway': 'away', 'score': '114', 'team': {'abbreviation': 'PHI'}, 'linescores': [{'displayValue': '28'}]}]}]},
    'boxscore': {'teams': [
        {'team': {'abbreviation': 'IND'}, 'statistics': [{'name': 'fieldGoalsMade-fieldGoalsAttempted', 'displayValue': '40-88'}, {'name': 'freeThrowsMade-freeThrowsAttempted', 'displayValue': '18-22'},
                                                           {'name': 'offensiveRebounds', 'displayValue': '10'}, {'name': 'totalTurnovers', 'displayValue': '14'}, {'name': 'totalRebounds', 'displayValue': '44'},
                                                           {'name': 'assists', 'displayValue': '25'}, {'name': 'threePointFieldGoalsMade-threePointFieldGoalsAttempted', 'displayValue': '12-35'}]},
        {'team': {'abbreviation': 'PHI'}, 'statistics': [{'name': 'fieldGoalsMade-fieldGoalsAttempted', 'displayValue': '42-90'}, {'name': 'freeThrowsMade-freeThrowsAttempted', 'displayValue': '16-20'},
                                                           {'name': 'offensiveRebounds', 'displayValue': '12'}, {'name': 'totalTurnovers', 'displayValue': '11'}, {'name': 'totalRebounds', 'displayValue': '47'},
                                                           {'name': 'assists', 'displayValue': '27'}, {'name': 'threePointFieldGoalsMade-threePointFieldGoalsAttempted', 'displayValue': '14-38'}]}],
        'players': [{'team': {'abbreviation': 'IND'}, 'statistics': [{'keys': ['minutes', 'points', 'fieldGoalsMade-fieldGoalsAttempted', 'threePointFieldGoalsMade-threePointFieldGoalsAttempted',
                                                                                'freeThrowsMade-freeThrowsAttempted', 'rebounds', 'assists', 'turnovers', 'offensiveRebounds'],
                                                                       'athletes': [{'athlete': {'id': '10', 'displayName': 'Pascal Siakam'}, 'starter': True, 'stats': ['36', '27', '10-18', '2-5', '5-6', '9', '4', '2', '2']},
                                                                                    {'athlete': {'id': '11', 'displayName': 'Tyrese Haliburton'}, 'didNotPlay': True, 'stats': []}]}]},
                    {'team': {'abbreviation': 'PHI'}, 'statistics': [{'keys': ['minutes', 'points', 'threePointFieldGoalsMade-threePointFieldGoalsAttempted', 'rebounds', 'assists'],
                                                                       'athletes': [{'athlete': {'id': '20', 'displayName': 'Tyrese Maxey'}, 'starter': True, 'stats': ['38', '31', '4-9', '5', '6']}]}]}]}}


def test_box_and_archive(monkeypatch):
    monkeypatch.setattr(espn, 'get', lambda path, **kw: NBA_SUM)
    b = espn.box('nba', '1')
    sk = next(p for p in b['players'] if p['name'] == 'Pascal Siakam')
    assert b['state'] == 'final' and sk['side'] == 'home' and sk['stats']['pts'] == 27 and sk['stats']['fg3'] == 2 and sk['stats']['fga'] == 18
    assert next(p for p in b['players'] if p['name'] == 'Tyrese Haliburton')['dnp']
    db = FakeDB()
    g = {'id': '1', 'league': 'nba', 'start': '2026-10-28T23:00Z', 'date': '2026-10-28', 'state': 'final', 'season': 2027, 'season_type': 2, 'week': None,
         'home_ab': 'IND', 'away_ab': 'PHI', 'home': 'Pacers', 'away': '76ers', 'home_score': 110, 'away_score': 114}
    assert espn.archive_games(db, 'nba', [g]) == 1
    pg = db.tables['player_games']
    assert {r['name'] for r in pg} == {'Pascal Siakam', 'Tyrese Haliburton', 'Tyrese Maxey'}
    assert next(r for r in pg if r['name'] == 'Tyrese Maxey')['opp_ab'] == 'IND'
    teams = stats.nba_team_doc(db.tables['games'])
    assert teams['Pacers']['off']['pts'] == 110 and teams['Pacers']['def']['pts'] == 114 and teams['76ers']['off']['fg3'] == 14
    assert 95 < teams['Pacers']['off']['pace'] < 110 and 100 < teams['76ers']['off']['ortg'] < 130
    players, gp = stats.nba_players(pg)
    assert {p['name'] for p in players} == {'Pascal Siakam', 'Tyrese Maxey'} and gp[('Tyrese Haliburton', 'Pacers')] == 0


def test_nfl_defense():
    box = {'teams': {'home': {'stats': {'netPassingYards': '250', 'rushingYards': '100', 'totalYards': '350', 'thirdDownEff': '5-12', 'redZoneAttempts': '2-4'}},
                     'away': {'stats': {'netPassingYards': '180', 'rushingYards': '140', 'totalYards': '320', 'thirdDownEff': '4-10', 'redZoneAttempts': '3-3'}}},
           'players': [{'side': 'home', 'stats': {'passTd': 2}}, {'side': 'away', 'stats': {'rushTd': 1, 'passTd': 1}}]}
    teams, wk = stats.nfl_defense_doc([{'home': 'Packers', 'away': 'Panthers', 'home_score': 24, 'away_score': 17, 'week': 8, 'box': box}])
    assert wk == 8
    assert teams['Panthers'] == {'tot': 350.0, 'totTd': 2, 'pass': 250.0, 'passTd': 2, 'rush': 100.0, 'rushTd': 0, 'pts': 24.0, 'ptsTd': 2, 'third': 41.7, 'rz': 50.0}
    assert teams['Packers']['rz'] == 100.0 and teams['Packers']['pts'] == 17.0


# ---------- model runs on the real exported docs ----------
def shifted(docs, lg):
    """Move one game in the odds doc into the next pre-game window so the model has something due."""
    a, b = sgo.window('pre')
    start = iso(dt.datetime.fromisoformat(a.replace('Z', '+00:00')) + dt.timedelta(minutes=30))
    for d in docs:
        if d['coll'] == '' and d['id'] == f'{lg}Odds':
            d['data']['games'][0]['start'] = start
            return d['data']['games'][0]


def test_pregame_logs_shadow(monkeypatch):
    docs = copy.deepcopy(load_backup())
    g = shifted(docs, 'nba')
    db = FakeDB([d for d in docs if not (d['coll'] == 'ledger/shadow')])
    monkeypatch.setattr(sgo, 'fetch', lambda *a, **k: [])
    inj = next(d['data'] for d in docs if d['id'] == 'nbaInjuries')
    monkeypatch.setattr(espn, 'injuries', lambda lg: inj['players'] if lg == 'nba' else [])
    msg = model_jobs.pregame(db)
    sh = db.list('ledger/shadow')
    assert len(sh) == 1 and sh[0]['game'] == f"{g['away']} @ {g['home']}" and len(sh[0]['rows']) > 10, msg
    r = sh[0]['rows'][0]
    assert 0 < r['pModel'] < 1 and 0 < r['pBook'] < 1 and r['comps']['lg'] == 'nba'
    assert not db.list('ledger/picks')  # locking paused


def test_nbagl_locks(monkeypatch):
    docs = copy.deepcopy(load_backup())
    g = shifted(docs, 'nba')
    db = FakeDB([d for d in docs if d['coll'] != 'ledger/nbagl'])
    msg = model_jobs.nbagl(db)
    calls = db.list('ledger/nbagl')
    assert len(calls) == 1, msg
    c = calls[0]['calls']
    assert set(c) <= {'spread', 'total'} and all(0.01 <= x['pWin'] <= 0.99 for x in c.values())


# ---------- settle ----------
def test_settle_picks_and_bets(monkeypatch):
    monkeypatch.setattr(data_jobs, 'archive_range', lambda *a, **k: 0)
    monkeypatch.setattr(ledger_jobs, 'archive_range', lambda *a, **k: 0)
    monkeypatch.setattr(ledger_jobs, 'claude', lambda *a, **k: {'bets': [{'id': 'b3', 'status': 'won', 'sport': 'NBA'}]})
    monkeypatch.setattr(espn, 'scoreboard', lambda *a, **k: [])
    day = (now_utc() - dt.timedelta(days=1)).astimezone(ET).strftime('%Y-%m-%d')
    games = [{'league': 'nba', 'id': 'g1', 'date': day, 'home': 'Pacers', 'away': '76ers', 'home_score': 110, 'away_score': 114, 'status': 'final'}]
    pg = [{'league': 'nba', 'game_id': 'g1', 'player_id': '10', 'name': 'Pascal Siakam', 'name_norm': 'pascal siakam', 'team': 'Pacers', 'opp': '76ers', 'date': day,
           'dnp': False, 'stats': {'pts': 27, 'reb': 9, 'ast': 4, 'fg3': 2, 'min': 36}},
          {'league': 'nba', 'game_id': 'g1', 'player_id': '11', 'name': 'Tyrese Haliburton', 'name_norm': 'tyrese haliburton', 'team': 'Pacers', 'opp': '76ers', 'date': day,
           'dnp': True, 'stats': {}}]
    start = iso(dt.datetime.fromisoformat(day).replace(hour=23, tzinfo=dt.timezone.utc))
    docs = [{'coll': 'ledger/picks', 'id': 'p1', 'data': {'league': 'NBA', 'player': 'Pascal Siakam', 'team': 'Pacers', 'market': 'pra', 'side': 'over', 'line': 35.5, 'gameStart': start, 'status': 'open'}},
            {'coll': 'ledger/picks', 'id': 'p2', 'data': {'league': 'NBA', 'player': 'Tyrese Haliburton', 'team': 'Pacers', 'market': 'pts', 'side': 'over', 'line': 18.5, 'gameStart': start, 'status': 'open'}},
            {'coll': 'ledger/bets', 'id': 'b1', 'data': {'label': 'Pascal Siakam o24.5 pts, Haliburton o18.5 pts', 'legs': 2, 'date': day, 'status': 'open', 'stake': 25, 'odds': 250}},
            {'coll': 'ledger/bets', 'id': 'b2', 'data': {'label': 'Siakam u24.5 pts, Some Guy o3.5 reb', 'legs': 2, 'date': day, 'status': 'open', 'stake': 25, 'odds': 250, 'note': 'x'}},
            {'coll': 'ledger/bets', 'id': 'b3', 'data': {'label': '76ers ML (vs Pacers)', 'legs': 1, 'date': day, 'status': 'open', 'stake': 25, 'odds': 220}},
            {'coll': 'ledger/shadow', 'id': 's1', 'data': {'league': 'NBA', 'game': '76ers @ Pacers', 'gameStart': start, 'graded': False,
                                                            'rows': [{'player': 'Pascal Siakam', 'market': 'reb', 'line': 8.5}]}}]
    db = FakeDB(docs, {'games': games, 'player_games': pg})
    out = ledger_jobs.settle(db)
    p1, p2 = db.get('ledger/picks/p1'), db.get('ledger/picks/p2')
    assert p1['status'] == 'won' and p1['result'] == 40
    assert p2['status'] == 'push' and p2['note'] == 'Did not play'
    b1, b2, b3 = db.get('ledger/bets/b1'), db.get('ledger/bets/b2'), db.get('ledger/bets/b3')
    assert b1['status'] == 'open' and 'pushed/voided' in b1['note']               # Siakam won, Haliburton DNP -> void leg
    assert b2['status'] == 'lost' and b2['lostLeg'] == 'Siakam u24.5 pts (27)' and 'note' not in b2   # lost even with an unknown leg
    assert b3['status'] == 'won' and b3['sport'] == 'NBA'                          # from Claude
    s1 = db.get('ledger/shadow/s1')
    assert s1['graded'] and s1['rows'][0]['result'] == 9
    assert 'bets settled: 2' in out


def test_recap(monkeypatch):
    monkeypatch.setattr(ledger_jobs, 'notify', lambda *a: None)
    t = now_utc().astimezone(ET).date(); last_mon = t - dt.timedelta(days=t.weekday() + 7)
    docs = [{'coll': 'ledger/bets', 'id': 'a', 'data': {'date': last_mon.isoformat(), 'status': 'won', 'stake': 50, 'odds': -110, 'label': 'X o1.5 reb', 'sport': 'NBA'}},
            {'coll': 'ledger/bets', 'id': 'b', 'data': {'date': last_mon.isoformat(), 'status': 'lost', 'stake': 25, 'odds': 120, 'label': 'Y o9.5 ast', 'sport': 'NBA'}},
            {'coll': '', 'id': 'settings', 'data': {'unit': 50}}]
    db = FakeDB(docs)
    head = ledger_jobs.recap(db)
    assert head.startswith('You: 1–1, +$20.45') and db.get('recap')['week']['start'] == last_mon.isoformat()


# ---------- closing lines ----------
def test_closing(monkeypatch):
    def ev(pid, name, stat, close, odds='-110'):
        return {f'{stat}-{pid}-game-ou-over': {'betTypeID': 'ou', 'sideID': 'over', 'periodID': 'game', 'playerID': pid, 'statID': stat, 'closeBookOverUnder': close,
                                                'closeBookOdds': odds, 'opposingOddID': f'{stat}-{pid}-game-ou-under'},
                f'{stat}-{pid}-game-ou-under': {'closeBookOdds': '-110'}}
    odds = {**ev('P1', 'Pascal Siakam', 'points', '25.5'),
            'points-all-game-ou-over': {'betTypeID': 'ou', 'sideID': 'over', 'periodID': 'game', 'statEntityID': 'all', 'statID': 'points', 'closeBookOverUnder': '236.5'}}
    e = {'leagueID': 'NBA', 'status': {'started': True}, 'teams': {'home': {'names': {'medium': 'Pacers'}}, 'away': {'names': {'medium': '76ers'}}},
         'players': {'P1': {'name': 'Pascal Siakam'}}, 'odds': odds}
    monkeypatch.setattr(sgo, 'fetch', lambda L, *a, **k: [e] if L == 'NBA' else [])
    monkeypatch.setattr(sgo, 'usage', lambda: {'used': 100, 'max': 2500})
    day = now_utc().astimezone(ET).strftime('%Y-%m-%d')
    start = iso(now_utc() - dt.timedelta(hours=6))
    db = FakeDB([{'coll': 'ledger/picks', 'id': 'p', 'data': {'league': 'NBA', 'player': 'Pascal Siakam', 'market': 'pts', 'side': 'over', 'line': 24.5, 'odds': '-110', 'gameStart': start}},
                 {'coll': 'ledger/bets', 'id': 'b1', 'data': {'label': 'Siakam o24.5 pts', 'legs': 1, 'date': day, 'sport': 'NBA'}},
                 {'coll': 'ledger/bets', 'id': 'b2', 'data': {'label': '76ers/Pacers Under 238.5', 'legs': 1, 'date': day}}])
    out = ledger_jobs.closing(db)
    assert db.get('ledger/picks/p')['clv'] == 1.0
    assert db.get('ledger/bets/b1')['clv'] == 1.0 and db.get('ledger/bets/b2')['clv'] == 2.0
    assert db.get('status')['sgo']['used'] == 100, out


# ---------- Sunday live ----------
def test_live(monkeypatch):
    monkeypatch.setattr(ledger_jobs, 'notify', lambda *a: sent.append(a))
    sent = []
    monkeypatch.setattr(espn, 'scoreboard', lambda lg, d: [{'id': '9', 'state': 'live'}] if lg == 'nfl' else [])
    monkeypatch.setattr(espn, 'box', lambda lg, i: {'state': 'live', 'detail': 'Q3 6:12', 'players': [
        {'name': 'Josh Jacobs', 'stats': {'rush': 71, 'rushTd': 1}}, {'name': 'Tucker Kraft', 'stats': {'rec': 30, 'recs': 3}}]})
    db = FakeDB([{'coll': 'ledger/bets', 'id': 'b', 'data': {'label': 'Jacobs o64.5 rush yds, Kraft o44.5 rec yds', 'legs': 2, 'status': 'open', 'odds': 260}}])
    ledger_jobs.live(db)
    b = db.get('ledger/bets/b')
    assert b['live'] == 'Jacobs ✓ 71 · Kraft 30/44.5 (Q3 6:12)' and b['status'] == 'open' and b['alertedOneLeg'] and sent
