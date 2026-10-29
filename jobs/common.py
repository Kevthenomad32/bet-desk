"""Shared helpers for the Bet Desk jobs: settings, the Supabase database, Eastern time, names, phone alerts."""
import datetime as dt
import json
import os
import re
import time
import unicodedata
import zoneinfo

import requests

ET = zoneinfo.ZoneInfo('America/New_York')
UTC = dt.timezone.utc


def env(name, default=None, required=True):
    v = os.environ.get(name, default)
    if required and not v:
        raise SystemExit(f'Missing setting {name} (add it under GitHub repo Settings -> Secrets and variables -> Actions)')
    return v


def now_utc():
    return dt.datetime.now(UTC)


def now_et():
    return dt.datetime.now(ET)


def iso(t):
    return t.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(s):
    return dt.datetime.fromisoformat(str(s).replace('Z', '+00:00'))


def et_date(t):
    return (parse_iso(t) if isinstance(t, str) else t).astimezone(ET).strftime('%Y-%m-%d')


def norm(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
    s = re.sub(r'[^a-z ]', '', s.replace('-', ' '))
    return ' '.join(x for x in s.split() if x not in ('jr', 'sr', 'ii', 'iii', 'iv', 'v'))


def key2(n):
    w = n.split()
    return (w[0][:3] + ' ' + w[-1]) if len(w) >= 2 else n


# ---------- teams ----------
NBA_AB = {'ATL': 'Hawks', 'BOS': 'Celtics', 'BKN': 'Nets', 'CHA': 'Hornets', 'CHI': 'Bulls', 'CLE': 'Cavaliers', 'DAL': 'Mavericks', 'DEN': 'Nuggets',
          'DET': 'Pistons', 'GSW': 'Warriors', 'HOU': 'Rockets', 'IND': 'Pacers', 'LAC': 'Clippers', 'LAL': 'Lakers', 'MEM': 'Grizzlies', 'MIA': 'Heat',
          'MIL': 'Bucks', 'MIN': 'Timberwolves', 'NOP': 'Pelicans', 'NYK': 'Knicks', 'OKC': 'Thunder', 'ORL': 'Magic', 'PHI': '76ers', 'PHX': 'Suns',
          'POR': 'Trail Blazers', 'SAC': 'Kings', 'SAS': 'Spurs', 'TOR': 'Raptors', 'UTA': 'Jazz', 'WAS': 'Wizards'}
NFL_AB = {'ARI': 'Cardinals', 'ATL': 'Falcons', 'BAL': 'Ravens', 'BUF': 'Bills', 'CAR': 'Panthers', 'CHI': 'Bears', 'CIN': 'Bengals', 'CLE': 'Browns',
          'DAL': 'Cowboys', 'DEN': 'Broncos', 'DET': 'Lions', 'GB': 'Packers', 'HOU': 'Texans', 'IND': 'Colts', 'JAX': 'Jaguars', 'KC': 'Chiefs',
          'LAC': 'Chargers', 'LAR': 'Rams', 'LV': 'Raiders', 'MIA': 'Dolphins', 'MIN': 'Vikings', 'NE': 'Patriots', 'NO': 'Saints', 'NYG': 'Giants',
          'NYJ': 'Jets', 'PHI': 'Eagles', 'PIT': 'Steelers', 'SEA': 'Seahawks', 'SF': '49ers', 'TB': 'Buccaneers', 'TEN': 'Titans', 'WAS': 'Commanders'}
# ESPN abbreviation -> StatMuse-style abbreviation used throughout the app
AB_FIX = {'nba': {'GS': 'GSW', 'NO': 'NOP', 'NY': 'NYK', 'SA': 'SAS', 'UTAH': 'UTA', 'WSH': 'WAS', 'PHO': 'PHX', 'BK': 'BKN'},
          'nfl': {'WSH': 'WAS', 'JAC': 'JAX', 'LA': 'LAR'}}


def fix_ab(lg, ab):
    return AB_FIX.get(lg, {}).get(ab, ab)


def nick(lg, ab):
    return (NBA_AB if lg == 'nba' else NFL_AB).get(fix_ab(lg, ab))


def team_by_name(lg, name):
    """'Indiana Pacers' / 'Pacers' / 'IND' -> 'Pacers'."""
    T = NBA_AB if lg == 'nba' else NFL_AB
    if not name:
        return None
    if fix_ab(lg, name) in T:
        return T[fix_ab(lg, name)]
    n = norm(name)
    for v in T.values():
        if n == norm(v) or n.endswith(' ' + norm(v)):
            return v
    return None


# ---------- Supabase (service role: full access; every app document is scoped to Kev's user id) ----------
class DB:
    def __init__(self):
        self.url = env('SUPABASE_URL').rstrip('/')
        key = env('SUPABASE_SERVICE_KEY')
        self.h = {'apikey': key, 'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
        self.uid = os.environ.get('BETDESK_USER_ID') or self._find_uid()

    def _find_uid(self):
        # Kev's app login: the user matching APP_EMAIL, or the only user in the project.
        r = requests.get(f'{self.url}/auth/v1/admin/users', params={'per_page': 1000}, headers=self.h, timeout=30)
        r.raise_for_status()
        users = r.json().get('users', [])
        email = re.sub(r'[\s"\'<>]', '', os.environ.get('APP_EMAIL') or '').lower()
        hit = [u for u in users if (u.get('email') or '').lower() == email] if email else users
        if len(hit) != 1:
            raise SystemExit('Could not find your app login: run the "Set up Supabase" workflow first (and check the APP_EMAIL secret).')
        return hit[0]['id']

    def _req(self, method, path, **kw):
        for attempt in range(3):
            r = requests.request(method, f'{self.url}/rest/v1/{path}', headers={**self.h, **kw.pop('headers', {})}, timeout=60, **kw)
            if r.status_code < 500:
                break
            time.sleep(2 * (attempt + 1))
        if r.status_code >= 400:
            raise RuntimeError(f'Supabase {method} {path}: {r.status_code} {r.text[:300]}')
        return r.json() if r.text else None

    # documents: path like 'nbaOdds' or 'ledger/bets/abc'
    @staticmethod
    def split(path):
        i = path.rfind('/')
        return ('', path) if i < 0 else (path[:i], path[i + 1:])

    def get(self, path, default=None):
        c, i = self.split(path)
        rows = self._req('GET', 'docs', params={'uid': f'eq.{self.uid}', 'coll': f'eq.{c}', 'id': f'eq.{i}', 'select': 'data,version'})
        return rows[0]['data'] if rows else default

    def set(self, path, data):
        c, i = self.split(path)
        self._req('POST', 'docs', params={'on_conflict': 'uid,coll,id'}, headers={'Prefer': 'resolution=merge-duplicates'},
                  data=json.dumps({'uid': self.uid, 'coll': c, 'id': i, 'data': data}))

    def merge(self, path, patch):
        """Merge fields into a document; a value {'__delete__': True} removes that field."""
        c, i = self.split(path)
        return self._req('POST', 'rpc/doc_merge', data=json.dumps({'p_uid': self.uid, 'p_coll': c, 'p_id': i, 'p_patch': patch}))

    def list(self, coll):
        out, start = [], 0
        while True:
            rows = self._req('GET', 'docs', params={'uid': f'eq.{self.uid}', 'coll': f'eq.{coll}', 'select': 'id,data'},
                             headers={'Range': f'{start}-{start + 999}'})
            out += rows
            if len(rows) < 1000:
                return [dict(r['data'], _id=r['id']) for r in out]
            start += 1000

    # archive tables
    def upsert(self, table, rows, conflict):
        for k in range(0, len(rows), 500):
            self._req('POST', table, params={'on_conflict': conflict}, headers={'Prefer': 'resolution=merge-duplicates'}, data=json.dumps(rows[k:k + 500], default=str))

    def select(self, table, **params):
        out, start = [], 0
        while True:
            rows = self._req('GET', table, params=params, headers={'Range': f'{start}-{start + 999}'})
            out += rows
            if len(rows) < 1000:
                return out
            start += 1000

    def log_run(self, job, started, ok, summary):
        self._req('POST', 'job_runs', data=json.dumps({'job': job, 'started_at': iso(started), 'finished_at': iso(now_utc()), 'ok': ok, 'summary': summary[:2000]}))


def notify(title, text):
    """Phone alert through ntfy (free app). Set NTFY_TOPIC to a long random topic name; no topic = no alerts."""
    topic = os.environ.get('NTFY_TOPIC')
    if not topic:
        return
    try:
        requests.post(f'https://ntfy.sh/{topic}', data=text.encode(), headers={'Title': title}, timeout=20)
    except requests.RequestException:
        pass


# ---------- odds math ----------
def amer(x):
    try:
        return int(str(x).replace('+', ''))
    except (TypeError, ValueError):
        return None


def dec(o):
    o = amer(o) if not isinstance(o, (int, float)) else o
    if not o:
        return None
    return 1 + o / 100 if o > 0 else 1 + 100 / abs(o)
