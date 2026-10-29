"""Data refresh jobs: NBA daily, NFL weekly, injuries, college lines. Model inputs come from StatMuse (when STATMUSE_API_KEY is set),
with ESPN + the games archive as the fallback."""
import datetime as dt

from . import espn, sgo, statmuse, stats
from .common import ET, et_date, iso, now_et, now_utc

YMD = lambda d: d.strftime('%Y%m%d')


def warn(what, e):
    """Shows on the GitHub run page; the job carries on with ESPN."""
    print(f'::warning title={what}::{str(e)[:300]} (used ESPN instead)')


def archive_range(db, lg, start, end):
    """Archive every final game between two ET dates (inclusive)."""
    n, d = 0, start
    while d <= end:
        chunk_end = min(d + dt.timedelta(days=6), end)
        try:
            games = espn.scoreboard(lg, f'{YMD(d)}-{YMD(chunk_end)}')
        except RuntimeError:
            # ESPN sometimes refuses date ranges: go day by day, skipping days it still refuses
            games, day = [], d
            while day <= chunk_end:
                try:
                    games += espn.scoreboard(lg, YMD(day))
                except RuntimeError as e:
                    print(f'::warning title=ESPN {lg} {YMD(day)}::{e}')
                day += dt.timedelta(days=1)
        n += espn.archive_games(db, lg, games)
        d = chunk_end + dt.timedelta(days=1)
    return n


def season_rows(db, lg, season, season_type=2):
    games = db.select('games', league=f'eq.{lg}', season=f'eq.{season}', season_type=f'eq.{season_type}', status='eq.final', select='id,date,week,home,away,home_score,away_score,box')
    rows = db.select('player_games', league=f'eq.{lg}', season=f'eq.{season}', select='player_id,name,team,date,starter,dnp,stats,game_id')
    ids = {g['id'] for g in games}
    return games, [r for r in rows if r['game_id'] in ids]


# ---------- injuries ----------
def build_injuries(db, lg, keep_week=None):
    src = 'ESPN injury report'
    rows = None
    if statmuse.enabled():
        try:
            rows = statmuse.injuries(lg, keep_week); src = 'StatMuse injury report'
        except Exception as e:  # noqa: BLE001
            warn(f'StatMuse {lg} injuries', e)
    if rows is None:
        rows = espn.injuries(lg)
    doc = {'source': src, 'updated': iso(now_utc()), 'players': rows}
    if keep_week:
        doc['week'] = keep_week
    return doc


def merged_injuries(db, doc):
    """Today's manual corrections (doc 'injOverride', same as the app uses) layered on the ESPN report, for the model."""
    ov = db.get('injOverride') or {}
    if ov.get('date') != now_et().strftime('%Y-%m-%d'):
        return doc
    by = {x['name'].lower(): x for x in doc.get('players', [])}
    for x in ov.get('players') or []:
        by[x['name'].lower()] = {**by.get(x['name'].lower(), {}), **x}
    return {**doc, 'players': list(by.values())}


def injuries_job(db):
    nba = build_injuries(db, 'nba')
    db.set('nbaInjuries', nba)
    msg = [f"NBA: {len(nba['players'])} listed"]
    if now_et().weekday() in (2, 3, 4, 5, 6, 0):  # Wed-Mon: NFL week
        slate = db.get('slate') or {}
        nfl = build_injuries(db, 'nfl', slate.get('week'))
        db.set('nflInjuries', nfl)
        msg.append(f"NFL: {len(nfl['players'])} listed")
    return '; '.join(msg)


# ---------- NBA daily ----------
def nba_daily(db):
    t = now_et(); today = t.date(); season, _ = stats.season_years()
    last = db.select('games', league='eq.nba', season=f'eq.{season}', status='eq.final', select='date', order='date.desc', limit='1')
    start = dt.date.fromisoformat(last[0]['date']) - dt.timedelta(days=1) if last else dt.date(today.year if today.month >= 10 else today.year - 1, 10, 1)
    archived = archive_range(db, 'nba', start, today - dt.timedelta(days=1))
    games, rows = season_rows(db, 'nba', season)
    out = []
    teams, tsrc = None, 'ESPN box scores'
    if statmuse.enabled():
        try:
            teams = statmuse.nba_team_doc(season); tsrc = 'StatMuse'
            if len(teams) != 30:
                warn('StatMuse NBA team stats', f'only {len(teams)} teams'); teams = None; tsrc = 'ESPN box scores'
        except Exception as e:  # noqa: BLE001
            warn('StatMuse NBA team stats', e)
    teams = teams or stats.nba_team_doc(games)
    if len(teams) == 30:
        db.set('nba', {'source': tsrc, 'updated': today.isoformat(), 'season': f'{season - 1}-{str(season)[2:]}', 'mode': f'{season - 1}-{str(season)[2:]} only',
                       'curWeight': 1, 'teams': teams})
        out.append(f'team stats for 30 teams ({tsrc})')
    else:
        out.append(f'team stats skipped: only {len(teams)} teams have games')
    # slate + back-to-backs
    sb_today = [g for g in espn.scoreboard('nba', YMD(today)) if g['state'] != 'off' and g['season_type'] in (2, 3)]
    sb_yday = espn.scoreboard('nba', YMD(today - dt.timedelta(days=1)))
    played_y = {x for g in sb_yday for x in (g['home'], g['away'])}
    playing = {x for g in sb_today for x in (g['home'], g['away'])}
    db.set('nbaSlate', {'date': today.isoformat(), 'source': 'ESPN', 'games': [{'away': g['away'], 'home': g['home'], 'time': espn.et_time(g['start'])} for g in sb_today],
                        'b2b': sorted(playing & played_y)})
    out.append(f'{len(sb_today)} games today, b2b: {", ".join(sorted(playing & played_y)) or "none"}')
    # players + injury pool
    players = None; psrc = 'ESPN box scores'
    if statmuse.enabled():
        try:
            players, gp = statmuse.nba_players(season); psrc = 'StatMuse'
            if len(players) < 100:
                warn('StatMuse NBA players', f'only {len(players)} players'); players = None; psrc = 'ESPN box scores'
        except Exception as e:  # noqa: BLE001
            warn('StatMuse NBA players', e)
    if players is None:
        players, gp = stats.nba_players(rows)
    inj = merged_injuries(db, db.get('nbaInjuries') or build_injuries(db, 'nba'))
    pdoc = {'updated': iso(now_utc()), 'source': psrc, 'players': players, 'pool': stats.nba_pool(players, gp, playing, inj.get('players', []))}
    db.set('nbaPlayers', pdoc)
    out.append(f"{len(players)} player averages ({psrc}), pool {len(pdoc['pool'])}")
    # book lines
    if sb_today:
        try:
            a, b = sgo.window('nba')
            odds = sgo.build('nba', sgo.fetch('NBA', a, b), players)
            db.set('nbaOdds', odds)
            out.append(f"{len(odds['props'])} props ({sum(p['avg'] is not None for p in odds['props'])} with averages)")
        except Exception as e:  # noqa: BLE001
            out.append(f'odds skipped: {e}')
    out.insert(0, f'archived {archived} games')
    return '; '.join(out)


