"""Calibration: learns a lean the model misses, leaves balanced markets alone, and is applied by the model script."""
import datetime as dt
import random

from jobs import calibrate as C


def shadow(market, n, p_model, over_rate, seed=1, date='2026-10-30'):
    rnd = random.Random(seed)
    rows = [{'market': market, 'pOver': p_model, 'pOverRaw': p_model, 'line': 10.5, 'result': 12 if rnd.random() < over_rate else 9} for _ in range(n)]
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
        rows.append({'market': 'ast', 'pOver': p, 'pOverRaw': p, 'line': 5.5, 'result': 7 if rnd.random() < p else 4})
    cal = C.build([{'date': '2026-10-30', 'rows': rows}], today=dt.date(2026, 11, 2))
    for p in (0.4, 0.5, 0.6):
        assert abs(C.apply(cal, 'ast', p) - p) < 0.05


def test_fits_raw_not_calibrated():
    rows = [{'market': 'pts', 'pOver': 0.40, 'pOverRaw': 0.60, 'line': 1.5, 'result': 2}]
    assert C.samples([{'date': '2026-11-01', 'rows': rows}], today=dt.date(2026, 11, 2))[0][1] == 0.60


def test_old_props_count_less():
    s = C.samples([shadow('pts', 1, 0.5, 1, date='2026-10-19'), shadow('pts', 1, 0.5, 1, date='2026-11-02')], today=dt.date(2026, 11, 2))
    assert abs(s[0][3] - 0.5) < 0.01 and s[1][3] == 1.0


def test_skips_rows_without_raw():
    # rows from before calibration carry only pOver (an older blend); they must not feed the fit
    old = [{'market': 'pts', 'pOver': 0.55, 'line': 1.5, 'result': 0} for _ in range(300)]
    new = [{'market': 'pts', 'pOver': 0.47, 'pOverRaw': 0.48, 'line': 1.5, 'result': 2}]
    s = C.samples([{'date': '2026-10-30', 'rows': old}, {'date': '2026-11-03', 'rows': new}], today=dt.date(2026, 11, 4))
    assert len(s) == 1 and s[0][1] == 0.48
    assert 'a' not in C.build([{'date': '2026-10-30', 'rows': old}], today=dt.date(2026, 11, 4))['markets'].get('pts', {})
