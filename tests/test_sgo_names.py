"""SportsGameOdds sometimes sends NBA players without a 'name' (Oct 2026): props must still get averages and the stats feed's spelling."""
from jobs import sgo


def ev(players, pids):
    return [{'status': {}, 'teams': {'home': {'teamID': 'H', 'names': {'medium': 'Thunder'}}, 'away': {'teamID': 'A', 'names': {'medium': 'Kings'}}},
             'players': players,
             'odds': {pid: {'playerID': pid, 'statID': 'points', 'betTypeID': 'ou', 'sideID': 'over', 'periodID': 'game', 'bookOddsAvailable': True,
                            'bookOverUnder': '24.5', 'bookOdds': '-110'} for pid in pids}}]


def test_names_from_ids_match_stats_spelling():
    pids = ['SHAI_GILGEOUSALEXANDER_1_NBA', 'DE_AARON_FOX_1_NBA', 'AARON_WIGGINS_1_NBA']
    players = {p: {'teamID': 'H'} for p in pids}
    stats = [{'name': 'Shai Gilgeous-Alexander', 'pts': 31.0, 'reb': 5, 'ast': 6}, {'name': "De'Aaron Fox", 'pts': 24.0, 'reb': 4, 'ast': 6},
             {'name': 'Aaron Wiggins', 'pts': 11.0, 'reb': 4, 'ast': 2}]
    got = {p['player']: p['avg'] for p in sgo.build('nba', ev(players, pids), stats)['props']}
    assert got == {'Shai Gilgeous-Alexander': 31.0, "De'Aaron Fox": 24.0, 'Aaron Wiggins': 11.0}


def test_first_last_and_plain_name():
    assert sgo.pname({'firstName': 'Jalen', 'lastName': 'Brunson'}, 'X') == 'Jalen Brunson'
    assert sgo.pname({'name': 'Josh Allen'}, 'JOSH_ALLEN_1_NFL') == 'Josh Allen'
    assert sgo.pname({}, 'AARON_WIGGINS_1_NBA') == 'Aaron Wiggins'
