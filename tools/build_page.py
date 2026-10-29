"""Build web/index.html (standalone Bet Desk) from the Claude artifact's HTML.

    python3 tools/build_page.py SOURCE.html web/index.html

Every patch below is an exact text swap; if one no longer matches (the Claude page changed), the script stops and names it,
so nothing is silently skipped.
"""
import re, sys

src, out = sys.argv[1], sys.argv[2]
s = open(src, encoding='utf-8').read()

def rep(old, new, count=1):
    global s
    n = s.count(old)
    if n != count:
        sys.exit(f'patch not applied ({n} matches, expected {count}): {old[:90]!r}')
    s = s.replace(old, new)

def rep_re(pattern, new, flags=re.S):
    global s
    s2, n = re.subn(pattern, lambda m: new, s, count=1, flags=flags)
    if n != 1:
        sys.exit(f'patch not applied (pattern): {pattern[:90]!r}')
    s = s2

# --- page shell: our scripts, iPad home-screen support, sign out ---
HEAD = ('<link rel="manifest" href="manifest.webmanifest"><meta name="apple-mobile-web-app-capable" content="yes">'
        '<meta name="apple-mobile-web-app-title" content="Bet Desk"><link rel="apple-touch-icon" href="icon-192.png">'
        '<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4/dist/umd/supabase.js"></script>'
        '<script src="config.js"></script><script src="shim.js"></script></head>')
if s.lstrip().lower().startswith('<!doctype'):
    rep('</head>', HEAD)
else:  # artifact source without the publish skeleton
    s = ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
         '<style>:root{box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}body{margin:0}</style>'
         + HEAD + '<body>\n' + s + '\n</body></html>')
rep('<div class="sync" id="sync">Connecting…</div>',
    '<div class="row" style="gap:8px"><div class="sync" id="sync">Connecting…</div><button type="button" class="ghost" style="min-height:30px;padding:3px 9px;font-size:12px" onclick="BD_signOut()">Sign out</button></div>')
rep("Saving isn't available in this view, so changes here last only until you close the page. Open it from your Claude app while signed in to keep your history.",
    "Couldn't reach your database, so changes here last only until you close the page. Check your connection and reload.")

# --- no Optimal: SportsGameOdds consensus lines everywhere ---
rep('async function loadOpt(lg, force){', 'async function loadOpt(lg, force){ return; // standalone: no Optimal connector')
rep('Model picks at 60%+ on points, rebounds, assists, 3-pointers, PRA, spreads and totals · regular lines · Hard Rock price.',
    'Model picks at 60%+ on points, rebounds, assists, 3-pointers, PRA, spreads and totals · regular lines · consensus price.')
rep("Model = this season's offensive and defensive ratings and pace for both teams, plus 2 points for home court, priced at Hard Rock's line.",
    "Model = this season's offensive and defensive ratings and pace for both teams, plus 2 points for home court, priced at the SportsGameOdds consensus line.")

# NBA spreads & totals: build the rows from the nbaOdds doc (SportsGameOdds) instead of an Optimal query, grade from ESPN finals
rep_re(r'async function loadNbaGl\(force\)\{.*?\n\}\n',
    """function nglFromOdds(){ const o=S.nbaOdds; if(!o){ NGL.rows=null; return; } const t=(l,a,b)=>l==null?'':[l,a??-110,b??-110].join(',');
  NGL.rows=(o.games||[]).map(g=>({a:g.away,h:g.home,s:g.start,st:'',sa:null,sh:null,r:[t(g.spread?.home,g.spread?.homeOdds,g.spread?.awayOdds),t(g.total?.line,g.total?.overOdds,g.total?.underOdds),(g.ml?.home&&g.ml?.away)?[0,g.ml.home,g.ml.away].join(','):'','','','','','','','',''].join('|')}));
  NGL.at=o.updated?Date.parse(o.updated):Date.now(); NGL.err=null; }
async function loadNbaGl(force){ nglFromOdds(); renderNbaGl(); nbaGlLockDue().catch(()=>{}); maybeGradeNbaGl(); }
""")
rep_re(r'async function maybeGradeNbaGl\(\)\{.*?\n\}\n',
    """async function maybeGradeNbaGl(){
  if(NGL.grading||!S.live) return; const due=(S.nbagl||[]).filter(d=>!d.graded&&d.gameStart&&Date.parse(d.gameStart)<Date.now()-3*36e5); if(!due.length) return;
  const mcp=await getMcp(); if(!mcp) return; NGL.grading=true;
  try{ const days=[...new Set(due.map(d=>etDate(new Date(d.gameStart))))];
    for(const day of days){ const gr=await mcp.callTool(SM,'get_games',{league:'nba',start_date:day}); const games=gr?.payload?.games||[];
      for(const d of due.filter(x=>etDate(new Date(x.gameStart))===day)){ const g=games.find(g=>nick('nba',g.away?.name)===d.away&&nick('nba',g.home?.name)===d.home); if(!g||sbState(g)!=='final') continue;
        const sa=+g.away.score, sh=+g.home.score; if(!isFinite(sa)||!isFinite(sh)||sa+sh===0) continue;
        const res={}; for(const [k,c] of Object.entries(d.calls||{})) res[k]=cfbRes(k,c,sa,sh);
        const {id,...body}=d; await S.db.doc(S.base+'/ledger').collection('nbagl').doc(id).set({...body, score:{away:sa,home:sh}, res, graded:true, gradedAt:new Date().toISOString()}); } } }
  catch(e){} finally{ NGL.grading=false; }
}
""")
rep("function renderNbaGl(){\n  const tb=$('#nglTable'); if(!tb) return;", "function renderNbaGl(){\n  const tb=$('#nglTable'); if(!tb) return; nglFromOdds();")
rep("`Hard Rock (FL) + Pinnacle · ${new Date(NGL.at).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})}`",
    "`SportsGameOdds consensus · ${new Date(NGL.at).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})}`")

