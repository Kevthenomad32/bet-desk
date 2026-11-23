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


# ---------- model v7 (Nov 23 2026) ----------
def _nba_comps(v=None, why=None, rr=0.6, mr=34.0):
    c = {'lg': 'nba', 'parts': [{'k': 'pts', 's': 15.0, 'rs': 0.5, 'rr': rr, 'ms': 30.0, 'mr': mr, 'mAdj': 1.1 * 0.97, 'uAdj': 1.12, 'oAdj': 1.04,
                                  'sf': None, 'dF': 1.0, 'a': 1.0, 'b': 0.0}], 'why': why or [], 'opt': None}
    if v: c['v'] = v
    return c


def test_hot_role_not_counted():
    W = P.weights({}, 'nba')
    hot = _nba_comps(v=7, rr=0.6, mr=34.0); flat = _nba_comps(v=7, rr=None, mr=None)
    assert abs(P.project(hot, W) - P.project(flat, W)) < 1e-9          # role up: same as no recent games
    cold = _nba_comps(v=7, rr=0.4, mr=26.0)
    assert P.project(cold, W) < P.project(flat, W)                       # role down still lowers it
    nfl = {'lg': 'nfl', 'v': 7, 'parts': [{'k': 'rush', 's': 60.0, 'r': 90.0, 'sf': None, 'dF': 1.0, 'a': 1.0, 'b': 0.5}], 'opt': None}
    nfl_flat = {**nfl, 'parts': [dict(nfl['parts'][0], r=None)]}
    Wn = P.weights({}, 'nfl')
    assert abs(P.project(nfl, Wn) - P.project(nfl_flat, Wn)) < 1e-9


def test_old_comps_get_v7_boosts():
    W = P.weights({}, 'nba')
    old = _nba_comps(why=['Brown out: +10% min, +12% usage'], rr=None, mr=None)
    m, u, o = P.v7_boosts(old, old['parts'][0])
    assert abs(m - 1.1 * 0.97 / 1.10 * 1.05) < 1e-9 and abs(u - 1.06) < 1e-9 and abs(o - 1.02) < 1e-9
    new = _nba_comps(v=7, why=['Brown out: +5% min, +6% usage'], rr=None, mr=None)
    assert P.v7_boosts(new, new['parts'][0]) == (new['parts'][0]['mAdj'], 1.12, 1.04)  # v7 comps are already v7
    assert P.project(old, W) < 15.0 * 1.1 * 0.97 * 1.12 * 1.04


def test_injury_boost_capped():
    T = {t: {'gp': 15, 'off': {'pts': 115, 'reb': 44, 'ast': 26, 'fg3': 13}, 'def': {'pts': 115, 'reb': 44, 'ast': 26, 'fg3': 13}} for t in ('A', 'B')}
    pool = [{'name': f'Star {i}', 'team': 'A', 'min': 34, 'pts': 25, 'reb': 6, 'ast': 6, 'fg3': 3, 'gpc': 15} for i in range(3)]
    inj = {'players': [{'name': f'Star {i}', 'team': 'A', 'status': 'Out'} for i in range(3)]}
    P_ = {'min': 30, 'pts': 15, 'reb': 5, 'ast': 3, 'fg3': 2, 'gp5': 1}
    ctx = P.nba_context({'player': 'Role Guy', 'team': 'A', 'market': 'pts'}, P_, T, [], 'B', inj, pool, [])
    c = ctx['parts'][0]
    assert ctx['v'] == 7 and c['mAdj'] <= 1.08 + 1e-9 and c['uAdj'] <= 1.08 + 1e-9  # v6 gave +34% min, +15% usage here


def test_projection_scale(monkeypatch):
    W = P.weights({}, 'nba'); p = {'market': 'reb', 'line': 7.5, 'odds': '-110', 'uodds': '-110'}
    monkeypatch.setattr(P, 'CAL', {})
    base = P.evaluate(p, 8.0, W)['pModel']
    monkeypatch.setattr(P, 'CAL', {'markets': {'reb': {'scale': 0.9}}})
    assert P.evaluate(p, 8.0, W)['pModel'] < base
    monkeypatch.setattr(P, 'CAL', {'markets': {'reb': {'scale': 0.5}}})  # capped at -15%
    assert abs(P.evaluate(p, 8.0, W)['pModel'] - P.p_over('reb', 8.0 * 0.85, 7.5)) < 1e-9
