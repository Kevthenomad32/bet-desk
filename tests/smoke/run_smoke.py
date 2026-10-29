"""Browser smoke test: the standalone page boots on the shim, reads seeded data, renders each tab, and writes through the db shim.

    python3 tests/smoke/run_smoke.py BACKUP.json
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WEB = os.path.join(ROOT, 'web')
backup = json.load(open(sys.argv[1]))
ESPN_SB = {'events': [{'id': '401', 'date': '2026-10-29T23:30:00Z', 'season': {'year': 2027, 'type': 2},
                       'status': {'type': {'state': 'in', 'name': 'STATUS_IN_PROGRESS', 'shortDetail': 'Q3 5:12'}},
                       'competitions': [{'broadcasts': [{'names': ['ESPN']}], 'competitors': [
                           {'homeAway': 'home', 'score': '77', 'team': {'abbreviation': 'GS', 'shortDisplayName': 'Warriors'}, 'records': [{'summary': '3-2'}]},
                           {'homeAway': 'away', 'score': '80', 'team': {'abbreviation': 'NY', 'shortDisplayName': 'Knicks'}, 'records': [{'summary': '4-1'}]}]}]}]}
ESPN_SUM = {'header': {'competitions': [{'status': {'type': {'state': 'in'}}, 'competitors': [
                {'homeAway': 'home', 'score': '77', 'team': {'abbreviation': 'GS'}, 'linescores': [{'displayValue': '25'}, {'displayValue': '30'}, {'displayValue': '22'}]},
                {'homeAway': 'away', 'score': '80', 'team': {'abbreviation': 'NY'}, 'linescores': [{'displayValue': '28'}, {'displayValue': '27'}, {'displayValue': '25'}]}]}]},
            'boxscore': {'players': [
                {'team': {'abbreviation': 'GS'}, 'statistics': [{'keys': ['minutes', 'points', 'threePointFieldGoalsMade-threePointFieldGoalsAttempted', 'rebounds', 'assists'],
                                                                'athletes': [{'athlete': {'id': '1', 'displayName': 'Stephen Curry'}, 'starter': True, 'stats': ['30', '25', '5-11', '4', '6']},
                                                                             {'athlete': {'id': '2', 'displayName': 'Bench Guy'}, 'didNotPlay': True, 'stats': []}]}]},
                {'team': {'abbreviation': 'NY'}, 'statistics': [{'keys': ['minutes', 'points', 'threePointFieldGoalsMade-threePointFieldGoalsAttempted', 'rebounds', 'assists'],
                                                                'athletes': [{'athlete': {'id': '3', 'displayName': 'Jalen Brunson'}, 'starter': True, 'stats': ['31', '28', '3-7', '3', '7']}]}]}]}}
PG = [{'league': 'nba', 'game_id': f'g{i}', 'player_id': '99', 'name': 'Darius Garland', 'name_norm': 'darius garland', 'team': 'Cavaliers', 'team_ab': 'CLE',
       'opp': 'Wizards', 'opp_ab': 'WAS', 'date': f'2026-10-2{i}', 'season': 2027, 'starter': True, 'dnp': False, 'stats': {'pts': 20 + i, 'reb': 3, 'ast': 7, 'fg3': 2, 'min': 33}}
      for i in range(1, 6)]

errors, logs = [], []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width': 1200, 'height': 900})
    pg.on('pageerror', lambda e: errors.append(str(e)))
    pg.on('console', lambda m: logs.append(f'{m.type}: {m.text}') if m.type in ('error', 'warning') else None)

    asked = []

    def route(r):
        u = r.request.url
        if 'supabase-js' in u:
            seed = 'window.__SEED=' + json.dumps(backup['docs']) + ';window.__PLAYER_GAMES=' + json.dumps(PG) + ';\n'
            return r.fulfill(body=seed + open(os.path.join(ROOT, 'tests/smoke/mock_supabase.js')).read(), content_type='application/javascript')
        if u.endswith('/config.js'):
            return r.fulfill(body="window.BD_CONFIG={supabaseUrl:'https://test.supabase.co',supabaseAnonKey:'anon'};", content_type='application/javascript')
        if '/functions/v1/statmuse' in u:
            asked.append(json.loads(r.request.post_data))
            return r.fulfill(body=json.dumps({'type': 'stats', 'answer': 'Jalen Brunson averaged 27.2 points in his last 5 games.',
                'table': {'columns': ['player', 'PointsPerGame'], 'rows': [['Jalen Brunson', '27.2']]}, 'quota': {'limit': '10000', 'remaining': '9876'}}),
                content_type='application/json')
        if '/functions/v1/espn' in u:
            return r.fulfill(body=json.dumps(ESPN_SUM if 'summary' in u else ESPN_SB), content_type='application/json')
        if u.startswith('http://app.test/'):
            path = os.path.join(WEB, u.split('http://app.test/')[1].split('?')[0] or 'index.html')
            if os.path.exists(path):
                return r.fulfill(path=path)
            return r.fulfill(status=404, body='')
        if 'fonts.g' in u:
            return r.fulfill(body='', content_type='text/css')
        return r.fulfill(status=404, body='')
    pg.route('**/*', route)
    pg.goto('http://app.test/index.html')
    pg.wait_for_timeout(2500)
    res = {}
    res['sync'] = pg.inner_text('#sync')
    res['offline_hidden'] = pg.eval_on_selector('#offline', 'e=>e.hidden')
    res['tiles'] = pg.inner_text('#tiles').replace('\n', ' | ')[:300]
    res['bets_listed'] = pg.eval_on_selector_all('#betList .bet', 'els=>els.length')
    # write path: change the unit setting
    pg.fill('#st-unit', '50'); pg.click('#st-save'); pg.wait_for_timeout(500)
    res['wrote_settings'] = pg.evaluate("window.__WRITES.some(w=>w.id==='settings'&&w.data.unit===50)")
    pg.click('#t-nba'); pg.wait_for_timeout(1500)
    res['sixty_tiles'] = pg.inner_text('#sixtyTiles').replace('\n', ' | ')[:200]
    res['ngl_src'] = pg.inner_text('#nglSrc')
    pg.click('#t-nfl'); pg.wait_for_timeout(800)
    res['nfl_title'] = pg.inner_text('#slateTitle')
    res['soft_spots'] = pg.eval_on_selector_all('#ssDef .ss-g', 'els=>els.length')
    pg.click('#t-score'); pg.wait_for_timeout(1500)
    res['scores'] = pg.inner_text('#sbGames').replace('\n', ' | ')[:200]
    pg.click('#sbGames .sb'); pg.wait_for_timeout(1200)
    res['box'] = pg.inner_text('#sbGames .box').replace('\n', ' | ')[:200] if pg.query_selector('#sbGames .box') else None
    pg.click('#t-cfb'); pg.wait_for_timeout(800)
    res['cfb_msg'] = pg.inner_text('#cfbMsg')
    pg.click('#t-tracker'); pg.wait_for_timeout(500)
    pg.click('#t-nba'); pg.wait_for_timeout(300)
    pg.fill('#tab-nba .bd-ask-in', 'Brunson points last 5 games'); pg.click('#tab-nba .bd-ask button'); pg.wait_for_timeout(800)
    res['ask'] = pg.inner_text('#tab-nba .bd-ask').replace('\n', ' | ')[:220]
    res['ask_sent'] = asked
    if os.environ.get('ASKSHOT'): pg.locator('#tab-nba .bd-ask').screenshot(path=os.environ['ASKSHOT'])
    res['ask_nfl_panel'] = pg.evaluate("document.querySelector('#tab-nfl .bd-ask .bd-ask-lg').value")
    pg.click('#t-tracker'); pg.wait_for_timeout(300)
    res['export_button'] = pg.evaluate("!!document.getElementById('bdExport') && document.getElementById('bdExport').nextElementSibling.id==='refreshAll'")
    pg.screenshot(path=os.environ.get('SHOT', '/tmp/smoke.png'), full_page=False)
    b.close()
for k, v in res.items():
    print(f'{k}: {v}')
print('page errors:', errors or 'none')
print('console errors:', [l for l in logs if 'Failed to load resource' not in l] or 'none')
sys.exit(1 if errors else 0)
