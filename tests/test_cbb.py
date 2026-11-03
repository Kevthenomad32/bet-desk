"""College basketball: ratings fit, team-name matching, calls and grading."""
import random

from jobs import cbb


def synth(n_teams=40, games_per=18, seed=7, hca=3.4, mu=71.0, noise=8.0):
    rnd = random.Random(seed)
    O = {str(i): rnd.gauss(0, 6) for i in range(n_teams)}; D = {str(i): rnd.gauss(0, 5) for i in range(n_teams)}
    games = []
    for k in range(n_teams * games_per // 2):
        h, a = rnd.sample(list(O), 2); neutral = rnd.random() < 0.1
        hh = 0 if neutral else hca / 2
        ph = mu + O[h] + D[a] + hh + rnd.gauss(0, noise); pa = mu + O[a] + D[h] - hh + rnd.gauss(0, noise)
        games.append({'id': str(k), 'date': '2026-12-01', 'home': f'School {chr(65 + int(h))}', 'away': f'School {chr(65 + int(a))}', 'home_score': round(ph), 'away_score': round(pa),
                      'box': {'neutral': neutral, 'home_id': h, 'away_id': a, 'home_mascot': 'Cats', 'away_mascot': 'Dogs'}})
    return games, O, D


def test_fit_recovers_strength_and_home_court():
    games, O, D = synth()
    R = cbb.fit(games, lam=1.0)
    assert 2.0 < R['h'] < 4.8
    net_true = {t: O[t] - D[t] for t in O}; net_fit = {t: v['O'] - v['D'] for t, v in R['teams'].items()}
    mt = sum(net_true.values()) / len(net_true)
    corr_num = sum((net_true[t] - mt) * net_fit[t] for t in O)
    var_t = sum((net_true[t] - mt) ** 2 for t in O); var_f = sum(net_fit[t] ** 2 for t in O)
    assert corr_num / (var_t * var_f) ** .5 > 0.85


def test_prior_dominates_with_no_games():
    R = cbb.fit([{'id': '1', 'date': '2026-11-03', 'home': 'A', 'away': 'B', 'home_score': 90, 'away_score': 60,
                  'box': {'home_id': 'a', 'away_id': 'b'}}], prior={'a': {'O': -5, 'D': 5}, 'b': {'O': 5, 'D': -5}}, lam=6)
    assert R['teams']['a']['O'] - R['teams']['a']['D'] < R['teams']['b']['O'] - R['teams']['b']['D']  # one blowout doesn't overturn the prior


def test_matcher():
    teams = {'1': {'name': 'UConn', 'mascot': 'Huskies'}, '2': {'name': 'Washington', 'mascot': 'Huskies'}, '3': {'name': "Saint Mary's", 'mascot': 'Gaels'},
             '4': {'name': 'Iowa State', 'mascot': 'Cyclones'}, '5': {'name': 'Iowa', 'mascot': 'Hawkeyes'}}
    f = cbb.matcher(teams)
    assert f('Washington Huskies', 'Huskies') == '2' and f('Iowa St', 'Cyclones') == '4' and f('Iowa', 'Hawkeyes') == '5'
    assert f("St. Mary's", 'Gaels') == '3' and f('Nowhere Tech', 'Owls') is None


def test_call_uses_pinnacle_and_grades():
    c = cbb.call('spread', 8.0, 10.8, -5.5, '-110', '-110', -5.5, '-120', '+100')
    assert c['anchor'] == 'Pinnacle' and c['side'] == 'home' and 0.5 < c['pWin'] < 0.7
    doc = {'calls': {'spread': dict(c), 'total': cbb.call('total', 140, 13.5, 145.5, '-110', '-110')}}
    g = cbb.grade(doc, 75, 68)  # won by 7: covers -5.5; 143 total
    assert g['calls']['spread']['result'] == 'won' and g['calls']['total']['result'] == ('won' if g['calls']['total']['side'] == 'under' else 'lost')


def test_board_from_rundown_doc():
    games, _, _ = synth(n_teams=6, games_per=10)
    R = cbb.fit(games, lam=1.0); R.update(sdMargin=10.8, sdTotal=13.5)
    odds = {'games': [{'home': 'School B', 'away': 'School C', 'homeMascot': 'Cats', 'awayMascot': 'Dogs', 'start': '2026-12-02T00:00:00Z',
                       'spread': {'home': -2.5, 'homeOdds': '-110', 'awayOdds': '-110'}, 'total': {'line': 141.5, 'overOdds': '-110', 'underOdds': '-110'}}]}
    rows = cbb.board(R, odds)
    assert len(rows) == 1 and set(rows[0]['calls']) == {'spread', 'total'}
    docs = cbb.lock_docs(rows, '2026-12-01T23:00:00Z', '2026-12-02T01:00:00Z')
    assert list(docs) == ['cbb-2026-12-01-school-c-at-school-b']
