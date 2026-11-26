"""Mismatch alert: top-10 offense vs bottom-10 defense, either direction, once per game."""
from jobs import matchups as M


def teams():
    # team i: offense rating falls with i (T0 best offense), defense rating rises with i (T29 worst defense)
    return {f'T{i}': {'off': {'ortg': 120 - i}, 'def': {'drtg': 100 + i}} for i in range(30)}


class DB(dict):
    def get(self, k): return super().get(k)
    def set(self, k, v): self[k] = v


def test_flags_top_offense_vs_worst_defense_both_ways():
    T = teams()
    games = [{'away': 'T0', 'home': 'T29', 'time': '7:30pm'},   # #1 offense at #30 defense
             {'away': 'T25', 'home': 'T3', 'time': '8:00pm'},   # home #4 offense vs visiting #26 defense
             {'away': 'T12', 'home': 'T15', 'time': '9:00pm'}]  # nothing special
    got = [(o, d) for o, d, _ in M.mismatches(T, games)]
    assert got == [('T0', 'T29'), ('T3', 'T25')]
    assert '#1 offense' in M.line(T, 'T0', 'T29', games[0]) and '#30 defense' in M.line(T, 'T0', 'T29', games[0])


def test_alerts_once_per_game(monkeypatch):
    sent = []
    monkeypatch.setattr(M, 'notify', lambda title, text: sent.append((title, text)))
    db, T = DB(), teams()
    games = [{'away': 'T0', 'home': 'T29', 'time': '7:30pm'}]
    assert 'alerted' in M.alert(db, T, games, '2026-11-27')
    M.alert(db, T, games, '2026-11-27')   # backfill reruns nba_daily: no second alert
    assert len(sent) == 1 and sent[0][0] == 'Mismatch tonight: 1 game'
    assert db['mismatches']['rows'][0]['offense'] == 'T0'
    M.alert(db, T, games, '2026-11-28')   # same teams, another day: alerts again
    assert len(sent) == 2
