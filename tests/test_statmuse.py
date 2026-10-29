"""StatMuse client + builders, against canned answers shaped like the real API (Oct 2026)."""
import json
import os

import pytest

from jobs import statmuse as sm

INJ_NBA = {"columns": ["player_id", "player_name", "team_id", "team", "status", "description"], "rows": [[30395, "Devin Carter", 1, "BOS", "Out", "Illness"]]}
INJ_NFL = {"columns": ["player_id", "player_name", "team_id", "team", "week", "season_type", "game_status", "practice_status", "primary_issue", "secondary_issue"],
           "rows": [[1, "Keyshaun Elliott", 4, "CHI", 8, "Regular Season", None, "Full", "Quadricep", None],
                    [2, "Kyle Monangai", 4, "CHI", 8, "Regular Season", "Out", "Did Not Practice", "Thumb", "Toe"],
                    [3, "Jack Sanborn", 4, "CHI", 8, "Regular Season", None, "Did Not Practice", "Concussion", None],
                    [4, "Old Week", 4, "CHI", 7, "Regular Season", "Out", "Did Not Practice", "Knee", None]]}
TEAMS = {"columns": ["teamId", "team", "season", "GamesPlayed", "Points", "Rebounds", "Assists", "ThreePointsMade", "OffensiveRating", "DefensiveRating", "PaceFactor",
                     "OpponentPoints", "OpponentRebounds", "OpponentAssists", "OpponentThreePointsMade"],
         "rows": [[1, "BOS", "2026-27", "4", "479", "177", "111", "64", "118.0", "110.1", "98.89", "447", "181", "100", "60"]]}
SEASON = {"columns": ["playerId", "player", "teamId", "team", "GamesPlayed", "PointsPerGame", "ReboundsPerGame", "AssistsPerGame", "ThreePointsMadePerGame", "MinutesPlayedPerGame"],
          "rows": [[10164, "Tyrese Maxey", 21, "PHI", "5", "28.4", "4.2", "4.6", "2.4", "37.6"], [1, "Traded Guy", 0, "TOT", "5", "9", "1", "1", "1", "20"]]}
LAST5 = {"columns": ["playerId", "player", "GamesPlayed", "GamesStarted", "PointsPerGame", "ReboundsPerGame", "AssistsPerGame", "ThreePointsMadePerGame", "MinutesPlayedPerGame"],
         "rows": [[10164, "Tyrese Maxey", "5", "5", "28.4", "4.2", "4.6", "2.4", "37.5"]]}
DEF = {"columns": ["teamId", "team", "season", "OpponentTotalYards", "Passing-OpponentNetYards", "Rushing-OpponentYards", "OpponentPoints", "OpponentPointsPerGame",
                   "OpponentTouchdowns", "Passing-OpponentTouchdowns", "Rushing-OpponentTouchdowns"],
       "rows": [[4, "CHI", "2026", "2194", "1581", "613", "143", "20.4", "17", "11", "6"]]}
NFLP = {"columns": ["playerId", "player", "teamId", "team", "GamesPlayed", "Passing-YardsPerGame", "Rushing-YardsPerGame", "Receiving-YardsPerGame"],
        "rows": [[31072, "Drake Maye", 70, "NE", "7", "280.1", "30.0", "0.0"]]}
NFL3 = {"columns": ["playerId", "player", "GamesPlayed", "Passing-YardsPerGame", "Rushing-YardsPerGame", "Receiving-YardsPerGame"],
        "rows": [[31072, "Drake Maye", "3", "310.0", "25.0", "0.0"]]}


class Resp:
    def __init__(self, body, sse=False, status=200, headers=None):
        self.status_code = status
        self.headers = {'content-type': 'text/event-stream' if sse else 'application/json', **(headers or {})}
        self.text = ('event: message\ndata: ' + json.dumps(body) + '\n\n') if sse else json.dumps(body)

    def json(self):
        return json.loads(self.text)


