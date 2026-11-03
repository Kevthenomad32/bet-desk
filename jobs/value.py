"""Value finder and closing line value, from TheRundown odds docs (Hard Rock prices, Pinnacle as the sharp reference).

value:   every Hard Rock price that beats Pinnacle's no-vig fair price at the same line -> the 'value' doc (+ phone alert for big ones)
closing: the last line seen before each game starts -> the 'closeSnap' doc; once a game starts, Kev's single bets on it get
         closeLine / closeOdds / clv (points beaten) / clvProb (% edge vs Pinnacle's closing fair price)
Runs at the end of every hourly tick (cheap: reads a few docs, no API calls)."""
import datetime as dt

from .common import amer, dec, et_date, iso, norm, notify, now_utc, parse_iso
from .legs import parse_label

MIN_EV = 0.02     # list prices at least 2% better than fair
ALERT_EV = 0.05   # phone alert at 5%+, for games starting within 4 hours
MKT_NAME = {'pts': 'points', 'reb': 'rebounds', 'ast': 'assists', 'fg3': '3-pointers', 'pra': 'PRA', 'pass': 'pass yds', 'rush': 'rush yds', 'rec': 'rec yds'}


def imp(o):
    o = amer(o) if not isinstance(o, (int, float)) else o
    if not o:
        return None
    return 100 / (o + 100) if o > 0 else -o / (-o + 100)


def fair(a, b):
    """No-vig probability of side a, from the two prices."""
    pa, pb = imp(a), imp(b)
    return None if pa is None or pb is None else pa / (pa + pb)


def ev(p, price):
    d = dec(price)
    return None if p is None or d is None else p * d - 1


def amer_of(p):
    p = min(max(p, 0.01), 0.99)
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def _start_of(doc):
    """team -> game start, from the doc's games."""
    m = {}
    for g in doc.get('games') or []:
        for t in (g.get('home'), g.get('away')):
            if t and g.get('start'):
                m[t] = (g['start'], f"{g['away']} @ {g['home']}")
    return m


def scan(lg, doc, now=None):
    """Value rows for one odds doc: games not started, Hard Rock price vs Pinnacle fair at the same line."""
    now = now or now_utc(); rows = []
    if not doc or 'Hard Rock' not in (doc.get('source') or ''):
        return rows
    starts = _start_of(doc)
    add = lambda **r: rows.append(r) if r['ev'] is not None and r['ev'] >= MIN_EV else None
    for g in doc.get('games') or []:
        if not g.get('start') or parse_iso(g['start']) <= now:
            continue
        game = f"{g['away']} @ {g['home']}"
        sp, to, ml = g.get('spread') or {}, g.get('total') or {}, g.get('ml') or {}
        if sp.get('pinHome') is not None and sp.get('home') is not None and abs(sp['pinHome'] - sp['home']) < 1e-9:
            p = fair(sp.get('pinHomeOdds'), sp.get('pinAwayOdds'))
            if p is not None:
                L = sp['home']
                add(lg=lg, game=game, start=g['start'], kind='spread', label=f"{g['home']} {'+' if L > 0 else ''}{L:g}", price=sp.get('homeOdds'), fairP=round(p, 4), ev=ev(p, sp.get('homeOdds')))
                add(lg=lg, game=game, start=g['start'], kind='spread', label=f"{g['away']} {'+' if -L > 0 else ''}{-L:g}", price=sp.get('awayOdds'), fairP=round(1 - p, 4), ev=ev(1 - p, sp.get('awayOdds')))
        if to.get('pinLine') is not None and to.get('line') is not None and abs(to['pinLine'] - to['line']) < 1e-9:
            p = fair(to.get('pinOver'), to.get('pinUnder'))
            if p is not None:
                add(lg=lg, game=game, start=g['start'], kind='total', label=f"{game} over {to['line']:g}", price=to.get('overOdds'), fairP=round(p, 4), ev=ev(p, to.get('overOdds')))
                add(lg=lg, game=game, start=g['start'], kind='total', label=f"{game} under {to['line']:g}", price=to.get('underOdds'), fairP=round(1 - p, 4), ev=ev(1 - p, to.get('underOdds')))
        if ml.get('pinHome') and ml.get('home'):
            p = fair(ml.get('pinHome'), ml.get('pinAway'))
            if p is not None:
                add(lg=lg, game=game, start=g['start'], kind='ml', label=f"{g['home']} ML", price=ml.get('home'), fairP=round(p, 4), ev=ev(p, ml.get('home')))
                add(lg=lg, game=game, start=g['start'], kind='ml', label=f"{g['away']} ML", price=ml.get('away'), fairP=round(1 - p, 4), ev=ev(1 - p, ml.get('away')))
    for pr in doc.get('props') or []:
        if not pr.get('pinO') or not str(pr.get('book') or '').startswith('Hard Rock'):
            continue
        st = starts.get(pr.get('team'))
        if not st or parse_iso(st[0]) <= now:
            continue
        p = fair(pr['pinO'], pr.get('pinU'))
        if p is None:
            continue
        mk = MKT_NAME.get(pr['market'], pr['market'])
        base = dict(lg=lg, game=st[1], start=st[0], kind='prop', player=pr['player'], market=pr['market'], line=pr['line'], avg=pr.get('avg'))
        add(**base, label=f"{pr['player']} over {pr['line']:g} {mk}", side='over', price=pr.get('odds'), fairP=round(p, 4), ev=ev(p, pr.get('odds')))
        add(**base, label=f"{pr['player']} under {pr['line']:g} {mk}", side='under', price=pr.get('uodds'), fairP=round(1 - p, 4), ev=ev(1 - p, pr.get('uodds')))
    for r in rows:
        r['ev'] = round(r['ev'], 4); r['fair'] = amer_of(r['fairP'])
    return rows


