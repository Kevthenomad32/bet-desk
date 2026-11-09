"""Nightly probability calibration for the prop model.

The raw model's over/under chances can lean one way (Oct-Nov 2026: overs hit 45% while unders hit 58%). For each
market this fits   logit(calibrated) = a * logit(raw) + b   on the props the model priced and that have been graded,
pulled toward "no change" (a=1, b=0) so a small sample can only move it a little. The result is stored in the
'calibration' doc and applied by both the hourly model runs and the app page.

Always fit on the RAW model chance (rows keep it as pOverRaw once calibration is on), never on calibrated ones.
Rows without pOverRaw are skipped: they predate calibration (Nov 2 2026) and were priced by an older blend (before the
NBA 0.02 under shift and NBA points going book-only), so fitting on them re-learned the under lean and stacked it on top
of the shift (week of Nov 2: NBA overs hit 48.6% but were priced at 45.6%; every NBA points and PRA pick was an under)."""
import datetime as dt
import math

from .common import iso, now_utc

MIN_N = 60          # graded props a market needs before it gets its own correction
DAYS = 60           # only the recent past (roles and lines change through a season)
HALF_LIFE = 14      # days: a prop from two weeks ago counts half as much as last night's
PRIOR = (4.0, 8.0)  # pull strength toward a=1 and toward b=0 (in "pseudo-observations" per unit)
A_RANGE, B_RANGE = (0.4, 1.4), (-0.8, 0.8)

clip = lambda x, lo, hi: max(lo, min(hi, x))
logit = lambda p: math.log(p / (1 - p))
sig = lambda z: 1 / (1 + math.exp(-z))


def raw_p(r):
    p = r.get('pOverRaw')  # no fallback to pOver: see the module note
    try:
        return clip(float(p), 0.01, 0.99)
    except (TypeError, ValueError):
        return None


def samples(shadow_docs, days=DAYS, today=None):
    """[(market, raw pOver, went over, weight)] from graded shadow docs; pushes and players who didn't play are left out."""
    today = today or now_utc().date()
    cut = (today - dt.timedelta(days=days)).isoformat()
    out = []
    for d in shadow_docs:
        if (d.get('date') or '') < cut:
            continue
        for r in d.get('rows') or []:
            res, line, p = r.get('result'), r.get('line'), raw_p(r)
            if res is None or line is None or p is None:
                continue
            res, line = float(res), float(line)
            if res == line:
                continue
            age = max(0, (today - dt.date.fromisoformat(d['date'])).days) if d.get('date') else DAYS
            out.append((r.get('market'), p, 1 if res > line else 0, 0.5 ** (age / HALF_LIFE)))
    return out


def fit(xs, ys, ws=None, iters=50):
    """Penalized, weighted logistic fit of y on (a*x + b), x = logit(raw). Newton steps with a ridge toward a=1, b=0."""
    ws = ws or [1.0] * len(xs)
    a, b = 1.0, 0.0
    la, lb = PRIOR
    for _ in range(iters):
        ga = -la * (a - 1); gb = -lb * b
        haa, hab, hbb = -la, 0.0, -lb
        for x, y, wt in zip(xs, ys, ws):
            q = sig(a * x + b); r = wt * (y - q); w = wt * q * (1 - q)
            ga += r * x; gb += r
            haa -= w * x * x; hab -= w * x; hbb -= w
        det = haa * hbb - hab * hab
        if abs(det) < 1e-12:
            break
        da = (hbb * ga - hab * gb) / det; db = (haa * gb - hab * ga) / det
        a, b = a - da, b - db
        if abs(da) < 1e-7 and abs(db) < 1e-7:
            break
    return clip(a, *A_RANGE), clip(b, *B_RANGE)


def build(shadow_docs, today=None):
    by = {}
    for m, p, y, wt in samples(shadow_docs, today=today):
        by.setdefault(m, []).append((p, y, wt))
    markets = {}
    for m, rows in sorted(by.items()):
        n = len(rows); W = sum(w for _, _, w in rows)
        over_rate = sum(y * w for _, y, w in rows) / W; pred = sum(p * w for p, _, w in rows) / W  # recency-weighted
        entry = {'n': n, 'overRate': round(over_rate, 3), 'predOver': round(pred, 3)}
        if n >= MIN_N:
            a, b = fit([logit(p) for p, _, _ in rows], [y for _, y, _ in rows], [w for _, _, w in rows])
            entry.update(a=round(a, 4), b=round(b, 4))
        markets[m] = entry
    return {'updated': iso(now_utc()), 'method': 'logit(p) -> a*logit(p)+b per market, ridge toward no change', 'minN': MIN_N, 'days': DAYS,
            'markets': markets}


def apply(cal, market, p):
    """Calibrated over chance (unchanged when the market has no correction yet)."""
    m = ((cal or {}).get('markets') or {}).get(market) or {}
    if p is None or 'a' not in m:
        return p
    p = clip(float(p), 0.01, 0.99)
    return clip(sig(m['a'] * logit(p) + m['b']), 0.01, 0.99)


def run(db):
    cal = build(db.list('ledger/shadow'))
    db.set('calibration', cal)
    fitted = {m: v for m, v in cal['markets'].items() if 'a' in v}
    return 'calibration: ' + (', '.join(f"{m} (n {v['n']}, overs hit {v['overRate']:.0%} vs model {v['predOver']:.0%})" for m, v in fitted.items()) or 'not enough graded props yet')