@pytest.fixture
def fake(monkeypatch):
    os.environ['STATMUSE_API_KEY'] = 'test-key'
    sm._session.update(id=None, ready=False)
    seen = []

    def post(url, headers=None, json=None, timeout=None):
        seen.append((headers, json))
        m = json.get('method')
        if m == 'initialize':
            return Resp({'jsonrpc': '2.0', 'id': json['id'], 'result': {}}, headers={'mcp-session-id': 'sess-1'})
        if m != 'tools/call':
            return Resp({}, status=202)
        name, a = json['params']['name'], json['params']['arguments']
        if name == 'get_player_injuries':
            ans = INJ_NBA if a['league'] == 'nba' else INJ_NFL
        elif a['league'] == 'nba':
            ans = TEAMS if a['groups'][0]['type'] == 'team' else (LAST5 if any(f['type'] == 'playerGameIndex' for f in a['filters']) else SEASON)
        else:
            ans = DEF if a['groups'][0]['type'] == 'team' else (NFL3 if any(f['type'] == 'playerGameIndex' for f in a['filters']) else NFLP)
        body = {'jsonrpc': '2.0', 'id': json['id'], 'result': {'content': [{'type': 'text', 'text': __import__('json').dumps(ans)}]}}
        return Resp(body, sse=(name == 'get_stats'))
    monkeypatch.setattr(sm._S, 'post', post)
    yield seen
    os.environ.pop('STATMUSE_API_KEY', None)


def test_session_and_key_header(fake):
    sm.injuries('nba')
    init, call = fake[0], fake[-1]
    assert init[1]['method'] == 'initialize' and init[0]['x-statmuse-api-key'] == 'test-key'
    assert call[0]['mcp-session-id'] == 'sess-1' and call[1]['params']['name'] == 'get_player_injuries'


def test_injuries(fake):
    assert sm.injuries('nba') == [{'name': 'Devin Carter', 'team': 'Celtics', 'status': 'Out', 'desc': 'Illness'}]
    nfl = sm.injuries('nfl', week=8)
    assert [(x['name'], x['status']) for x in nfl] == [('Kyle Monangai', 'Out'), ('Jack Sanborn', 'DNP')]  # full-practice and old-week rows dropped
    assert nfl[0]['desc'] == 'Thumb, Toe' and nfl[0]['team'] == 'Bears'


def test_nba_builders(fake):
    t = sm.nba_team_doc(2027)['Celtics']
    assert t['gp'] == 4 and t['off']['pts'] == 119.8 and t['off']['pace'] == 98.9 and t['def']['drtg'] == 110.1 and t['def']['fg3'] == 15.0
    players, gp = sm.nba_players(2027)
    assert len(players) == 1 and players[0]['team'] == '76ers' and players[0]['min5'] == 37.5 and players[0]['gs5'] == 5 and gp[('Tyrese Maxey', '76ers')] == 5


def test_nfl_builders(fake):
    d = sm.nfl_defense(2026)['Bears']
    assert d['pts'] == 20.4 and d['pass'] == 225.9 and d['passTd'] == 11 and d['totTd'] == 17
    p = sm.nfl_players(2026)[0]
    assert p['team'] == 'Patriots' and p['pass'] == 280.1 and p['rec'] is None and p['pass3'] == 310.0 and p['g3'] == 3


def test_disabled_without_key():
    os.environ.pop('STATMUSE_API_KEY', None)
    assert not sm.enabled()
    with pytest.raises(sm.StatMuseError):
        sm.call('get_stats')


def test_errors_are_clear(monkeypatch):
    os.environ['STATMUSE_API_KEY'] = 'bad'
    sm._session.update(id=None, ready=False)
    monkeypatch.setattr(sm._S, 'post', lambda *a, **k: Resp({'error': 'nope'}, status=401))
    with pytest.raises(sm.StatMuseError, match='refused the API key'):
        sm.injuries('nba')
    os.environ.pop('STATMUSE_API_KEY', None)