# ---------- closing snapshot ----------
def snap_key(lg, day, player, market):
    return f"{lg}|{day}|{norm(player).replace(' ', '')}|{market}"


def snapshot(snap, lg, doc, now=None):
    """Record the current line for every prop and game total whose game hasn't started (the last one before tip = the close)."""
    now = now or now_utc()
    if not doc:
        return 0
    starts = _start_of(doc); n = 0
    for pr in doc.get('props') or []:
        st = starts.get(pr.get('team'))
        if not st or parse_iso(st[0]) <= now:
            continue
        snap[snap_key(lg, et_date(parse_iso(st[0])), pr['player'], pr['market'])] = {
            'start': st[0], 'line': pr['line'], 'odds': pr.get('odds'), 'uodds': pr.get('uodds'), 'pinO': pr.get('pinO'), 'pinU': pr.get('pinU'),
            'pinLine': pr.get('pinLine'), 'book': pr.get('book'), 'at': iso(now)}
        n += 1
    return n


def clv_for(bet, snap, now=None):
    """CLV fields for a single player-prop bet whose game has started, or None."""
    now = now or now_utc()
    if (bet.get('legs') or 1) != 1 or bet.get('clvAt'):
        return None
    legs = parse_label(bet.get('label', ''))
    if not legs or legs[0]['market'] not in MKT_NAME:
        return None
    L = legs[0]; lg = 'nba' if L['market'] in ('pts', 'reb', 'ast', 'fg3', 'pra') else 'nfl'
    day = bet.get('date')
    c = snap.get(snap_key(lg, day, L['player'], L['market']))
    if not c:  # last-name-only labels: unique last name that day
        last = (norm(L['player']).split() or [''])[-1]
        hits = [v for k, v in snap.items() if k.startswith(f'{lg}|{day}|') and k.endswith('|' + L['market']) and k.split('|')[2].endswith(last.replace(' ', ''))]
        c = hits[0] if len(hits) == 1 else None
    if not c or parse_iso(c['start']) > now:
        return None
    over = L['side'] == 'over'
    out = {'closeLine': c['line'], 'closeOdds': c['odds'] if over else c['uodds'], 'clv': round((c['line'] - L['line']) if over else (L['line'] - c['line']), 1), 'clvAt': iso(now)}
    if c.get('pinO') and abs(c['line'] - L['line']) < 1e-9:  # Pinnacle closed at the bet's line: price edge vs its fair price
        p = fair(c['pinO'], c['pinU']); p = p if over else 1 - p
        e = ev(p, bet.get('odds'))
        if e is not None:
            out['clvProb'] = round(100 * e, 1); out['closeFair'] = amer_of(p)
    return out


def run(db):
    now = now_utc(); out = []
    docs = {lg: db.get(f'{lg}Odds') for lg in ('nba', 'nfl')}
    # 1) closing snapshot
    snap = db.get('closeSnap') or {}
    n = sum(snapshot(snap, lg, d, now) for lg, d in docs.items())
    cutoff = et_date(now - dt.timedelta(days=4))
    snap = {k: v for k, v in snap.items() if k.split('|')[1] >= cutoff}
    db.set('closeSnap', snap)
    # 2) CLV on Kev's single bets once their game has started
    nb = 0
    since = et_date(now - dt.timedelta(days=3))
    for b in db.list('ledger/bets'):
        if (b.get('date') or '') < since:
            continue
        u = clv_for(b, snap, now)
        if u:
            db.merge(f"ledger/bets/{b['_id']}", u); nb += 1
    # 3) value finder
    rows = [r for lg, d in docs.items() for r in scan(lg, d, now)]
    cbb = db.get('cbbOdds')
    rows += [r for r in scan('cbb', cbb, now) if r['kind'] != 'prop']
    rows.sort(key=lambda r: -r['ev'])
    old = db.get('value') or {}
    sent = set(old.get('alerted') or [])
    hot = [r for r in rows if r['ev'] >= ALERT_EV and parse_iso(r['start']) - now <= dt.timedelta(hours=4) and f"{r['start'][:10]}|{r['label']}" not in sent]
    if hot:
        notify(f"Value at Hard Rock: {len(hot)} price{'s' if len(hot) > 1 else ''}",
               '\n'.join(f"{r['label']} {r['price']} (fair {'+' if r['fair'] > 0 else ''}{r['fair']}, +{r['ev'] * 100:.0f}%)" for r in hot[:6]))
        sent |= {f"{r['start'][:10]}|{r['label']}" for r in hot}
    db.set('value', {'updated': iso(now), 'minEv': MIN_EV, 'rows': rows[:150], 'alerted': sorted(sent)[-400:]})
    out.append(f"{len(rows)} Hard Rock prices beat Pinnacle's fair price" + (f" ({len(hot)} alerted)" if hot else ''))
    out.append(f"closing snapshot {n} lines; CLV on {nb} bets")
    return '; '.join(out)
