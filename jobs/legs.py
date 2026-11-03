"""Reading bet legs from free-text labels and grading them from box-score stats."""
import re

from .common import NBA_AB, NFL_AB, key2, norm

# order matters: combos before single stats
LEG_MKT = [(r'p\s*\+\s*r\s*\+\s*a|\bpra\b|points\s*\+\s*rebounds\s*\+\s*assists|pts\s*\+\s*reb\s*\+\s*ast', 'pra'),
           (r'p\s*\+\s*r\b|pts\s*\+\s*reb|points\s*\+\s*rebounds', 'pr'), (r'p\s*\+\s*a\b|pts\s*\+\s*ast|points\s*\+\s*assists', 'pa'),
           (r'r\s*\+\s*a\b|reb\s*\+\s*ast|rebounds\s*\+\s*assists', 'ra'), (r'3pm|3pt|three|\b3s\b|3-pointers', 'fg3'),
           (r'steal', 'stl'), (r'block', 'blk'), (r'\breb', 'reb'), (r'\bast\b|assist', 'ast'), (r'\bpts?\b|point', 'pts'),
           (r'pass(ing)?\s*(td|touchdown)', 'passTd'), (r'pass(ing)?\s*att', 'att'), (r'complet', 'cmp'), (r'intercept|\bints?\b', 'int'),
           (r'rush(ing)?\s*att|carries', 'car'), (r'rush(ing)?\s*\+\s*rec', 'rr'), (r'pass', 'pass'), (r'rush', 'rush'),
           (r'receptions|\brecs\b|catches', 'recs'), (r'rec|receiv', 'rec')]
NBA_MKTS = {'pts', 'reb', 'ast', 'fg3', 'pra', 'pr', 'pa', 'ra', 'stl', 'blk', 'dd', 'td3'}
NFL_MKTS = {'pass', 'rush', 'rec', 'recs', 'td', 'passTd', 'att', 'cmp', 'int', 'car', 'rr'}
SPLIT = re.compile(r'\s*(?:,|;|\n|\s\+\s|\s/\s)\s*')
CATEGORY = {'pass': 'Passing yds', 'rush': 'Rushing yds', 'rec': 'Receiving yds', 'td': 'Anytime TD', 'pts': 'Points', 'reb': 'Rebounds', 'ast': 'Assists',
            'fg3': '3-pointers', 'pra': 'PRA', 'pr': 'Other', 'pa': 'Other', 'ra': 'Other', 'recs': 'Other', 'dd': 'Double-double', 'td3': 'Triple-double'}


def parse_leg(t):
    t = t.strip()
    if not t:
        return None
    m = re.match(r'^(.+?)\s+(?:anytime\s*td|atd|anytime touchdown|to score a td)(?:\s*scorer)?\s*$', t, re.I)
    if m:
        return {'player': m.group(1).strip(), 'market': 'td', 'side': 'over', 'line': 0.5, 'text': t}
    m = re.match(r'^(.+?)\s+(?:to record (?:a |an )?)?(triple[\s-]*double|double[\s-]*double)\s*(?:yes)?\s*$', t, re.I)
    if m:  # double-double / triple-double: counted from points, rebounds, assists, steals, blocks
        return {'player': m.group(1).strip(), 'market': 'td3' if m.group(2).lower().startswith('t') else 'dd', 'side': 'over', 'line': 0.5, 'text': t}
    m = re.match(r'^(.+?)\s+(?:to record\s+)?(\d+)\+\s*(.*)$', t, re.I)
    if m:  # 'Jokic 20+ pts' / 'to record 20+ points' = over 19.5
        mk = next((k for rx, k in LEG_MKT if re.search(rx, m.group(3), re.I)), None)
        if mk:
            return {'player': m.group(1).strip(), 'side': 'over', 'line': int(m.group(2)) - 0.5, 'market': mk, 'text': t}
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


BASIC = {'pts', 'reb', 'ast', 'fg3', 'stl', 'blk', 'pass', 'rush', 'rec', 'recs'}  # a missing basic stat means 0; anything else unknown = can't grade


def stat_value(stats, market):
    if not stats:
        return None
    g = lambda k: stats.get(k) or 0
    if market in ('dd', 'td3'):
        return sum(1 for k in ('pts', 'reb', 'ast', 'stl', 'blk') if g(k) >= 10)
    combo = {'pra': g('pts') + g('reb') + g('ast'), 'pr': g('pts') + g('reb'), 'pa': g('pts') + g('ast'), 'ra': g('reb') + g('ast'),
             'td': g('rushTd') + g('recTd'), 'rr': g('rush') + g('rec')}
    if market in combo:
        return combo[market]
    if market in stats:
        return stats[market]
    return 0 if market in BASIC else None


def grade(leg, value):
    if value is None:
        return None
    if leg['market'] == 'td':
        return 'won' if value >= 1 else 'lost'
    if leg['market'] in ('dd', 'td3'):
        return 'won' if value >= (2 if leg['market'] == 'dd' else 3) else 'lost'
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
