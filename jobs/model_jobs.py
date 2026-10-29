"""Hourly model runs: pre-game refresh + shadow log (props), NBA spreads & totals locks."""
import json
import os
import subprocess
import sys
import tempfile

from . import sgo
from .data_jobs import build_injuries, merged_injuries
from .model import gl_build

HERE = os.path.dirname(os.path.abspath(__file__))
PICKS_N = int(os.environ.get('BETDESK_PICKS_N') or '0')  # 0 = best-bet locking paused (log every prop for grading only)


def _dump(path, obj):
    with open(path, 'w') as f:
        json.dump(obj if obj is not None else {}, f)


def pregame(db):
    a, b = sgo.window('pre')
    out = []
    for lg, LEAGUE in (('nba', 'NBA'), ('nfl', 'NFL')):
        old = db.get(f'{lg}Odds')
        if not sgo.due(old, a, b):
            continue
        pdoc = db.get(f'{lg}Players') or {}
        odds = old
        try:
            evs = sgo.fetch(LEAGUE, a, b)
            if evs:
                odds = sgo.merge(old, sgo.build(lg, evs, pdoc.get('players', [])))
                db.set(f'{lg}Odds', odds)
        except Exception as e:  # noqa: BLE001
            out.append(f'{LEAGUE} odds not refreshed ({e}); using the last lines')
        inj = build_injuries(db, lg, (db.get('slate') or {}).get('week') if lg == 'nfl' else None)
        db.set(f'{lg}Injuries', inj)
        inj = merged_injuries(db, inj)
        with tempfile.TemporaryDirectory() as W:
            f = lambda n: os.path.join(W, n)
            _dump(f('odds.json'), odds); _dump(f('teams.json'), db.get('nba' if lg == 'nba' else 'defense')); _dump(f('players.json'), pdoc)
            _dump(f('inj.json'), inj); _dump(f('weights.json'), db.get('modelWeights')); _dump(f('slate.json'), db.get('nbaSlate' if lg == 'nba' else 'slate'))
            _dump(f('opt.json'), [])
            os.makedirs(f('locked')); os.makedirs(f('picks'))
            for p in db.list('ledger/picks'):
                _dump(os.path.join(f('locked'), p['_id'] + '.json'), p)
            r = subprocess.run([sys.executable, '-I', os.path.join(HERE, 'model', 'picks_build.py'), lg, f('odds.json'), f('teams.json'), f('players.json'), f('inj.json'),
                                f('weights.json'), f('slate.json'), f('opt.json'), f('locked'), f('picks'), a, b, str(PICKS_N)], capture_output=True, text=True)
            if r.returncode:
                raise RuntimeError(r.stderr[-1500:])
            existing = {x['_id'] for x in db.list('ledger/shadow')}
            sd = os.path.join(f('picks'), 'shadow'); props = 0; games = 0
            for fn in sorted(os.listdir(sd)) if os.path.isdir(sd) else []:
                sid = fn[:-5]
                if sid in existing:
                    continue
                doc = json.load(open(os.path.join(sd, fn)))
                db.set(f'ledger/shadow/{sid}', doc); props += len(doc['rows']); games += 1
            locked = []
            for fn in sorted(os.listdir(f('picks'))):
                if fn.endswith('.json'):
                    doc = json.load(open(os.path.join(f('picks'), fn)))
                    db.set(f'ledger/picks/{fn[:-5]}', doc)
                    locked.append(f"{doc['player']} {'o' if doc['side'] == 'over' else 'u'}{doc['line']} ({doc['odds']}, {doc['pWin']:.0%})")
            out.append(f'{LEAGUE}: logged {props} props across {games} games' + (f"; locked {', '.join(locked)}" if locked else ''))
            if locked:
                from .common import notify
                notify('Best bets locked', ', '.join(locked))
    return '; '.join(out) or 'Nothing due.'


def nbagl(db):
    a, b = sgo.window('pre')
    odds = db.get('nbaOdds')
    if not sgo.due(odds, a, b):
        return 'Nothing due.'
    teams = (db.get('nba') or {}).get('teams', {})
    with tempfile.TemporaryDirectory() as W:
        n = gl_build.build(gl_build.odds_rows(odds), teams, a, b, W)
        existing = {x['_id'] for x in db.list('ledger/nbagl')}
        locked = 0
        for fn in sorted(os.listdir(W)):
            if fn.endswith('.json') and fn[:-5] not in existing:
                db.set(f'ledger/nbagl/{fn[:-5]}', json.load(open(os.path.join(W, fn)))); locked += 1
    return f'{locked} games locked ({n} priced)'
