"""Reading bet legs from free-text labels and grading them from box-score stats."""
import re

from .common import NBA_AB, NFL_AB, key2, norm

# order matters: combos before single stats
LEG_MKT = [(r'p\s*\+\s*r\s*\+\s*a|\bpra\b', 'pra'), (r'p\s*\+\s*r\b|pts\s*\+\s*reb', 'pr'), (r'p\s*\+\s*a\b|pts\s*\+\s*ast', 'pa'),
           (r'r\s*\+\s*a\b|reb\s*\+\s*ast', 'ra'), (r'3pm|3pt|three|\b3s\b|3-pointers', 'fg3'), (r'reb', 'reb'), (r'ast|assist', 'ast'),
           (r'pts|point', 'pts'), (r'pass', 'pass'), (r'rush', 'rush'), (r'receptions|\brecs\b|catches', 'recs'), (r'rec|receiv', 'rec')]
NBA_MKTS = {'pts', 'reb', 'ast', 'fg3', 'pra', 'pr', 'pa', 'ra'}
NFL_MKTS = {'pass', 'rush', 'rec', 'recs', 'td'}
SPLIT = re.compile(r'\s*(?:,|;|\n|\s\+\s|\s/\s)\s*')
CATEGORY = {'pass': 'Passing yds', 'rush': 'Rushing yds', 'rec': 'Receiving yds', 'td': 'Anytime TD', 'pts': 'Points', 'reb': 'Rebounds', 'ast': 'Assists',
            'fg3': '3-pointers', 'pra': 'PRA', 'pr': 'Other', 'pa': 'Other', 'ra': 'Other', 'recs': 'Other'}


def parse_leg(t):
    t = t.strip()
    if not t:
        return None
    m = re.match(r'^(.+?)\s+(?:anytime\s*td|atd|anytime touchdown|to score a td)\s*$', t, re.I)
    if m:
        return {'player': m.group(1).strip(), 'market': 'td', 'side': 'over', 'line': 0.5, 'text': t}
    m = re.match(r'^(.+?)\s+(o|u|over|under)\s*([\d.]+)\+?\s*(.*)$', t, re.I)
    if not m:
        return None
    mk = next((k for rx, k in LEG_MKT if re.search(rx, m.group(4), re.I)), None)
    if not mk:
        return None
    player = re.sub(r'\s*\((?:[A-Z]{2,4}\s*@\s*[A-Z]{2,4}|vs?\.? .*)\)\s*$', '', m.group(1)).strip()
    if any(norm(player) == norm(n) for n in list(NBA_AB.values()) + list(NFL_AB.values())) or re.search(r'team total|/', player, re.I):
        return None  # team totals / game totals: not a player leg
    return {'player': player, 'side': 'over' if m.group(2).lower().startswith('o') else 'under', 'line': float(m.group(3)), 'market': mk, 'text': t}


def parse_label(label):
    label = re.sub(r'\([^)]*@[^)]*\)', '', label or '')
    if ':' in label[:40]:
        label = label.split(':', 1)[1]  # 'SGP 7-leg (POR@LAL): ...'
    parts = [p for p in SPLIT.split(label) if p.strip()]
    legs = [parse_leg(p) for p in parts]
    return legs if legs and all(legs) else None


def league_of(leg):
    return 'nba' if leg['market'] in NBA_MKTS else 'nfl'


def stat_value(stats, market):
    g = lambda k: stats.get(k) or 0
    return {'pra': g('pts') + g('reb') + g('ast'), 'pr': g('pts') + g('reb'), 'pa': g('pts') + g('ast'), 'ra': g('reb') + g('ast'),
            'td': g('rushTd') + g('recTd')}.get(market, stats.get(market) if market in stats else (0 if stats else None))


def grade(leg, value):
    if value is None:
        return None
    if leg['market'] == 'td':
        return 'won' if value >= 1 else 'lost'
    if value == leg['line']:
        return 'push'
    return 'won' if (leg['side'] == 'over') == (value > leg['line']) else 'lost'


def find_player(rows, name):
    """rows: player_games rows; exact normalized name, else first-3-letters + last name, else unique last name."""
    n = norm(name)
    hit = [r for r in rows if r['name_norm'] == n] or [r for r in rows if key2(r['name_norm']) == key2(n)]
    if not hit and len(n.split()) == 1:
        hit = [r for r in rows if r['name_norm'].split()[-1:] == [n]]
        if len({r['player_id'] for r in hit}) > 1:
            return None
    return hit[0] if hit else None