# College football: lines only (SportsGameOdds, doc "cfbOdds"); no projections, so no model record
rep_re(r'<p class="small muted">Game lines only: spread, total and moneyline, at Hard Rock \(FL\)\..*?</p>',
    '<p class="small muted">Game lines only: spread, total and moneyline (SportsGameOdds consensus, refreshed Friday and Saturday mornings). '
    'The standalone app has no college projections, so the chances shown are the book\'s own no-vig chances, not a model.</p>')
rep('<div class="panel" id="cfbRecPanel">', '<div class="panel" id="cfbRecPanel" hidden>')
rep_re(r'async function loadCfb\(force\)\{.*?\n\}\n',
    """async function loadCfb(force){ const o=S.cfbOdds; const t=(l,a,b)=>l==null?'':[l,a??-110,b??-110].join(',');
  if(!o){ CFB.rows=null; CFB.err='College football lines load with the Friday and Saturday morning refresh.'; renderCfb(); return; }
  CFB.rows=(o.games||[]).map(g=>({a:g.away,h:g.home,s:g.start,st:'',sa:null,sh:null,r:[t(g.spread?.home,g.spread?.homeOdds,g.spread?.awayOdds),t(g.total?.line,g.total?.overOdds,g.total?.underOdds),(g.ml?.home&&g.ml?.away)?[0,g.ml.home,g.ml.away].join(','):'','','','','','','','',''].join('|')}));
  CFB.at=o.updated?Date.parse(o.updated):Date.now(); CFB.err=null; renderCfb();
}
""")
rep('async function cfbLockDue(){', 'async function cfbLockDue(){ return; // standalone: no CFB model')
rep('async function maybeGradeCfb(force){', 'async function maybeGradeCfb(force){ return 0; // standalone: no CFB model')
rep("`Hard Rock (FL) + Pinnacle lines, Optimal projections · ${new Date(CFB.at).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})}`",
    "`SportsGameOdds consensus · ${new Date(CFB.at).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})}`")
rep("  db.doc(S.base+'/nbaProps').onSnapshot(",
    "  db.doc(S.base+'/cfbOdds').onSnapshot(snap=>{ S.cfbOdds = snap.exists?snap.data():null; if(!$('#tab-cfb').hidden) loadCfb(true); }, ()=>{});\n  db.doc(S.base+'/nbaProps').onSnapshot(")

# --- tasks & refresh: GitHub Actions jobs instead of Claude scheduled tasks ---
rep("  ['trig_01KdG1jg21aP26nrPg4d5TmG','NBA stats, schedule, averages and lines'],\n  ['trig_01Q7EphCkmUPaWWoqYhDu3MP','NFL defense, schedule, averages and lines'],\n  ['trig_01UoCMDsd5DPrEz6RXErpL3F','NBA and NFL injuries']];",
    "  ['nba_daily','NBA stats, schedule, averages and lines'],\n  ['nfl_weekly','NFL defense, schedule, averages and lines'],\n  ['injuries','NBA and NFL injuries']];")
rep("el('td',{class:'small num',text:t.next_run_at?new Date(t.next_run_at).toLocaleString([], {weekday:'short',hour:'numeric',minute:'2-digit'}):'—'})",
    "el('td',{class:'small num',text:t.next_run_at?new Date(t.next_run_at).toLocaleString([], {weekday:'short',hour:'numeric',minute:'2-digit'}):(t.schedule||'—')})")

# --- game logs come from this season's archive only ---
rep("Green = went over the line, red = under. Includes last season, so early games may be with a different team.",
    "Green = went over the line, red = under. Only games from this season.")

open(out, 'w', encoding='utf-8').write(s)
print('built', out, len(s), 'bytes')