# ---------- NFL weekly (Wednesday) + line refreshes (Thu/Sun/Mon mornings) ----------
def nfl_lines(db, players=None):
    players = players if players is not None else (db.get('nflPlayers') or {}).get('players', [])
    a, b = sgo.window('nfl')
    odds = sgo.build('nfl', sgo.fetch('NFL', a, b), players)
    db.set('nflOdds', odds)
    return f"{len(odds['games'])} games, {len(odds['props'])} props"


def nfl_weekly(db):
    week, year, stype = espn.current_nfl_week()
    _, season = stats.season_years()
    out = []
    # archive the finished weeks (cheap: already-archived games are skipped)
    n = 0
    for w in range(1, (week or 1)):
        n += espn.archive_games(db, 'nfl', espn.scoreboard('nfl', str(season), seasontype=2, week=w))
    out.append(f'archived {n} games')
    games, rows = season_rows(db, 'nfl', season)
    teams, through = stats.nfl_defense_doc(games)
    dsrc = 'ESPN box scores'
    if statmuse.enabled():
        try:
            smt = statmuse.nfl_defense(season)
            if len(smt) == 32:
                # StatMuse has no 3rd-down / red-zone rates: keep those from the box scores
                teams = {t: {**v, 'third': (teams.get(t) or {}).get('third'), 'rz': (teams.get(t) or {}).get('rz')} for t, v in smt.items()}
                dsrc = 'StatMuse + ESPN box scores (3rd down/red zone)'
            else:
                warn('StatMuse NFL defense', f'only {len(smt)} teams')
        except Exception as e:  # noqa: BLE001
            warn('StatMuse NFL defense', e)
    if len(teams) == 32:
        db.set('defense', {'source': dsrc, 'updated': now_et().strftime('%Y-%m-%d'), 'weekThrough': through, 'teams': teams})
        out.append(f'defense through Week {through}')
    else:
        out.append(f'defense skipped: {len(teams)} teams')
    up = [g for g in espn.scoreboard('nfl', str(season), seasontype=2, week=week) if g['state'] != 'off']
    playing = {x for g in up for x in (g['home'], g['away'])}
    db.set('slate', {'week': week, 'games': [{'away': g['away'], 'home': g['home'], 'time': espn.et_time(g['start'], with_day=True)} for g in up],
                     'bye': sorted(stats.ALL['nfl'] - playing)})
    out.append(f'Week {week}: {len(up)} games')
    players = None; psrc = 'ESPN box scores'
    if statmuse.enabled():
        try:
            players = statmuse.nfl_players(season); psrc = 'StatMuse'
            if len(players) < 100:
                warn('StatMuse NFL players', f'only {len(players)} players'); players = None; psrc = 'ESPN box scores'
        except Exception as e:  # noqa: BLE001
            warn('StatMuse NFL players', e)
    if players is None:
        players = stats.nfl_players(rows)
    db.set('nflPlayers', {'updated': iso(now_utc()), 'source': psrc, 'players': players})
    out.append(f'{len(players)} player averages ({psrc})')
    try:
        out.append(nfl_lines(db, players))
    except Exception as e:  # noqa: BLE001
        out.append(f'odds skipped: {e}')
    return '; '.join(out)


# ---------- college football lines (Fri/Sat mornings) ----------
def cfb_lines(db):
    a, b = sgo.window('cfb')
    doc = sgo.build('cfb', sgo.fetch('NCAAF', a, b), [])
    db.set('cfbOdds', {k: doc[k] for k in ('source', 'updated', 'games')})
    return f"{len(doc['games'])} college games"
