"""Calibration: learns a lean the model misses, leaves balanced markets alone, and is applied by the model script."""
import datetime as dt
import random

from jobs import calibrate as C


def shadow(market, n, p_model, over_rate, seed=1, date='2026-10-30'):
    rnd = random.Random(seed)
    rows = [{'market': market, 'pOver': p_model, 'line': 10.5, 'result': 12 if rnd.random() < over_rate else 9} for _ in range(n)]
    return {'date': date, 'rows': rows}


def test_learns_under_lean():
    cal = C.build([shadow('pts', 400, 0.55, 0.42)], today=dt.date(2026, 11, 2))
    m = cal['markets']['pts']
    assert m['n'] == 400 and m['overRate'] < 0.46
    assert C.apply(cal, 'pts', 0.55) < 0.50  # a "55% over" now reads as an under lean


def test_small_sample_untouched():
    cal = C.build([shadow('reb', 20, 0.6, 0.2)], today=dt.date(2026, 11, 2))
    assert 'a' not in cal['markets']['reb'] and C.apply(cal, 'reb', 0.6) == 0.6


def test_balanced_market_barely_moves():
    rnd = random.Random(3)
    rows = []
    for _ in range(600):
        p = rnd.uniform(0.35, 0.65)
        rows.append({'market': 'ast', 'pOver': p, 'line': 5.5, 'result': 7 if rnd.random() < p else 4})
    cal = C.build([{'date': '2026-10-30', 'rows': rows}], today=dt.date(2026, 11, 2))
    for p in (0.4, 0.5, 0.6):
        assert abs(C.apply(cal, 'ast', p) - p) < 0.05


def test_fits_raw_not_calibrated():
    rows = [{'market': 'pts', 'pOver': 0.40, 'pOverRaw': 0.60, 'line': 1.5, 'result': 2}]
    assert C.samples([{'date': '2026-11-01', 'rows': rows}], today=dt.date(2026, 11, 2))[0][1] == 0.60


def test_old_props_count_less():
    s = C.samples([shadow('pts', 1, 0.5, 1, date='2026-10-19'), shadow('pts', 1, 0.5, 1, date='2026-11-02')], today=dt.date(2026, 11, 2))
    assert abs(s[0][3] - 0.5) < 0.01 and s[1][3] == 1.0


def _v(model, n, exp, res, date='2026-11-20'):
    return {'date': date, 'model': model, 'rows': [{'market': 'rush', 'pOver': 0.5, 'line': 50.5, 'exp': exp, 'result': res} for _ in range(n)]}


def test_projection_scale_learns_from_v7_only():
    today = dt.date(2026, 11, 23)
    cal = C.build([_v(6, 500, 70.0, 50.0)], today=today)
    assert 'scale' not in cal['markets']['rush']                    # old-model projections are ignored
    cal = C.build([_v(6, 500, 70.0, 50.0), _v(7, 300, 60.0, 57.0)], today=today)
    m = cal['markets']['rush']
    assert m['scaleN'] == 300 and 0.95 < m['scale'] < 1.0           # 57/60 = 0.95, shrunk toward 1
    assert C.build([_v(7, 30, 60.0, 40.0)], today=today)['markets']['rush'].get('scale') is None  # too few
    assert C.build([_v(7, 5000, 60.0, 20.0)], today=today)['markets']['rush']['scale'] == 0.85   # capped
