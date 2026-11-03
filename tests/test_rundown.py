"""TheRundown odds -> Bet Desk odds docs (shape from docs.therundown.io: events -> markets -> participants -> lines -> prices)."""
from jobs import rundown as R

HOME = {'team_id': 10, 'name': 'New York', 'mascot': 'Knicks', 'abbreviation': 'NY', 'is_home': True, 'is_away': False}
AWAY = {'team_id': 20, 'name': 'Boston', 'mascot': 'Celtics', 'abbreviation': 'BOS', 'is_home': False, 'is_away': True}


def pr(**by_aff):
    return {str(a[1:]): {'price': p, 'is_main_line': True} for a, p in by_aff.items()}


def event():
    return {'event_id': 'e1', 'event_date': '2026-11-03T00:30:00Z', 'score': {'event_status': 'STATUS_SCHEDULED'}, 'teams': [AWAY, HOME],
            'markets': [
                {'market_id': 1, 'participants': [
                    {'id': 10, 'type': 'TYPE_TEAM', 'name': 'Knicks', 'lines': [{'value': '0', 'prices': pr(a28=-150, a3=-145, a19=-155)}]},
                    {'id': 20, 'type': 'TYPE_TEAM', 'name': 'Celtics', 'lines': [{'value': '0', 'prices': pr(a28=130, a3=135, a19=0.0001)}]}]},
                {'market_id': 2, 'participants': [
                    {'id': 10, 'type': 'TYPE_TEAM', 'name': 'Knicks', 'lines': [{'value': '-3.5', 'prices': pr(a28=-110, a3=-104)}]},
                    {'id': 20, 'type': 'TYPE_TEAM', 'name': 'Celtics', 'lines': [{'value': '3.5', 'prices': pr(a28=-110, a3=-106)}]}]},
                {'market_id': 3, 'participants': [
                    {'id': 1, 'type': 'TYPE_OVER', 'name': 'Over', 'lines': [{'value': '221.5', 'prices': pr(a28=-108, a3=-103)}]},
                    {'id': 2, 'type': 'TYPE_UNDER', 'name': 'Under', 'lines': [{'value': '221.5', 'prices': pr(a28=-112, a3=-107)}]}]},
                {'market_id': 29, 'participants': [
                    {'id': 7, 'type': 'TYPE_OVER', 'name': 'Jalen Brunson Over', 'lines': [{'value': '26.5', 'prices': pr(a28=-115, a3=-108, a19=-110, a23=-120)}]},
                    {'id': 8, 'type': 'TYPE_UNDER', 'name': 'Jalen Brunson Under', 'lines': [{'value': '26.5', 'prices': pr(a28=-105, a3=-112, a19=-110, a23=100)}]},
                    {'id': 9, 'type': 'TYPE_OVER', 'name': 'Josh Hart Over', 'lines': [{'value': '9.5', 'prices': pr(a19=-115, a23=-110)}, {'value': '10.5', 'prices': pr(a19=120)}]},
                    {'id': 11, 'type': 'TYPE_UNDER', 'name': 'Josh Hart Under', 'lines': [{'value': '9.5', 'prices': pr(a19=-105, a23=-110)}]},
                ]},
            ]}


PLAYERS = [{'name': 'Jalen Brunson', 'team': 'Knicks', 'pts': 27.0, 'reb': 3.0, 'ast': 7.0}, {'name': 'Josh Hart', 'team': 'Knicks', 'pts': 10.0, 'reb': 9, 'ast': 5}]


def test_game_lines_hard_rock_and_pinnacle():
    doc = R.build('nba', [event()], PLAYERS, prop_markets=R.NBA_PROPS)
    g = doc['games'][0]
    assert (g['away'], g['home']) == ('Celtics', 'Knicks') and g['book'] == 'Hard Rock'
    assert g['spread'] == {'home': -3.5, 'homeOdds': '-110', 'awayOdds': '-110', 'pinHome': -3.5, 'pinHomeOdds': '-104', 'pinAwayOdds': '-106'}
    assert g['total']['line'] == 221.5 and g['total']['pinOver'] == '-103'
    assert g['ml']['home'] == '-150' and g['ml']['away'] == '+130'


def test_props_use_hard_rock_with_pinnacle_anchor_and_best_price():
    props = {p['player']: p for p in R.build('nba', [event()], PLAYERS, prop_markets=R.NBA_PROPS)['props']}
    b = props['Jalen Brunson']
    assert b['book'] == 'Hard Rock (FL)' and b['line'] == 26.5 and (b['odds'], b['uodds']) == ('-115', '-105')
    assert (b['pinO'], b['pinU']) == ('-108', '-112') and b['avg'] == 27.0
    assert (b['bestO'], b['bestU']) == ('-110', '+100')


def test_no_hard_rock_falls_back_to_us_books_main_line():
    h = {p['player']: p for p in R.build('nba', [event()], PLAYERS, prop_markets=R.NBA_PROPS)['props']}['Josh Hart']
    assert h['line'] == 9.5 and h['book'] in ('DraftKings', 'FanDuel') and 'pinO' not in h  # one-sided 10.5 alt ignored


def test_started_games_skipped_and_unknown_players_dropped():
    e = event(); e['score']['event_status'] = 'STATUS_IN_PROGRESS'
    assert R.build('nba', [e], PLAYERS, prop_markets=R.NBA_PROPS)['games'] == []
    props = R.build('nba', [event()], [], team_of=lambda n: None, prop_markets=R.NBA_PROPS)['props']
    assert props == []  # can't tell which team a player is on: skipped


def test_et_offset():
    import datetime as dt
    assert R.et_offset(dt.date(2026, 11, 3)) == 300 and R.et_offset(dt.date(2026, 7, 1)) == 240
