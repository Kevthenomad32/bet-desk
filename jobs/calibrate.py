"""Nightly probability calibration for the prop model.

The raw model's over/under chances can lean one way (Oct-Nov 2026: overs hit 45% while unders hit 58%). For each
market this fits   logit(calibrated) = a * logit(raw) + b   on the props the model priced and that have been graded,
pulled toward "no change" (a=1, b=0) so a small sample can only move it a little. The result is stored in the
'calibration' doc and applied by both the hourly model runs and the app page.

Always fit on the RAW model chance (rows keep it as pOverRaw once calibration is on), never on calibrated ones.

Since model v7 it also learns a per-market projection 'scale' = actual results / projected averages on v7's own graded props
(rows keep the unscaled projection as 'exp'), shrunk toward 1 and capped at +/-15%, so an average that runs high or low is pulled back.

Book-only markets (the model's 'off' list: NBA points, NFL passing/rushing/receiving) get their own correction fitted on the BOOK's no-vig
chance (rows keep it as pBook): logit(p) = ba*logit(pBook) + bb. It replaces the flat 'under' nudge for those markets, so each one leans
the way its own graded props went (Nov 2026: NFL rushing/receiving overs hit ~44% at a 50% book price; passing overs ~55%)."""
import datetime as dt
import math

from .common import iso, now_utc

MIN_N = 60          # graded props a market needs before it gets its own correction
DAYS = 60           # only the recent past (roles and lines change through a season)
HALF_LIFE = 14      # days: a prop from two weeks ago counts half as much as last night's
PRIOR = (4.0, 8.0)  # pull strength toward a=1 and toward b=0 (in "pseudo-observations" per unit)
A_RANGE, B_RANGE = (0.4, 1.4), (-0.8, 0.8)
SCALE_MIN_MODEL = 7    # only props priced by model v7+ (older projections ran high for reasons v7 fixed)
SCALE_PRIOR = 200      # pseudo-props pulling the scale toward 1.0
SCALE_RANGE = (0.85, 1.15)
BOOK_PRIOR = (20.0, 40.0)  # book-price fit: pulled harder toward no change than the model fit (the lean has to show up across weeks)

clip = lambda x, lo, hi: max(lo, min(hi, x))
logit = lambda p: math.log(p / (1 - p))
sig = lambda z: 1 / (1 + math.exp(-z))


def raw_p(r, key=None):
    p = r.get(key) if key else r.get('pOverRaw', r.get('pOver'))
    try:
        return clip(float(p), 0.01, 0.99)
    except (TypeError, ValueError):
        return None


def samples(shadow_docs, days=DAYS, today=None):
    """[(market, raw pOver, went over, weight, book pOver)] from graded shadow docs; pushes and players who didn't play are left out."""
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
            out.append((r.get('market'), p, 1 if res > line else 0, 0.5 ** (age / HALF_LIFE), raw_p(r, 'pBook')))
    return out


def scale_samples(shadow_docs, days=DAYS, today=None):
    """{market: (n, weighted sum of results, weighted sum of projections)} from graded v7+ shadow docs."""
    today = today or now_utc().date()
    cut = (today - dt.timedelta(days=days)).isoformat()
    out = {}
    for d in shadow_docs:
        if (d.get('date') or '') < cut or (d.get('model') or 0) < SCALE_MIN_MODEL:
            continue
        age = max(0, (today - dt.date.fromisoformat(d['date'])).days) if d.get('date') else DAYS
        wt = 0.5 ** (age / HALF_LIFE)
        for r in d.get('rows') or []:
            try:
                res, e = float(r['result']), float(r['exp'])
            except (KeyError, TypeError, ValueError):
                continue
            if e <= 0:
                continue
            n, sr, se = out.get(r.get('market'), (0, 0.0, 0.0))
            out[r.get('market')] = (n + 1, sr + wt * res, se + wt * e)
    return out


