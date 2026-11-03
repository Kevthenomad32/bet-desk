"""Value finder (Hard Rock vs Pinnacle fair) and closing line value on Kev's bets."""
import datetime as dt

from jobs import value as V

NOW = dt.datetime(2026, 11, 3, 20, 0, tzinfo=dt.timezone.utc)
DOC = {'source': 'TheRundown: Hard Rock + Pinnacle',
       'games': [{'away': 'Celtics', 'home': 'Knicks', 'start': '2026-11-04T00:30:00Z',
                  'spread': {'home': -3.5, 'homeOdds': '+108', 'awayOdds': '-120', 'pinHome': -3.5, 'pinHomeOdds': '-104', 'pinAwayOdds': '-106'},
                  'total': {'line': 221.5, 'overOdds': '-110', 'underOdds': '-110', 'pinLine': 222.5, 'pinOver': '-105', 'pinUnder': '-105'}, 'ml': {}}],
       'props': [{'player': 'Jalen Brunson', 'team': 'Knicks', 'market': 'pts', 'line': 26.5, 'odds': '+105', 'uodds': '-135', 'pinO': '-110', 'pinU': '-110', 'book': 'Hard Rock (FL)'},
                 {'player': 'Josh Hart', 'team': 'Knicks', 'market': 'reb', 'line': 9.5, 'odds': '-110', 'uodds': '-110', 'pinLine': 10.5, 'book': 'Hard Rock (FL)'}]}


def test_scan_finds_prices_better_than_fair():
    rows = {r['label']: r for r in V.scan('nba', DOC, NOW)}
    assert 'Jalen Brunson over 26.5 points' in rows and rows['Jalen Brunson over 26.5 points']['ev'] > 0.02  # +105 vs fair -100
    assert 'Jalen Brunson under 26.5 points' not in rows
    assert 'Knicks -3.5' in rows and 'Celtics +3.5' not in rows  # +108 vs Pinnacle's -104/-106: fair about +101
    assert not any('221.5' in k for k in rows)  # Pinnacle at a different total: no comparison
    assert V.scan('nba', DOC, dt.datetime(2026, 11, 4, 1, 0, tzinfo=dt.timezone.utc)) == []  # started


def test_closing_snapshot_and_clv():
    snap = {}
    assert V.snapshot(snap, 'nba', DOC, NOW) == 2
    after = dt.datetime(2026, 11, 4, 1, 0, tzinfo=dt.timezone.utc)
    bet = {'label': 'Jalen Brunson o25.5 pts', 'legs': 1, 'odds': -110, 'date': '2026-11-03'}
    u = V.clv_for(bet, snap, after)
    assert u['closeLine'] == 26.5 and u['clv'] == 1.0 and 'clvProb' not in u  # beat the close by a point; Pinnacle closed at another line
    same = V.clv_for({'label': 'Brunson o26.5 pts', 'legs': 1, 'odds': 110, 'date': '2026-11-03'}, snap, after)
    assert same['clv'] == 0 and same['clvProb'] > 0  # +110 vs a fair -110 at the close
    assert V.clv_for(bet, snap, NOW) is None  # game not started yet
    assert V.clv_for(dict(bet, legs=3), snap, after) is None
