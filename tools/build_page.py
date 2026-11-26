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

# --- book-only markets: NFL passing joins NBA points (Nov 16 2026); calibration is skipped for 'off' markets (see jobs/model/picks_build.py) ---
rep("nfl:{wRole:.5,wScript:1,wMatch:1,wModel:.3,wOpt:.5,under:0,off:[]}};", "nfl:{wRole:.5,wScript:1,wMatch:1,wModel:.3,wOpt:.5,under:0,off:['pass','rush','rec']}};")  # rush/rec book-only Nov 26 2026

# --- probability calibration (doc 'calibration', refit nightly by the settle job): same correction the hourly model uses ---
rep("function evaluateN(p, exp, W){\n  const pm=modelOver(p.market, exp, p.line); if(pm==null) return null;\n  const pb=(p.pinO&&p.pinU)?bookOver(p.pinO,p.pinU):bookOver(p.odds,p.uodds), pf=blendN(pm,pb,W,p.market);",
    "function bookCalP(pb, market, W){ if(!(W?.off||[]).includes(market)) return null; const c=S.cal?.markets?.[market]; if(!c||c.bb==null||pb==null) return null; const q=Math.min(.99,Math.max(.01,pb)), z=(c.ba??1)*Math.log(q/(1-q))+c.bb; return Math.min(.99,Math.max(.01,1/(1+Math.exp(-z)))); }\n"
    "function calP(p, market, W){ if((W?.off||[]).includes(market)) return p; const c=S.cal?.markets?.[market]; if(!c||c.a==null||p==null) return p; const q=Math.min(.99,Math.max(.01,p)), z=c.a*Math.log(q/(1-q))+c.b; return Math.min(.99,Math.max(.01,1/(1+Math.exp(-z)))); }\n"
    "function evaluateN(p, exp, W){\n  const pm=modelOver(p.market, exp, p.line); if(pm==null) return null;\n  const pb=(p.pinO&&p.pinU)?bookOver(p.pinO,p.pinU):bookOver(p.odds,p.uodds), pfRaw=blendN(pm,pb,W,p.market), pf=bookCalP(pb,p.market,W)??calP(pfRaw,p.market,W);")
rep("  return {pModel:pm, pBook:pb, pOver:pf, side, ev:Math.max(evO,evU), pWin:side==='over'?pf:1-pf};",
    "  return {pModel:pm, pBook:pb, pOver:pf, pOverRaw:pfRaw, side, ev:Math.max(evO,evU), pWin:side==='over'?pf:1-pf};")
rep("  db.doc(S.base+'/nbaInjuries').onSnapshot(",
    "  db.doc(S.base+'/calibration').onSnapshot(snap=>{ S.cal = snap.exists?snap.data():null; try{ renderNba(); renderSixty(); }catch(e){} }, ()=>{});\n  db.doc(S.base+'/nbaInjuries').onSnapshot(")

# --- model v7 (Nov 23 2026; mirrors jobs/model/picks_build.py): injury boosts halved + capped, a hot recent role isn't counted
#     (only a cooler one), and a per-market projection scale from the calibration doc ---
rep("// ---------- projection model v6 (mirrors picks_build.py, the best-bets script) ----------",
    "// ---------- projection model v7 (mirrors picks_build.py, the best-bets script) ----------\n"
    "// v7 (Nov 23 2026): injury boosts halved + capped, a hot recent role isn't counted (only a cooler one), per-market projection scale from calibration.")
rep("function calP(p, market, W){",
    "const scaleFor=mk=>{ const v=S.cal?.markets?.[mk]?.scale; return typeof v==='number'?clampN(v,.85,1.15):1; };\nfunction calP(p, market, W){")
rep("  const pm=modelOver(p.market, exp, p.line); if(pm==null) return null;",
    "  const pm=modelOver(p.market, exp==null?exp:exp*scaleFor(p.market), p.line); if(pm==null) return null;")