def scale_of(n, sr, se):
    """Results/projections ratio, shrunk toward 1 by SCALE_PRIOR pseudo-props and capped."""
    if n < MIN_N or se <= 0:
        return None
    k = n / (n + SCALE_PRIOR)
    return round(clip(1 + k * (sr / se - 1), *SCALE_RANGE), 4)


def fit(xs, ys, ws=None, iters=50, prior=PRIOR):
    """Penalized, weighted logistic fit of y on (a*x + b), x = logit(raw). Newton steps with a ridge toward a=1, b=0."""
    ws = ws or [1.0] * len(xs)
    a, b = 1.0, 0.0
    la, lb = prior
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
    for m, p, y, wt, pb in samples(shadow_docs, today=today):
        by.setdefault(m, []).append((p, y, wt, pb))
    markets = {}
    for m, rows in sorted(by.items()):
        n = len(rows); W = sum(w for _, _, w, _ in rows)
        over_rate = sum(y * w for _, y, w, _ in rows) / W; pred = sum(p * w for p, _, w, _ in rows) / W  # recency-weighted
        entry = {'n': n, 'overRate': round(over_rate, 3), 'predOver': round(pred, 3)}
        if n >= MIN_N:
            a, b = fit([logit(p) for p, _, _, _ in rows], [y for _, y, _, _ in rows], [w for _, _, w, _ in rows])
            entry.update(a=round(a, 4), b=round(b, 4))
        bk = [(pb, y, w) for _, y, w, pb in rows if pb is not None]
        if len(bk) >= MIN_N:  # correction on the book's own price, used by book-only markets
            ba, bb = fit([logit(pb) for pb, _, _ in bk], [y for _, y, _ in bk], [w for _, _, w in bk], prior=BOOK_PRIOR)
            entry.update(ba=round(ba, 4), bb=round(bb, 4), bookOver=round(sum(pb * w for pb, _, w in bk) / sum(w for _, _, w in bk), 3))
        markets[m] = entry
    for m, (n, sr, se) in scale_samples(shadow_docs, today=today).items():
        sc = scale_of(n, sr, se)
        if sc is not None:
            markets.setdefault(m, {'n': 0})
            markets[m].update(scale=sc, scaleN=n)
    return {'updated': iso(now_utc()), 'method': 'logit(p) -> a*logit(p)+b per market, ridge toward no change', 'minN': MIN_N, 'days': DAYS,
            'markets': markets}


def apply(cal, market, p):
    """Calibrated over chance (unchanged when the market has no correction yet)."""
    m = ((cal or {}).get('markets') or {}).get(market) or {}
    if p is None or 'a' not in m:
        return p
    p = clip(float(p), 0.01, 0.99)
    return clip(sig(m['a'] * logit(p) + m['b']), 0.01, 0.99)


def apply_book(cal, market, pb):
    """Book-only markets: the book's no-vig chance with the market's learned lean, or None until it has one."""
    m = ((cal or {}).get('markets') or {}).get(market) or {}
    if pb is None or 'bb' not in m:
        return None
    pb = clip(float(pb), 0.01, 0.99)
    return clip(sig(m['ba'] * logit(pb) + m['bb']), 0.01, 0.99)


def run(db):
    cal = build(db.list('ledger/shadow'))
    db.set('calibration', cal)
    fitted = {m: v for m, v in cal['markets'].items() if 'a' in v}
    scaled = {m: v for m, v in cal['markets'].items() if 'scale' in v}
    return ('calibration: ' + (', '.join(f"{m} (n {v['n']}, overs hit {v['overRate']:.0%} vs model {v['predOver']:.0%})" for m, v in fitted.items()) or 'not enough graded props yet')
            + ('; projection scale: ' + ', '.join(f"{m} ×{v['scale']}" for m, v in scaled.items()) if scaled else '')
            + ('; book lean: ' + ', '.join(f"{m} {v['bookOver']:.0%}→{sig(v['ba'] * logit(clip(v['bookOver'], .01, .99)) + v['bb']):.0%}" for m, v in cal['markets'].items() if 'bb' in v)))
