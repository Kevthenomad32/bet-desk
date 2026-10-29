"""Bets and picks: nightly settle (code first, Claude for the rest), closing lines, weekly recap, Sunday live updates."""
import datetime as dt
import json
import os
import re

import requests

from . import espn, sgo
from .common import ET, amer, dec, env, et_date, iso, norm, notify, now_et, now_utc, parse_iso
from .data_jobs import archive_range
from .legs import CATEGORY, find_player, grade, league_of, parse_label, stat_value
from .model import close_build

D = dt.timedelta


# ---------- helpers ----------
def _rows(db, lg, start, end):
    return db.select('player_games', league=f'eq.{lg}', date=f'gte.{start}', **{'and': f'(date.lte.{end})'},
                     select='player_id,name,name_norm,team,opp,date,dnp,stats,game_id')


def _games(db, lg, start, end):
    return db.select('games', league=f'eq.{lg}', date=f'gte.{start}', **{'and': f'(date.lte.{end})'}, select='id,date,home,away,home_score,away_score,status')


def claude(prompt, max_tokens=4000):
    key = env('ANTHROPIC_API_KEY', required=False)
    if not key:
        return None
    r = requests.post('https://api.anthropic.com/v1/messages', timeout=120,
                      headers={'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
                      json={'model': os.environ.get('CLAUDE_MODEL') or 'claude-sonnet-5-5', 'max_tokens': max_tokens,
                            'messages': [{'role': 'user', 'content': prompt + '\n\nReply with only the JSON object.'}]})
    r.raise_for_status()
    text = ''.join(c.get('text', '') for c in r.json().get('content', []) if c.get('type') == 'text')
    m = re.search(r'\{[\s\S]*\}', text)
    return json.loads(m.group(0) if m else text)


def _sport(b):
    if b.get('sport'):
        return b['sport']
    legs = parse_label(b.get('label', ''))
    return league_of(legs[0]).upper() if legs else None


# ---------- nightly settle ----------
SETTLE_RULES = """Rules:
- Every leg won -> "won". Any leg lost (even if other games aren't final yet) -> "lost"; for a lost bet with 2+ legs give lostLeg = the first leg that lost, short, with the result (e.g. "Cook o64.5 rush yds (52)") and lostMarket = one of "Passing yds","Rushing yds","Receiving yds","Anytime TD","Points","Rebounds","Assists","3-pointers","PRA","Spread / ML","Over/under","Other".
- All games final, no leg lost, but a leg pushed or was voided (player inactive) -> "open" with note "A leg pushed/voided — check the book's adjusted payout and mark it."
- A leg's game not final, or a leg you can't confirm from the data below -> "open" with a one-line note naming what's pending (e.g. "Waiting on Cowboys @ Packers (SNF)"). Never guess.
- Over wins only if the stat is strictly greater than the line; "80+" needs at least that number. Anytime TD = rushing or receiving TD. Spreads use the final score including OT; 1st half / 1st quarter legs use the line score (quarters/periods listed in order).
- Give sport (NFL, NBA, CFB, NHL, MLB or Other)."""


def _context(db, bets, start, end):
    """Scores + line scores + relevant player lines for Claude."""
    lines = []
    d = start
    while d <= end:
        for lg in ('nba', 'nfl', 'cfb'):
            try:
                for g in espn.scoreboard(lg, d.strftime('%Y%m%d')):
                    if g['state'] == 'pre':
                        continue
                    ls = lambda x: '-'.join(str(int(v)) for v in x if v is not None)
                    lines.append(f"{lg.upper()} {g['date']} {g['away']} @ {g['home']}: {g['state']} {g['away_score']}-{g['home_score']} "
                                 f"({g['detail']}; by period away {ls(g['away_ls'])} / home {ls(g['home_ls'])})")
            except Exception:  # noqa: BLE001
                pass
        d += D(days=1)
    tokens = {w for b in bets for w in norm(b.get('label', '')).split() if len(w) >= 4}
    for lg in ('nba', 'nfl'):
        for r in _rows(db, lg, start, end):
            if set(r['name_norm'].split()) & tokens:
                s = r['stats']
                if lg == 'nba':
                    txt = 'DNP' if r['dnp'] else f"pts {s.get('pts')} reb {s.get('reb')} ast {s.get('ast')} 3pm {s.get('fg3')} min {s.get('min')}"
                else:
                    txt = ' '.join(f'{k} {v}' for k, v in s.items() if v)
                lines.append(f"{lg.upper()} {r['date']} {r['name']} ({r['team']} vs {r['opp']}, FINAL): {txt}")
    return '\n'.join(lines)


def settle(db):
    t = now_et(); today = t.date()
    archive_range(db, 'nba', today - D(days=2), today)
    archive_range(db, 'nfl', today - D(days=8), today)
    out = []
    # app best bets (picks)
    picks = [p for p in db.list('ledger/picks') if (p.get('status') or 'open') == 'open' and p.get('gameStart') and parse_iso(p['gameStart']) < now_utc()]
    won = lost = push = 0
    for p in picks:
        lg, day = p['league'].lower(), et_date(p['gameStart'])
        g = next((g for g in _games(db, lg, day, day) if p['team'] in (g['home'], g['away'])), None)
        if not g:
            continue
        r = find_player([x for x in _rows(db, lg, day, day) if x['game_id'] == g['id']], p['player'])
        v = None if not r or r['dnp'] else stat_value(r['stats'], p['market'])
        if v is None:
            db.merge(f"ledger/picks/{p['_id']}", {'status': 'push', 'note': 'Did not play', 'result': None}); push += 1; continue
        st = grade({'side': p['side'], 'line': float(p['line']), 'market': p['market']}, v)
        db.merge(f"ledger/picks/{p['_id']}", {'status': st, 'result': v, 'settledBy': 'task'})
        won += st == 'won'; lost += st == 'lost'; push += st == 'push'
    if picks:
        out.append(f'best bets graded: {won}-{lost}' + (f'-{push}' if push else ''))
    # shadow log (every prop the model priced)
    n = 0
    for d in db.list('ledger/shadow'):
        if d.get('graded') or not d.get('gameStart') or parse_iso(d['gameStart']) > now_utc() - D(hours=3):
            continue
        lg, day = d['league'].lower(), et_date(d['gameStart'])
        aw, hm = (d.get('game') or ' @ ').split(' @ ')
        g = next((g for g in _games(db, lg, day, day) if g['home'] == hm and g['away'] == aw), None)
        if not g:
            continue
        rows = [x for x in _rows(db, lg, day, day) if x['game_id'] == g['id']]
        for r in d['rows']:
            pr = find_player(rows, r['player'])
            r['result'] = None if not pr or pr['dnp'] else stat_value(pr['stats'], r['market'])
        db.set(f"ledger/shadow/{d['_id']}", {**{k: v for k, v in d.items() if k != '_id'}, 'graded': True, 'gradedAt': iso(now_utc())}); n += 1
    if n:
        out.append(f'{n} games of logged props graded')
    # personal bets
    bets = [b for b in db.list('ledger/bets') if b.get('status') == 'open']
    todo, done = [], []
    for b in bets:
        legs = parse_label(b.get('label', ''))
        if not legs:
            todo.append(b); continue
        start = dt.date.fromisoformat(b.get('date') or today.isoformat())
        res = []
        for leg in legs:
            lg = (b.get('sport') or '').lower() if (b.get('sport') or '').lower() in ('nba', 'nfl') else league_of(leg)
            r = find_player(_rows(db, lg, start, min(today, start + D(days=1 if lg == 'nba' else 7))), leg['player'])
            v = None if not r or r['dnp'] else stat_value(r['stats'], leg['market'])
            res.append((leg, v, 'void' if r and r['dnp'] else grade(leg, v) if r else None))
        lostleg = next(((l, v) for l, v, s in res if s == 'lost'), None)
        if lostleg:
            upd = {'status': 'lost', 'settledBy': 'auto'}
            if (b.get('legs') or 1) > 1:
                upd.update(lostLeg=f"{lostleg[0]['text']} ({lostleg[1]:g})", lostMarket=CATEGORY.get(lostleg[0]['market'], 'Other'))
            done.append((b, upd))
        elif all(s == 'won' for _, _, s in res):
            done.append((b, {'status': 'won', 'settledBy': 'auto'}))
        elif all(s in ('won', 'push', 'void') for _, _, s in res):
            done.append((b, {'note': "A leg pushed/voided — check the book's adjusted payout and mark it."}))
        else:
            todo.append(b)
    if todo:
        start = min(dt.date.fromisoformat(b.get('date') or today.isoformat()) for b in todo)
        start = max(start, today - D(days=8))
        ctx = _context(db, todo, start, today)
        ask = [{'id': b['_id'], 'date': b.get('date'), 'label': b.get('label'), 'type': b.get('type'), 'legs': b.get('legs'), 'sport': b.get('sport')} for b in todo]
        try:
            j = claude(f"Grade these open sports bets from the results below.\n{SETTLE_RULES}\n\nBets (JSON):\n{json.dumps(ask)}\n\nResults (US Eastern dates):\n{ctx}\n\n"
                       'Return {"bets":[{"id":..., "status":"won"|"lost"|"open", "lostLeg":..., "lostMarket":..., "note":..., "sport":...}]}.')
        except Exception as e:  # noqa: BLE001
            j = None; out.append(f'Claude unavailable ({e})')
        for v in (j or {}).get('bets', []):
            b = next((x for x in todo if x['_id'] == v.get('id')), None)
            if not b:
                continue
            if v.get('status') in ('won', 'lost'):
                upd = {'status': v['status'], 'settledBy': 'auto'}
                if v['status'] == 'lost' and (b.get('legs') or 1) > 1:
                    upd.update(lostLeg=v.get('lostLeg'), lostMarket=v.get('lostMarket'))
                if not b.get('sport') and v.get('sport'):
                    upd['sport'] = v['sport']
                done.append((b, upd))
            elif v.get('note') and v['note'] != b.get('note'):
                done.append((b, {'note': v['note']}))
    for b, upd in done:
        if upd.get('status') and b.get('note'):
            upd['note'] = {'__delete__': True}
        if upd.get('status') and not b.get('sport') and _sport(b):
            upd.setdefault('sport', _sport(b))
        db.merge(f"ledger/bets/{b['_id']}", upd)
    settled = [b.get('label', '')[:40] + ' ' + u['status'] for b, u in done if u.get('status')]
    out.append(f"bets settled: {len(settled)}" + (f" ({'; '.join(settled)})" if settled else '') + f"; {len(bets) - len(settled)} still open")
    return '; '.join(out)


# ---------- closing lines (CLV) ----------
CLOSE_ODDS = {'NBA': 'points-all-game-ou-over,points-home-game-sp-home,points-PLAYER_ID-game-ou-over,rebounds-PLAYER_ID-game-ou-over,assists-PLAYER_ID-game-ou-over,threePointersMade-PLAYER_ID-game-ou-over,points+rebounds+assists-PLAYER_ID-game-ou-over',
              'NFL': 'points-all-game-ou-over,points-home-game-sp-home,passing_yards-PLAYER_ID-game-ou-over,rushing_yards-PLAYER_ID-game-ou-over,receiving_yards-PLAYER_ID-game-ou-over'}
SINGLE_MKT = {'pts': 'pts', 'reb': 'reb', 'ast': 'ast', 'fg3': 'fg3', 'pra': 'pra', 'pass': 'pass', 'rush': 'rush', 'rec': 'rec'}


def closing(db):
    n = now_utc(); a, b = iso(n - D(hours=30)), iso(n - D(hours=3))
    evs = []
    for L in ('NBA', 'NFL'):
        try:
            evs.append(sgo.fetch(L, a, b, CLOSE_ODDS[L]))
        except Exception:  # noqa: BLE001
            pass
    cl = close_build.closing_lines(evs)
    picks = db.list('ledger/picks')
    ups = close_build.pick_updates(cl, picks, a)
    for pid, u in ups.items():
        db.merge(f'ledger/picks/{pid}', u)
    # personal single bets: player props and game totals
    idx = {(c['league'], norm(c['player']).replace(' ', ''), c['market']): c for c in cl if c['player']}
    nb = 0
    day0 = et_date(n - D(hours=54))
    for bt in db.list('ledger/bets'):
        if bt.get('closeLine') is not None or (bt.get('legs') or 1) != 1 or (bt.get('date') or '') < day0:
            continue
        legs = parse_label(bt.get('label', ''))
        c = side = line = None
        if legs and legs[0]['market'] in SINGLE_MKT:
            L = legs[0]
            c = idx.get(((bt.get('sport') or league_of(L)).upper(), norm(L['player']).replace(' ', ''), L['market']))
            if not c:  # last-name-only labels
                last = (norm(L['player']).split() or [''])[-1]
                cands = [v for k, v in idx.items() if last and k[2] == L['market'] and norm(v['player']).split()[-1:] == [last]]
                c = cands[0] if len(cands) == 1 else None
            side, line = L['side'], L['line']
        else:
            m = re.search(r'\b(over|under|o|u)\s*([\d.]+)\s*$', bt.get('label', ''), re.I)
            if m:
                tok = lambda t: {w for w in norm(re.sub(r'[^A-Za-z]+', ' ', t)).split() if len(w) >= 4}
                words = tok(bt.get('label', ''))
                cands = [x for x in cl if x['market'] == 'total' and words & tok(x['game'])]
                c = cands[0] if len(cands) == 1 else None
                side, line = ('over' if m.group(1).lower().startswith('o') else 'under'), float(m.group(2))
        if c and line is not None:
            clv = round((c['close'] - line) if side == 'over' else (line - c['close']), 1)
            db.merge(f"ledger/bets/{bt['_id']}", {'closeLine': c['close'], 'clv': clv}); nb += 1
    try:
        u = sgo.usage()
        db.set('status', {'sgo': {**u, 'checkedAt': iso(now_utc())}, 'closingRunAt': iso(now_utc())})
    except Exception:  # noqa: BLE001
        u = {}
    vals = [x['clv'] for x in ups.values()]
    return f"{len(ups)} picks and {nb} bets given CLV" + (f", avg {sum(vals) / len(vals):+.1f}" if vals else '') + (f"; SportsGameOdds {u.get('used')}/{u.get('max')}" if u else '')


# ---------- weekly recap (Monday) ----------
def _net(b):
    d = dec(amer(b.get('odds'))) or 1
    risk = 0 if b.get('bonus') else (b.get('stake') or 0)
    st = b.get('status')
    if st == 'won':
        return (b.get('stake') or 0) * (d - 1) * (1 + (b.get('boost') or 0) / 100)
    if st == 'lost':
        return -risk
    if st == 'cashout':
        return (b.get('cashout') or 0) - risk
    return 0.0


def recap(db):
    t = now_et().date()
    end = t - D(days=t.weekday() + 1); start = end - D(days=6)
    inwk = lambda x: start.isoformat() <= (x.get('date') or '') <= end.isoformat()
    bets = db.list('ledger/bets'); picks = db.list('ledger/picks'); unit = ((db.get('settings') or {}).get('unit') or 0)
    wk = [b for b in bets if inwk(b) and b.get('status') in ('won', 'lost', 'push', 'cashout')]
    W = sum(b['status'] == 'won' for b in wk); L = sum(b['status'] == 'lost' for b in wk); P = sum(b['status'] == 'push' for b in wk)
    net = sum(_net(b) for b in wk); risk = sum(0 if b.get('bonus') else (b.get('stake') or 0) for b in wk if b['status'] != 'push')
    money = lambda v: ('+' if v >= 0 else '−') + f'${abs(v):,.2f}'
    u = lambda v: f' ({v / unit:+.1f}u)' if unit else ''
    lines = [f'You: {W}–{L}' + (f'–{P}' if P else '') + f', {money(net)}{u(net)}' + (f', ROI {100 * net / risk:+.0f}%' if risk else '')]
    if wk:
        best = max(wk, key=_net); worst = min(wk, key=_net)
        if _net(best) > 0:
            lines.append(f"Best win: {best.get('label', '')[:40]} {money(_net(best))}")
        if _net(worst) < 0:
            lines.append(f"Worst loss: {worst.get('label', '')[:40]} {money(_net(worst))}")
    sports = {}
    for b in wk:
        s = _sport(b) or 'Other'; sports[s] = sports.get(s, 0) + _net(b)
    if len(sports) > 1:
        lines.append('By sport: ' + ', '.join(f'{k} {money(v)}' for k, v in sorted(sports.items(), key=lambda kv: -kv[1])))
    lm = [b.get('lostMarket') for b in wk if b['status'] == 'lost' and (b.get('legs') or 1) > 1 and b.get('lostMarket')]
    if lm:
        top = max(set(lm), key=lm.count); lines.append(f'Parlays mostly died on {top} ({lm.count(top)} of {len(lm)})')
    cl = [b['clv'] for b in wk if b.get('clv') is not None]
    if cl:
        lines.append(f'Beat the close on {sum(c > 0 for c in cl)} of {len(cl)} (avg {sum(cl) / len(cl):+.1f})')
    pk = [p for p in picks if inwk(p) and p.get('source') != 'asked' and p.get('status') in ('won', 'lost', 'push')]
    if pk:
        pu = sum((dec(amer(p.get('odds'))) or 1.91) - 1 if p['status'] == 'won' else -1 if p['status'] == 'lost' else 0 for p in pk)
        lines.append(f"App best bets: {sum(p['status'] == 'won' for p in pk)}–{sum(p['status'] == 'lost' for p in pk)}, {pu:+.1f}u")
    allb = [b for b in bets if b.get('status') in ('won', 'lost', 'push', 'cashout')]
    lines.append(f"Season: {sum(b['status'] == 'won' for b in allb)}–{sum(b['status'] == 'lost' for b in allb)}, {money(sum(_net(b) for b in allb))}")
    if not wk and not pk:
        lines = ['Quiet week: no settled bets or graded picks.']
    title = f"Week of {start.strftime('%b')} {start.day}"
    db.set('recap', {'title': title, 'generatedAt': iso(now_utc()), 'week': {'start': start.isoformat(), 'end': end.isoformat()},
                     'stats': {'w': W, 'l': L, 'p': P, 'net': round(net, 2)}, 'text': '\n'.join(lines)})
    notify(f'Week recap: {lines[0]}', '\n'.join(lines[1:4]))
    return lines[0]


# ---------- Sunday live updates ----------
def live(db):
    bets = [b for b in db.list('ledger/bets') if b.get('status') == 'open']
    if not bets:
        return 'No open bets.'
    today = now_et().strftime('%Y%m%d')
    stats = {}
    for lg in ('nfl', 'nba'):
        for g in espn.scoreboard(lg, today):
            if g['state'] in ('live', 'final'):
                bx = espn.box(lg, g['id'])
                for p in bx['players']:
                    stats.setdefault(norm(p['name']), (p, bx['state'], bx['detail'], lg))
    if not stats:
        return 'No games in progress for open bets.'
    alerts, changed = [], 0
    for b in bets:
        legs = parse_label(b.get('label', ''))
        if not legs:
            continue
        parts, res = [], []  # res: (grade or None, game final?)
        for leg in legs:
            n = norm(leg['player'])
            same_last = [v for k, v in stats.items() if k.split()[-1:] == n.split()[-1:] and (len(n.split()) == 1 or k[:3] == n[:3])]
            hit = stats.get(n) or (same_last[0] if len(same_last) == 1 else None)
            if not hit:
                parts.append(f"{leg['player'].split()[-1]} ?"); res.append((None, False)); continue
            p, state, detail, lg = hit
            v = 0 if p.get('dnp') else (stat_value(p['stats'], leg['market']) or 0)
            g = grade(leg, v); final = state == 'final'
            if final or (leg['side'] == 'over' and g == 'won') or (leg['side'] == 'under' and g == 'lost'):
                parts.append(f"{leg['player'].split()[-1]} {'✓' if g == 'won' else '✗'} {v:g}"); res.append((g, final or g == 'won'))
            else:
                parts.append(f"{leg['player'].split()[-1]} {v:g}/{leg['line']:g} ({detail})"); res.append(('live', False))
        text = ' · '.join(parts)[:160]
        upd = {}
        if text != b.get('live'):
            upd.update(live=text, liveAt=iso(now_utc()))
        if all(g == 'won' and f for g, f in res) and all(stats.get(norm(l['player'])) and stats[norm(l['player'])][1] == 'final' for l in legs):
            upd.update(status='won', settledBy='auto')
        elif any(g == 'lost' and f for g, f in res):
            upd.update(status='lost', settledBy='auto')
        open_legs = [i for i, (g, f) in enumerate(res) if g in ('live', None)]
        if (b.get('legs') or 1) >= 2 and not b.get('alertedOneLeg') and len(open_legs) == 1 and all(g == 'won' for i, (g, f) in enumerate(res) if i != open_legs[0]):
            alerts.append(f"{b.get('label', '')[:40]} ({b.get('odds')}): waiting on {parts[open_legs[0]]}")
            upd['alertedOneLeg'] = True
        if upd:
            db.merge(f"ledger/bets/{b['_id']}", upd); changed += 1
    if alerts:
        notify('One leg away', '\n'.join(alerts))
    return f'{changed} bets updated' + (f'; alert sent for {len(alerts)}' if alerts else '')