rep("""  if(Mout>0&&mref&&mref>=10){ mAdj*=1+.8*Mout/240; for(const k in lost) uAdj[k]=1+clampN(.35*lost[k]/Math.max(tm[k]||1,1),0,.15);
    why.push(`${outs.slice(0,3).join(', ')} out: +${(.8*Mout/240*100).toFixed(0)}% min, +${((uAdj.pts-1)*100).toFixed(0)}% usage`); }""",
    """  if(Mout>0&&mref&&mref>=10){ const mb=Math.min(.4*Mout/240,.08); mAdj*=1+mb; for(const k in lost) uAdj[k]=1+clampN(.175*lost[k]/Math.max(tm[k]||1,1),0,.08);  // v7: half the old boost, capped
    why.push(`${outs.slice(0,3).join(', ')} out: +${(mb*100).toFixed(0)}% min, +${((uAdj.pts-1)*100).toFixed(0)}% usage`); }""")
rep("""  if(oMout>0){ const g=clampN(.12*oMout/240,0,.04), oo=T[opp].off||{}; oAdj.pts=oAdj.ast=oAdj.fg3=1+g; oAdj.reb=1+clampN(.3*oLost.reb/Math.max(oo.reb||44,1),0,.08);""",
    """  if(oMout>0){ const g=clampN(.06*oMout/240,0,.02), oo=T[opp].off||{}; oAdj.pts=oAdj.ast=oAdj.fg3=1+g; oAdj.reb=1+clampN(.15*oLost.reb/Math.max(oo.reb||44,1),0,.04);""")
rep("""    parts.push({k, s, rs:smin?s/smin:null, rr:(rec&&r5!=null)?r5/rmin:null, ms:smin, mr:rec?rmin:null, mAdj,""",
    """    const rs=smin?s/smin:null; let rr=(rec&&r5!=null)?r5/rmin:null; if(rr!=null&&rs!=null) rr=Math.min(rr,rs);  // v7: a hot stretch doesn't raise the projection
    parts.push({k, s, rs, rr, ms:smin, mr:rec?Math.min(rmin,smin):null, mAdj,""")
rep("""if(tag) why.unshift(`${tag}: ${rmin.toFixed(0)} min last 5 vs ${smin.toFixed(0)}`); }""",
    """if(tag) why.unshift(`${tag}: ${rmin.toFixed(0)} min last 5 vs ${smin.toFixed(0)}`+(tag==='role up'?' (not counted)':'')); }""")
rep("""  if(r!=null){ if(r>s*1.15) why.push(`role up: ${r.toFixed(0)} last 3 vs ${s.toFixed(0)}`); else if(r<s*.85) why.push(`role down: ${r.toFixed(0)} last 3 vs ${s.toFixed(0)}`); }""",
    """  if(r!=null){ if(r>s*1.15) why.push(`role up: ${r.toFixed(0)} last 3 vs ${s.toFixed(0)} (not counted)`); else if(r<s*.85) why.push(`role down: ${r.toFixed(0)} last 3 vs ${s.toFixed(0)}`); r=Math.min(r,s); }  // v7: only a cooler role counts""")

rep("  return {lg:'nba', parts, why, opt:opt??null, optW:null};", "  return {lg:'nba', v:7, parts, why, opt:opt??null, optW:null};")
rep("  return {lg:'nfl', parts:[{k:p.market, s, r, sf, dF:D[key]/lg, a:1, b:.5}], why, opt:opt??null};",
    "  return {lg:'nfl', v:7, parts:[{k:p.market, s, r, sf, dF:D[key]/lg, a:1, b:.5}], why, opt:opt??null};")
rep("""    if(ctx.lg==='nba'){ if(c.rs!=null&&c.ms){ const rate=c.rr==null?c.rs:(1-W.wRole)*c.rs+W.wRole*c.rr; const M=(c.mr==null?c.ms:(1-W.wRole)*c.ms+W.wRole*c.mr)*c.mAdj; base=rate*Math.min(M,42)*c.uAdj; } else base=c.s*c.uAdj; base*=(c.oAdj??1)**(W.wOpp??1); }
    else base=c.r==null?c.s:(1-W.wRole)*c.s+W.wRole*c.r;""",
    """    if(ctx.lg==='nba'){ const [mAdj,uAdj,oAdj]=v7Boosts(ctx,c);
      if(c.rs!=null&&c.ms){ const rr=c.rr==null?null:Math.min(c.rr,c.rs), mr=c.mr==null?null:Math.min(c.mr,c.ms); const rate=rr==null?c.rs:(1-W.wRole)*c.rs+W.wRole*rr; const M=(mr==null?c.ms:(1-W.wRole)*c.ms+W.wRole*mr)*mAdj; base=rate*Math.min(M,42)*uAdj; } else base=c.s*uAdj; base*=oAdj**(W.wOpp??1); }
    else { const r=c.r==null?null:Math.min(c.r,c.s); base=r==null?c.s:(1-W.wRole)*c.s+W.wRole*r; }""")
