"""Morning mismatch alert: a top offense playing one of the league's worst defenses tonight (Kev, Nov 26 2026).

Uses the 'nba' team doc (offense/defense ratings per 100 possessions, this season's box scores). A game is flagged when a
top-N offense faces a bottom-N defense, in either direction. Each game is alerted once (backfills rerun nba_daily)."""
from .common import notify

TOP_N = 10  # top 10 offenses vs the 10 worst defenses


def ranks(teams):
    """({team: offense rank, 1 = best}, {team: defense rank, 1 = best}) by rating."""
    off = sorted(teams, key=lambda t: -(teams[t].get('off') or {}).get('ortg', 0))
    dfn = sorted(teams, key=lambda t: (teams[t].get('def') or {}).get('drtg', 999))
    return {t: i + 1 for i, t in enumerate(off)}, {t: i + 1 for i, t in enumerate(dfn)}


def mismatches(teams, games, n=TOP_N):
    """[(offense team, defense team, game dict)] for tonight's games. games = [{away, home, time}]."""
    if len(teams) < 2 * n:
        return []
    ro, rd = ranks(teams); worst = len(teams) - n
    out = []
    for g in games:
        for o, d in ((g['away'], g['home']), (g['home'], g['away'])):
            if o in ro and d in rd and ro[o] <= n and rd[d] > worst:
                out.append((o, d, g))
    return out


def line(teams, o, d, g):
    ro, rd = ranks(teams)
    return (f"{o} (#{ro[o]} offense, {teams[o]['off']['ortg']:.1f}) vs {d} (#{rd[d]} defense, {teams[d]['def']['drtg']:.1f}) · "
            f"{g['away']} @ {g['home']} {g.get('time') or ''}").strip()


def alert(db, teams, games, day):
    """Sends the morning alert for new mismatches and stores today's list in the 'mismatches' doc. Returns a summary."""
    found = mismatches(teams, games)
    old = db.get('mismatches') or {}
    sent = set(old.get('alerted') or [])
    rows = [{'offense': o, 'defense': d, 'game': f"{g['away']} @ {g['home']}", 'time': g.get('time'), 'text': line(teams, o, d, g)} for o, d, g in found]
    new = [r for r in rows if f"{day}|{r['offense']}|{r['defense']}" not in sent]
    if new:
        notify(f"Mismatch tonight: {len(new)} game{'s' if len(new) > 1 else ''}", '\n'.join(r['text'] for r in new))
        sent |= {f"{day}|{r['offense']}|{r['defense']}" for r in new}
    db.set('mismatches', {'date': day, 'topN': TOP_N, 'rows': rows, 'alerted': sorted(sent)[-200:]})
    return f"mismatches: {len(rows)}" + (f" ({len(new)} alerted)" if new else '')
