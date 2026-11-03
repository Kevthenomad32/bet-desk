"""Where book lines come from: TheRundown (Hard Rock + Pinnacle) when THERUNDOWN_API_KEY is set, else SportsGameOdds
(consensus, free tier, budget-guarded). Both return the same odds document shape."""
from . import rundown, sgo
from .common import norm

SGO_CODE = {'nba': 'NBA', 'nfl': 'NFL', 'cfb': 'NCAAF', 'cbb': 'NCAAB'}
_teams = {}


def team_lookup(db, lg):
    """Player name -> current team (nickname), from this season's box-score archive (latest game wins)."""
    if lg not in _teams:
        m = {}
        if lg in ('nba', 'nfl'):
            try:
                for r in sorted(db.select('player_games', league=f'eq.{lg}', select='name,team,date'), key=lambda r: r['date']):
                    m[norm(r['name'])] = r['team']
            except Exception:  # noqa: BLE001
                pass
        _teams[lg] = m
    m = _teams[lg]
    return lambda name: m.get(norm(name))


def odds_doc(db, lg, a, b, players, kind='normal'):
    """(doc, note). Raises when neither source can be used."""
    notes = []
    if rundown.enabled():
        try:
            return rundown.fetch_doc(lg, a, b, players, team_lookup(db, lg)), ''
        except Exception as e:  # noqa: BLE001
            print(f'::warning title=TheRundown {lg}::{str(e)[:300]} (used SportsGameOdds instead)')
            notes.append(f'TheRundown failed ({str(e)[:120]})')
    ok, why = sgo.guard(db, kind)
    if not ok:
        raise RuntimeError('; '.join(notes + [why]))
    evs = sgo.fetch(SGO_CODE[lg], a, b)
    return sgo.build(lg if lg != 'cbb' else 'cfb', evs, players), '; '.join(notes)