rep("function projectN(ctx, W){",
    """// comps logged before v7 carry the old, doubled injury boosts: halve + cap them so the tuner judges old and new props alike (same as v7_boosts in picks_build.py)
function v7Boosts(ctx,c){ const m=c.mAdj, u=c.uAdj, o=c.oAdj??1; if((ctx.v||6)>=7) return [m,u,o];
  const h=/out: \\+(\\d+)% min/.exec((ctx.why||[]).join(' ')), inj=h?+h[1]/100:0; return [m/(1+inj)*(1+Math.min(inj/2,.08)), 1+Math.min((u-1)/2,.08), 1+(o-1)/2]; }
function projectN(ctx, W){""")

# --- men's college basketball tab (content drawn by shim.js from the cbbBoard doc and ledger/cbbgl) ---
rep('data-tab="cfb">CFB</button>', 'data-tab="cfb">CFB</button>\n      <button role="tab" id="t-cbb" aria-selected="false" data-tab="cbb">CBB</button>')
rep('<section class="tab" id="tab-score"', '<section class="tab" id="tab-cbb" hidden><div class="panel" id="cbbPanel"><h2>College hoops</h2><p class="small muted">Loading…</p></div></section>\n  <section class="tab" id="tab-score"')
rep("slip:'#tab-slip'}", "slip:'#tab-slip', cbb:'#tab-cbb'}")
rep("try{ if(t==='cfb'){ loadCfb(false); maybeGradeCfb(false); } }catch(e){}",
    "try{ if(t==='cfb'){ loadCfb(false); maybeGradeCfb(false); } }catch(e){}\n  try{ if(t==='cbb' && window.BD_renderCbb) BD_renderCbb(); }catch(e){}")

# --- Settle button: only grade stats the box score really has. 'pass TDs', 'rush attempts', 'receptions', 'longest rush', quarters/halves
#     used to match the yards markets and get graded on the wrong number; they now wait for the nightly settle job (jobs/legs.py) or Claude ---
rep("  const mk=(LEG_MKT.find(([re])=>re.test(m[4]))||[])[1]; if(!mk) return null;",
    "  const mk=(LEG_MKT.find(([re])=>re.test(m[4]))||[])[1]; if(!mk) return null;\n"
    "  if(mk!=='pra'&&/long|\\btds?\\b|touchdown|att|carr|rec\\w*ption|\\brecs\\b|catch|complet|intercept|\\bints?\\b|first|1st|quarter|\\bq[1-4]\\b|half|\\b[12]h\\b|\\+/i.test(m[4])) return null;")

# --- NBA totals paused (Nov 26 2026): the model's total calls went 91-114 (-15.9%) on 205 graded games while its spreads went 110-95.
#     Totals now follow the book's no-vig price (weight 0, so no leans and nothing reaches the 60%+ tracker); the record stays visible. ---
rep("  return {m, spread:cfbMarket('spread',g2,NGL_W), total:cfbMarket('total',g2,NGL_W)}; }",
    "  return {m, spread:cfbMarket('spread',g2,NGL_W), total:cfbMarket('total',g2,NGL_TOTAL_W)}; }")
rep("const NGL_W=.3, NGL_HCA=2.0, NGL_SD={s:14.3,t:18.8};", "const NGL_W=.3, NGL_TOTAL_W=0, NGL_HCA=2.0, NGL_SD={s:14.3,t:18.8};  // totals paused: book price only (91-114 through Nov 25)")
rep("  const msg=$('#nglMsg'); msg.hidden=!NGL.err; msg.textContent=NGL.err||'';",
    "  const msg=$('#nglMsg'); msg.hidden=false; msg.textContent=NGL.err||'Total calls are paused: the model went 91–114 on totals, so they now show the book\\'s own price and never count as a best bet. Spreads are unchanged.';")

open(out, 'w', encoding='utf-8').write(s)
print('built', out, len(s), 'bytes')
