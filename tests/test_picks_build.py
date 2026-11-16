from jobs.model import picks_build as P

CAL = {'markets': {'pts': {'a': 0.635, 'b': -0.0707}, 'pass': {'a': 0.8718, 'b': 0.0888}, 'reb': {'a': 0.9766, 'b': -0.0367}}}


def test_pass_is_book_only_in_nfl():
    W = P.weights({}, 'nfl')
    assert 'pass' in W['off'] and 'rec' not in W['off']
    assert P.blend(0.70, 0.52, W, 'pass') == 0.52  # model ignored, no NFL under shift


def test_book_only_markets_skip_calibration(monkeypatch):
    monkeypatch.setattr(P, 'CAL', CAL)
    nba, nfl = P.weights({}, 'nba'), P.weights({}, 'nfl')
    # NBA points: only the 0.02 under shift, so a book lean of 54% over stays an over pick
    pf = P.calibrated(P.blend(0.40, 0.54, nba, 'pts'), 'pts', nba)
    assert abs(pf - 0.52) < 1e-9 and pf >= 0.5
    assert P.calibrated(0.52, 'pass', nfl) == 0.52
    # markets that still use the model keep their calibration
    assert P.calibrated(0.52, 'reb', nba) != 0.52


def test_tuned_weights_cannot_drop_off_list():
    W = P.weights({'nfl': {'wModel': 0.15, 'off': []}}, 'nfl')
    assert W['wModel'] == 0.15 and W['off'] == ('pass',)
