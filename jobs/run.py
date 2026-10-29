"""Run Bet Desk jobs.

    python -m jobs.run tick         # what the hourly schedule runs: picks the jobs due now (US Eastern clock)
    python -m jobs.run <job>        # pregame | nbagl | nba_daily | nfl_weekly | nfl_lines | injuries | settle | closing | recap | live | cfb_lines
    python -m jobs.run backfill     # first-time setup: archive this season's games, then build every data doc
"""
import datetime as dt
import sys
import traceback

from . import data_jobs, espn, ledger_jobs, model_jobs, stats
from .common import DB, now_et, now_utc

JOBS = {
    'pregame': model_jobs.pregame, 'nbagl': model_jobs.nbagl,
    'nba_daily': data_jobs.nba_daily, 'nfl_weekly': data_jobs.nfl_weekly, 'nfl_lines': lambda db: data_jobs.nfl_lines(db),
    'injuries': data_jobs.injuries_job, 'cfb_lines': data_jobs.cfb_lines,
    'settle': ledger_jobs.settle, 'closing': ledger_jobs.closing, 'recap': ledger_jobs.recap, 'live': ledger_jobs.live,
}


def due_now(t=None):
    """Jobs due at this hour (the schedule fires once an hour, at :31 UTC)."""
    t = t or now_et(); h, wd = t.hour, t.weekday()  # Mon=0 ... Sun=6
    jobs = []
    if h == 9:
        jobs += ['injuries', 'nba_daily']
        if wd == 2:
            jobs.append('nfl_weekly')
        elif wd in (3, 6, 0):
            jobs.append('nfl_lines')
        if wd == 1:
            jobs.append('recap')
        if wd in (4, 5):
            jobs.append('cfb_lines')
    if 8 <= h <= 22:
        jobs.append('pregame')
    if 10 <= h <= 23:
        jobs.append('nbagl')
    if h in (0, 2):
        jobs.append('settle')
    if h == 4:
        jobs.append('closing')
    if wd == 6 and 11 <= h <= 23:
        jobs.append('live')
    return jobs


def run(db, name):
    started = now_utc()
    try:
        summary = JOBS[name](db) or 'done'
        ok = True
    except Exception as e:  # noqa: BLE001
        summary = f'{type(e).__name__}: {e}'
        ok = False
        traceback.print_exc()
    print(f'[{name}] {"ok" if ok else "FAILED"}: {summary}')
    if not ok:
        annotate(name, summary)
    try:
        db.log_run(name, started, ok, summary)
    except Exception:  # noqa: BLE001
        pass
    return ok


def annotate(where, msg):
    # Shows on the GitHub run page (visible without opening the logs).
    tb = traceback.format_exc().strip().splitlines()
    loc = next((l.strip() for l in reversed(tb) if l.strip().startswith('File ')), '')
    print(f'::error title={where}::{str(msg)[:600]} {loc[:200]}'.replace('\n', ' '))


def backfill(db):
    nba_season, nfl_season = stats.season_years()
    today = now_et().date()
    ok = True
    try:
        n = data_jobs.archive_range(db, 'nba', dt.date(nba_season - 1, 10, 1), today - dt.timedelta(days=1))
        print(f'NBA: archived {n} games')
    except Exception as e:  # noqa: BLE001
        traceback.print_exc(); annotate('backfill NBA', f'{type(e).__name__}: {e}'); ok = False
    try:
        week, _, _ = espn.current_nfl_week()
        m = 0
        for w in range(1, (week or 1)):
            m += espn.archive_games(db, 'nfl', espn.scoreboard('nfl', str(nfl_season), seasontype=2, week=w))
        print(f'NFL: archived {m} games through Week {(week or 1) - 1}')
    except Exception as e:  # noqa: BLE001
        traceback.print_exc(); annotate('backfill NFL', f'{type(e).__name__}: {e}'); ok = False
    for j in ('injuries', 'nba_daily', 'nfl_weekly'):
        ok &= run(db, j)
    return ok


def main(argv):
    name = argv[1] if len(argv) > 1 else 'tick'
    try:
        db = DB()
    except BaseException as e:  # noqa: BLE001
        annotate('database', f'{type(e).__name__}: {e}')
        raise
    if name == 'backfill':
        return 0 if backfill(db) else 1
    jobs = due_now() if name == 'tick' else [name]
    if name != 'tick' and name not in JOBS:
        raise SystemExit(f'unknown job {name}; choose from tick, backfill, {", ".join(JOBS)}')
    if not jobs:
        print('Nothing due this hour.')
    ok = all([run(db, j) for j in jobs])
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
