// Kev's Bet Desk, standalone: stands in for the Claude artifact runtime (window.claude.use) so the Bet Desk page runs unchanged.
//   db        -> Supabase table "docs" (live updates over Supabase Realtime)
//   user      -> Supabase Auth (email + password)
//   mcp       -> "Stat Muse" tools answered from ESPN (through the espn edge function) and the games archive;
//                "Claude Code Remote" task status / refresh from the job_runs table and the jobs edge function
//   sample    -> Claude via the claude edge function (your Anthropic API key): slip reading, parlay check
//   downloads -> a normal browser download
(() => {
'use strict';
const C = window.BD_CONFIG || {};
if (!C.supabaseUrl || !C.supabaseAnonKey || !window.supabase) { console.error('Bet Desk: missing config.js or supabase-js'); return; }
const sb = window.supabase.createClient(C.supabaseUrl, C.supabaseAnonKey, { auth: { persistSession: true, autoRefreshToken: true } });
window.BD_SB = sb;
const err = (code, msg) => Object.assign(new Error(msg || code), { code });
const FN = name => `${C.supabaseUrl}/functions/v1/${name}`;

// ---------- sign-in ----------
let sessionP = null;
function getSession() {
  if (sessionP) return sessionP;
  sessionP = (async () => { const { data } = await sb.auth.getSession(); return data.session || await showLogin(); })();
  return sessionP;
}
async function token() { const { data } = await sb.auth.getSession(); return data.session?.access_token || (await getSession()).access_token; }
function showLogin() {
  return new Promise(resolve => {
    const wrap = document.createElement('div');
    wrap.style.cssText = 'position:fixed;inset:0;z-index:100;display:grid;place-items:center;background:var(--bg,#EEF1F5);padding:16px';
    wrap.innerHTML = `<form style="width:min(380px,100%);background:var(--surface,#fff);border:1px solid var(--line,#CDD5E0);border-radius:10px;padding:20px;display:flex;flex-direction:column;gap:12px">
      <div style="font:800 26px var(--display,sans-serif);text-transform:uppercase">Kev's <span style="color:var(--accent,#1F4FE0)">Bet</span> Desk</div>
      <label class="f">Email<input id="bd-email" type="email" autocomplete="username" required></label>
      <label class="f">Password<input id="bd-pass" type="password" autocomplete="current-password" required></label>
      <button class="primary" type="submit" id="bd-login">Sign in</button>
      <p class="small muted" id="bd-login-msg" style="margin:0"></p></form>`;
    document.body.append(wrap);
    wrap.querySelector('form').addEventListener('submit', async e => {
      e.preventDefault(); const msg = wrap.querySelector('#bd-login-msg'); msg.textContent = 'Signing in…';
      const { data, error } = await sb.auth.signInWithPassword({ email: wrap.querySelector('#bd-email').value.trim(), password: wrap.querySelector('#bd-pass').value });
      if (error) { msg.textContent = error.message || 'Could not sign in.'; return; }
      wrap.remove(); resolve(data.session);
    });
  });
}
window.BD_signOut = async () => { await sb.auth.signOut(); location.reload(); };

// ---------- "Download my data": every document as one file (same format the import page reads) ----------
window.BD_exportData = async () => {
  const session = await getSession(); const me = session.user.id; const docs = []; const page = 500;
  for (let from = 0; ; from += page) {
    const { data, error } = await sb.from('docs').select('coll,id,data').eq('uid', me).order('coll').order('id').range(from, from + page - 1);
    if (error) throw error;
    docs.push(...data);
    if (data.length < page) break;
  }
  const day = new Date().toLocaleDateString('en-CA', { timeZone: 'America/New_York' });
  const blob = new Blob([JSON.stringify({ exportedAt: new Date().toISOString(), source: 'Bet Desk standalone', docs })], { type: 'application/json' });
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: `bet-desk-data-${day}.json` });
  document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 60000);
  return docs.length;
};
function addExportButton() {
  const near = document.getElementById('refreshAll');
  if (!near || document.getElementById('bdExport')) return;
  const b = Object.assign(document.createElement('button'), { type: 'button', id: 'bdExport', className: 'ghost small', textContent: 'Download my data',
    title: 'Saves your bets, picks, graded model props and settings as one file (also a backup you can re-import)' });
  near.before(b);
  b.addEventListener('click', async () => {
    const t = b.textContent; b.disabled = true; b.textContent = 'Preparing…';
    try { const n = await window.BD_exportData(); b.textContent = `Saved ${n} items`; }
    catch (e) { b.textContent = 'Download failed'; console.error(e); }
    setTimeout(() => { b.textContent = t; b.disabled = false; }, 4000);
  });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', addExportButton); else addExportButton();

const esc = v => String(v ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
// ---------- 60%+ trackers for NFL (props), CFB and CBB (spreads + totals): every model pick at 60%+, graded as 1u straight at the locked price ----------
const SX = {
  nfl: { tab: 'tab-nfl', title: 'NFL 60%+ Tracker', coll: 'ledger/shadow', league: 'NFL', mkts: { pass: 'Pass yds', rush: 'Rush yds', rec: 'Rec yds' }, by: 'Week',
         about: 'Every player prop the model priced before kickoff (passing, rushing, receiving yards) where it rates one side 60%+. Graded as a 1-unit straight bet at the book price.' },
  cfb: { tab: 'tab-cfb', title: 'CFB 60%+ Tracker', coll: 'ledger/cfb', mkts: { spread: 'Spread', total: 'Total' }, by: 'Saturday',
         about: 'Every locked college football spread and total where the model rates its side 60%+ (Hard Rock price, nudged from Pinnacle). Moneylines are left out: any big favorite clears 60%. Game lines are sharp, so 60%+ calls are rare: use 55%+ or 53%+ to see more.' },
  cbb: { tab: 'tab-cbb', title: 'CBB 60%+ Tracker', coll: 'ledger/cbbgl', mkts: { spread: 'Spread', total: 'Total' }, by: 'Night',
         about: 'Every locked college basketball spread and total where the model rates its side 60%+. Game lines are sharp and the model stays close to Pinnacle\'s fair price, so 60%+ calls are rare: use 55%+ or 53%+ to see more.' } };
const SX_BARS = [0.6, 0.55, 0.53];
const sxBar = lg => { try { const v = parseFloat(localStorage.getItem('bd-sxbar-' + lg)); return SX_BARS.includes(v) ? v : 0.6; } catch (_) { return 0.6; } };
document.addEventListener('click', e => { const b = e.target.closest && e.target.closest('.bd-sx-bar button'); if (!b) return; const lg = Object.keys(SX).find(k => b.closest('#' + SX[k].tab));
  try { localStorage.setItem('bd-sxbar-' + lg, b.dataset.b); } catch (_) {} window.BD_renderSixty(lg); });
const sxNum = v => { const n = parseInt(v, 10); return isFinite(n) ? n : null; };
const sxAm = n => n > 0 ? '+' + n : String(n);
const sxPct = v => Math.round(v * 1000) / 10 + '%';
const sxLine = v => (v > 0 ? '+' : '') + v;
function sxRows(lg, docs, bar = 0.6) {
  const out = [];
  if (lg === 'nfl') {
    for (const d of docs) for (const r of d.rows || []) {
      if (!SX.nfl.mkts[r.market] || typeof r.pModel !== 'number') continue;
      const conf = Math.max(r.pModel, 1 - r.pModel); if (conf < bar) continue;
      const over = r.pModel >= 0.5, odds = sxNum(over ? r.odds : r.uodds);
      const res = r.result == null ? null : r.result === r.line ? 'push' : (over === (r.result > r.line)) ? 'hit' : 'miss';
      out.push({ date: d.date, game: d.game, start: d.gameStart, label: `${r.player} ${over ? 'o' : 'u'}${r.line} ${SX.nfl.mkts[r.market]}`, market: r.market,
                 odds: odds ?? -115, priced: odds != null, conf, res, shown: r.result });
    }
    return out;
  }
  for (const d of docs) for (const k of ['spread', 'total']) {
    const c = d.calls?.[k]; if (!c || !(c.pWin >= bar) || c.far) continue;  // 'far' = model miles off the market: not a real call
    const label = k === 'spread' ? (c.side === 'home' ? `${d.home} ${sxLine(c.line)}` : `${d.away} ${sxLine(-c.line)}`) : `${c.side === 'over' ? 'Over' : 'Under'} ${c.line}`;
    let res = lg === 'cfb' ? (d.res?.[k] || null) : ({ won: 'hit', lost: 'miss', push: 'push' }[c.result] || null);
    const sc = d.final || d.score;
    out.push({ date: d.date, game: d.game || `${d.away} @ ${d.home}`, start: d.gameStart || d.start, label, market: k, odds: sxNum(c.price) ?? -110, priced: true,
               conf: c.pWin, res, shown: sc ? `${sc.away}–${sc.home}` : '' });
  }
  return out;
}
function sxAgg(rows) { let w = 0, l = 0, p = 0, u = 0; for (const r of rows) { if (r.res === 'push') { p++; continue; } if (r.res === 'hit') { w++; u += r.odds > 0 ? r.odds / 100 : 100 / -r.odds; } else if (r.res === 'miss') { l++; u -= 1; } } return { w, l, p, u, n: w + l }; }
const sxWeek = d => { const t = new Date(d + 'T12:00'); const k = new Date(t); k.setDate(t.getDate() - ((t.getDay() + 5) % 7)); return k.toISOString().slice(0, 10); };  // NFL week starts Tuesday
async function renderSixtyFor(lg) {
  const cfg = SX[lg], tab = document.getElementById(cfg.tab); if (!tab) return;
  let box = tab.querySelector('.bd-sixty');
  if (!box) { box = document.createElement('div'); box.className = 'panel bd-sixty'; const first = tab.querySelector('.bd-value') || tab.querySelector('.panel'); first ? first.after(box) : tab.prepend(box); box.innerHTML = `<h2>${cfg.title}</h2><p class="small muted">Loading…</p>`; }
  const me = (await getSession()).user.id;
  let q = sb.from('docs').select('id,data').eq('uid', me).eq('coll', cfg.coll); if (cfg.league) q = q.eq('data->>league', cfg.league);
  const [{ data: led }, { data: st }] = await Promise.all([q, sb.from('docs').select('data').eq('uid', me).eq('coll', '').eq('id', 'settings').maybeSingle()]);
  const unit = Number(st?.data?.unit) || 50;
  const bar = sxBar(lg); const all = sxRows(lg, (led || []).map(r => r.data), bar), done = all.filter(r => r.res != null), a = sxAgg(done);
  const avg = done.length ? Math.round(done.reduce((s, r) => s + r.odds, 0) / done.length) : -112;
  const be = avg < 0 ? -avg / (-avg + 100) : 100 / (avg + 100), rate = a.n ? a.w / a.n : 0;
  const v = a.n < 100 ? ['Too early', 'warnc', `${a.n} of ~100 graded picks needed`] : (rate > be + 0.02 && a.u > 0) ? ['Worth betting', 'pos', 'Beating break-even by 2%+'] : rate > be ? ['Borderline', 'warnc', 'Ahead, but barely'] : ['Not yet', 'neg', 'Below break-even'];
  const tile = (k, val, cls, sub) => `<div class="tile"><span class="k">${k}</span><span class="v num ${cls}">${val}</span><span class="s">${sub}</span></div>`;
  const u = x => `${x >= 0 ? '+' : '−'}${Math.abs(x).toFixed(2)}u`;
  const row = (label, g) => { const x = sxAgg(g); return `<tr><td>${esc(label)}</td><td class="r num">${x.w}-${x.l}</td><td class="r num">${x.n ? sxPct(x.w / x.n) : '—'}</td><td class="r num ${x.u >= 0 ? 'pos' : 'neg'}">${u(x.u)}</td></tr>`; };
  const head = t => `<tr><th>${t}</th><th class="r">W-L</th><th class="r">Hit</th><th class="r">Units</th></tr>`;
  const key = lg === 'nfl' ? r => sxWeek(r.date) : r => r.date;
  const groups = [...new Set(done.map(key))].sort().reverse();
  const gname = k => (lg === 'nfl' ? 'Week of ' : '') + new Date(k + 'T12:00').toLocaleDateString('en-US', { weekday: lg === 'nfl' ? undefined : 'short', month: 'short', day: 'numeric' });
  const now = Date.now(), open = all.filter(r => r.res == null && (!r.start || Date.parse(r.start) > now - 4 * 36e5)).sort((x, y) => y.conf - x.conf);
  const when = r => r.start ? new Date(r.start).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }) : '';
  const pctT = Math.round(bar * 1000) / 10 + '%+';
  box.innerHTML = `<div class="row spread"><h2>${cfg.title.replace('60%+', pctT)}</h2><div class="filters bd-sx-bar">${SX_BARS.map(b => `<button type="button" data-b="${b}" aria-pressed="${b === bar}">${Math.round(b * 1000) / 10}%+</button>`).join('')}</div></div>
    <p class="small muted">${cfg.about.replace('60%+', pctT)} You need about ${sxPct(be)} to break even at ${sxAm(avg)}.</p>
    <div class="tiles">${tile('Record', `${a.w}-${a.l}`, '', a.n ? `${sxPct(rate)} · break-even ${sxPct(be)}` : 'No graded picks yet')}${tile('Units', u(a.u), a.u >= 0 ? 'pos' : 'neg', `${a.u >= 0 ? '+' : '−'}$${Math.abs(a.u * unit).toFixed(0)} at $${unit} a unit`)}${tile('Sample', a.n, '', `${groups.length} ${cfg.by.toLowerCase()}${groups.length === 1 ? '' : 's'}${a.p ? ` · ${a.p} push` : ''}`)}${tile('Verdict', v[0], v[1], v[2])}</div>
    <div class="two" style="margin-top:14px">
      <div><h3 style="margin-bottom:8px">By market</h3><div class="tablewrap"><table>${head('Market')}${Object.entries(cfg.mkts).map(([k, n]) => row(n, done.filter(r => r.market === k))).join('')}</table></div></div>
      <div><h3 style="margin-bottom:8px">By ${cfg.by.toLowerCase()}</h3><div class="tablewrap"><table>${head(cfg.by)}${groups.map(g => row(gname(g), done.filter(r => key(r) === g))).join('') || '<tr><td colspan="4" class="muted">Nothing graded yet</td></tr>'}</table></div></div>
    </div>
    <h3 style="margin:14px 0 8px">Open qualifying picks</h3>
    <div class="tablewrap"><table><tr><th>Pick</th><th>Game</th><th class="r">Price</th><th class="r">Model</th></tr>${open.map(r => `<tr><td><b>${esc(r.label)}</b></td><td class="small muted">${esc(r.game)} · ${when(r)}${r.start && Date.parse(r.start) < now ? ' · started' : ''}</td><td class="r num">${r.priced ? sxAm(r.odds) : '—'}</td><td class="r num">${sxPct(r.conf)}</td></tr>`).join('') || '<tr><td colspan="4" class="muted">No qualifying picks right now. They show up once the model prices each game before it starts.</td></tr>'}</table></div>
    <p class="small muted">Check the line on Hard Rock before betting: skip a pick if the line has moved a point or more against it${lg === 'nfl' ? ', or if the player is on the injury report' : ''}.</p>
    <details><summary class="small">Every graded pick (${done.length})</summary><div class="tablewrap" style="margin-top:8px"><table><tr><th>Date</th><th>Pick</th><th class="r">Result</th><th class="r">Price</th></tr>${
      done.sort((x, y) => String(y.date).localeCompare(String(x.date)) || y.conf - x.conf).map(r => `<tr><td class="small muted">${String(r.date).slice(5)}</td><td>${esc(r.label)} <span class="small muted">${sxPct(r.conf)}</span></td><td class="r num ${r.res === 'push' ? '' : r.res === 'hit' ? 'pos' : 'neg'}">${esc(r.shown ?? '')} ${r.res === 'push' ? 'P' : r.res === 'hit' ? '✓' : '✗'}</td><td class="r num">${sxAm(r.odds)}</td></tr>`).join('')}</table></div></details>`;
}
// ---------- Value finder: Hard Rock prices that beat Pinnacle's no-vig fair price (the 'value' doc, rebuilt every hour) ----------
const VAL_LG = { nfl: 'tab-nfl', nba: 'tab-nba', cbb: 'tab-cbb' };
async function renderValueFor(lg) {
  const tab = document.getElementById(VAL_LG[lg]); if (!tab) return;
  let box = tab.querySelector('.bd-value');
  if (!box) { box = document.createElement('div'); box.className = 'panel bd-value'; const first = tab.querySelector('.panel'); first ? first.after(box) : tab.prepend(box); box.innerHTML = '<h2>Value finder</h2><p class="small muted">Loading…</p>'; }
  const me = (await getSession()).user.id;
  const { data } = await sb.from('docs').select('data').eq('uid', me).eq('coll', '').eq('id', 'value').maybeSingle();
  const V = data?.data, now = Date.now();
  const rows = (V?.rows || []).filter(r => r.lg === lg && Date.parse(r.start) > now);
  let kind = 'all'; try { kind = localStorage.getItem('bd-valkind-' + lg) || 'all'; } catch (_) {}
  const kinds = lg === 'cbb' ? [] : [['all', 'All'], ['prop', 'Props'], ['game', 'Game lines']];
  const shown = rows.filter(r => kind === 'all' || (kind === 'prop' ? r.kind === 'prop' : r.kind !== 'prop'));
  const when = iso => new Date(iso).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' });
  box.innerHTML = `<div class="row spread"><h2>Value finder</h2>${kinds.length ? `<div class="filters bd-val-kind">${kinds.map(([k, n]) => `<button type="button" data-k="${k}" aria-pressed="${k === kind}">${n}</button>`).join('')}</div>` : ''}</div>
    <p class="small muted">Hard Rock prices that are better than Pinnacle's fair price at the same line (Pinnacle is the sharpest book; "fair" is its price with the house cut taken out). This doesn't use the model at all, and it's the most reliable edge there is. Edges move fast: check the price on Hard Rock first. ${V ? 'Updated ' + new Date(V.updated).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) + '.' : ''}</p>
    <div class="tablewrap"><table><tr><th>Bet</th><th>Game</th><th class="r">Hard Rock</th><th class="r">Fair</th><th class="r">Edge</th></tr>${
      shown.slice(0, 30).map(r => `<tr><td><b>${esc(r.label)}</b>${r.avg != null ? ` <span class="small muted">avg ${esc(r.avg)}</span>` : ''}</td><td class="small muted">${esc(r.game)} · ${when(r.start)}</td><td class="r num">${esc(r.price)}</td><td class="r num">${r.fair > 0 ? '+' : ''}${r.fair}</td><td class="r num pos">+${(r.ev * 100).toFixed(1)}%</td></tr>`).join('')
      || `<tr><td colspan="5" class="muted">${V ? 'No Hard Rock prices beat Pinnacle right now. Check back closer to game time.' : 'The value list fills in at the next hourly update.'}</td></tr>`}</table></div>
    ${shown.length > 30 ? `<p class="small muted">Showing the best 30 of ${shown.length}.</p>` : ''}`;
}
window.BD_renderValue = lg => renderValueFor(lg).catch(e => console.error('value finder', lg, e));
document.addEventListener('click', e => { const b = e.target.closest && e.target.closest('.bd-val-kind button'); if (!b) return; const lg = Object.keys(VAL_LG).find(k => b.closest('#' + VAL_LG[k]));
  try { localStorage.setItem('bd-valkind-' + lg, b.dataset.k); } catch (_) {} window.BD_renderValue(lg); });
window.BD_renderSixty = lg => renderSixtyFor(lg).catch(e => console.error('60% tracker', lg, e));
function sixtyForVisible() { for (const lg of Object.keys(SX)) { const t = document.getElementById(SX[lg].tab); if (t && !t.hidden) { if (VAL_LG[lg]) window.BD_renderValue(lg); window.BD_renderSixty(lg); } } }
function valueForVisibleNba() { const t = document.getElementById('tab-nba'); if (t && !t.hidden) window.BD_renderValue('nba'); }
document.addEventListener('click', e => { if (e.target.closest && e.target.closest('nav.tabs button')) setTimeout(valueForVisibleNba, 50); });
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(valueForVisibleNba, 500)); else setTimeout(valueForVisibleNba, 500);
document.addEventListener('click', e => { if (e.target.closest && e.target.closest('nav.tabs button')) setTimeout(sixtyForVisible, 50); });
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(sixtyForVisible, 500)); else setTimeout(sixtyForVisible, 500);
// ---------- "Ask Claude": a chat panel on every tab; Claude answers from Kev's bets, tonight's lines and the model (claude function, mode 'chat') ----------
function mdLite(t) {
  const lines = esc(t).split('\n'), out = []; let tbl = null;
  const inline = s => s.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
  const flush = () => { if (!tbl) return; const rows = tbl.filter(r => !/^\s*\|?\s*:?-{2,}/.test(r)).map(r => r.replace(/^\s*\||\|\s*$/g, '').split('|').map(c => inline(c.trim())));
    out.push(`<div class="tablewrap"><table>${rows.map((r, i) => `<tr>${r.map(c => i ? `<td>${c}</td>` : `<th>${c}</th>`).join('')}</tr>`).join('')}</table></div>`); tbl = null; };
  for (const l of lines) {
    if (/^\s*\|.*\|\s*$/.test(l)) { (tbl = tbl || []).push(l); continue; }
    flush();
    if (/^\s*[-*] /.test(l)) out.push(`<div style="padding-left:14px;text-indent:-10px">• ${inline(l.replace(/^\s*[-*] /, ''))}</div>`);
    else if (/^#{1,4} /.test(l)) out.push(`<div style="font-weight:700;margin-top:6px">${inline(l.replace(/^#+ /, ''))}</div>`);
    else out.push(l.trim() ? `<div>${inline(l)}</div>` : '<div style="height:6px"></div>');
  }
  flush(); return out.join('');
}
function addChat() {
  if (C.claudeEnabled === false || document.getElementById('bdChatBtn')) return;
  const css = document.createElement('style');
  css.textContent = `#bdChatBtn{position:fixed;right:16px;z-index:30;border-radius:999px;padding:12px 18px;font-weight:700;box-shadow:0 6px 20px rgba(0,0,0,.25)}
#bdChat{position:fixed;right:16px;z-index:31;width:min(420px,calc(100vw - 32px));height:min(620px,calc(100dvh - 120px));display:flex;flex-direction:column;background:var(--surface);border:1px solid var(--line);border-radius:12px;box-shadow:0 12px 40px rgba(0,0,0,.3);overflow:hidden}
#bdChat[hidden]{display:none}
#bdChat .hd{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:1px solid var(--line)}
#bdChat .log{flex:1;overflow-y:auto;padding:12px 14px;display:flex;flex-direction:column;gap:10px;font-size:14px;line-height:1.45}
#bdChat .m{max-width:92%;padding:8px 11px;border-radius:10px;overflow-wrap:anywhere}
#bdChat .m.u{align-self:flex-end;background:var(--accent);color:var(--accent-ink)}
#bdChat .m.a{align-self:flex-start;background:var(--sunk)}
#bdChat .m table{font-size:12px;margin:6px 0} #bdChat .m td,#bdChat .m th{padding:3px 6px}
#bdChat form{display:flex;gap:8px;padding:10px;border-top:1px solid var(--line)}
#bdChat textarea{flex:1;min-height:40px;max-height:120px;resize:none;font:inherit;font-size:16px}`;
  document.head.append(css);
  const btn = document.createElement('button'); btn.id = 'bdChatBtn'; btn.type = 'button'; btn.className = 'primary'; btn.textContent = 'Ask Claude';
  const box = document.createElement('div'); box.id = 'bdChat'; box.hidden = true; box.setAttribute('role', 'dialog'); box.setAttribute('aria-label', 'Ask Claude');
  box.innerHTML = `<div class="hd"><b>Ask Claude</b><div class="row" style="gap:6px"><button type="button" class="ghost small" data-a="clear">Clear</button><button type="button" class="ghost small" data-a="close" aria-label="Close">✕</button></div></div>
    <div class="log" aria-live="polite"></div>
    <form><textarea rows="1" placeholder="Ask about lines, bets, the model…" aria-label="Message"></textarea><button type="submit" class="primary small">Send</button></form>`;
  document.body.append(btn, box);
  const log = box.querySelector('.log'), ta = box.querySelector('textarea'), send = box.querySelector('form button');
  let hist = []; try { hist = JSON.parse(localStorage.getItem('bd-chat') || '[]'); } catch (_) {}
  const save = () => { try { localStorage.setItem('bd-chat', JSON.stringify(hist.slice(-30))); } catch (_) {} };
  const bubble = m => { const d = document.createElement('div'); d.className = 'm ' + (m.role === 'user' ? 'u' : 'a'); d.innerHTML = m.role === 'user' ? esc(m.content).replace(/\n/g, '<br>') : mdLite(m.content); log.append(d); log.scrollTop = log.scrollHeight; return d; };
  const draw = () => { log.replaceChildren(); if (!hist.length) bubble({ role: 'assistant', content: "Hey Kev. I can see your logged bets, tonight's Hard Rock lines with Pinnacle's prices, the model's numbers and the injury reports. Ask me anything, like **\"Any NBA unders the model and Pinnacle agree on tonight?\"** or **\"How am I doing this week?\"**" }); hist.forEach(bubble); };
  const place = () => { const sb = document.getElementById('slipBar'); const off = sb && !sb.hidden && sb.offsetHeight ? sb.offsetHeight + 12 : 16;
    btn.style.bottom = `calc(${off}px + env(safe-area-inset-bottom,0px))`; box.style.bottom = `calc(${off}px + env(safe-area-inset-bottom,0px))`; };
  const open = v => { box.hidden = !v; btn.hidden = v; place(); if (v) { draw(); setTimeout(() => ta.focus(), 50); } };
  btn.onclick = () => open(true);
  box.addEventListener('click', e => { const a = e.target.closest('[data-a]')?.dataset.a; if (a === 'close') open(false); if (a === 'clear') { hist = []; save(); draw(); } });
  ta.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); box.querySelector('form').requestSubmit(); } });
  box.querySelector('form').addEventListener('submit', async e => {
    e.preventDefault(); const q = ta.value.trim(); if (!q || send.disabled) return;
    hist.push({ role: 'user', content: q }); save(); bubble(hist[hist.length - 1]); ta.value = '';
    send.disabled = true; const wait = bubble({ role: 'assistant', content: 'Thinking…' });
    try {
      const r = await fetch(FN('claude'), { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + await token(), apikey: C.supabaseAnonKey },
        body: JSON.stringify({ mode: 'chat', messages: hist.slice(-16) }) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || !j.reply) throw new Error(j.error || `Claude unavailable (${r.status})`);
      hist.push({ role: 'assistant', content: j.reply }); save(); wait.innerHTML = mdLite(j.reply);
    } catch (err) { wait.textContent = 'Couldn\'t reach Claude: ' + err.message; hist.pop(); save(); ta.value = q; }
    send.disabled = false; log.scrollTop = log.scrollHeight;
  });
  new MutationObserver(place).observe(document.body, { attributes: true, subtree: true, attributeFilter: ['hidden', 'class'] }); place();
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', addChat); else addChat();
// ---------- CBB tab: the cbbBoard doc (today's lines, projections, calls) + the season record from ledger/cbbgl ----------
window.BD_renderCbb = async () => {
  const box = document.getElementById('cbbPanel'); if (!box) return;
  const me = (await getSession()).user.id;
  const [{ data: bd }, { data: led }] = await Promise.all([
    sb.from('docs').select('data').eq('uid', me).eq('coll', '').eq('id', 'cbbBoard').maybeSingle(),
    sb.from('docs').select('id,data').eq('uid', me).eq('coll', 'ledger/cbbgl')]);
  const B = bd?.data, calls = (led || []).map(r => r.data);
  const am = v => v == null ? '—' : (Number(v) > 0 ? '+' + Number(v) : String(v));
  const pct = v => v == null ? '—' : Math.round(v * 100) + '%';
  const time = iso => iso ? new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '';
  // season record: every graded call, and the leans only
  const rec = (filter) => { let w = 0, l = 0, p = 0, u = 0;
    for (const d of calls) for (const c of Object.values(d.calls || {})) { if (!c.result || !filter(c)) continue;
      const o = Number(c.price), dd = o > 0 ? o / 100 : 100 / -o;
      if (c.result === 'won') { w++; u += dd; } else if (c.result === 'lost') { l++; u -= 1; } else p++; }
    return `${w}–${l}${p ? '–' + p : ''} (${u >= 0 ? '+' : ''}${u.toFixed(1)}u)`; };
  const rows = (B?.games || []).slice().sort((a, b) => (a.start || '').localeCompare(b.start || ''));
  const cell = (c, kind, g) => { if (!c) return '<td>—</td>';
    const side = kind === 'spread' ? (c.side === 'home' ? g.home + ' ' + am(c.line) : g.away + ' ' + am(-c.line)) : c.side + ' ' + c.line;
    return `<td class="small">${esc(side)} <span class="muted">${am(c.price)}</span><br>${pct(c.pWin)}${c.lean ? ' <b style="color:var(--accent)">lean</b>' : ''}${c.far ? ' <span class="muted">· model far off the market, ignore</span>' : ''}` +
      `<br><span class="muted">${c.anchor === 'Pinnacle' ? 'vs Pinnacle ' + pct(c.pb) : 'no Pinnacle'}</span></td>`; };
  box.innerHTML = `<div class="row spread"><h2>College hoops</h2><span class="small muted">${B ? 'Updated ' + new Date(B.updated).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }) : ''}</span></div>
    <p class="small muted">Spread and total calls from the app's team ratings against Hard Rock's line, nudged from Pinnacle's fair price. Calls lock an hour before tip; a <b>lean</b> needs 53%+ at a normal price.
    Record so far: leans ${rec(c => c.lean)} · all calls ${rec(() => true)}.</p>
    ${!B ? '<p>No college board yet. It fills in after the 9:31am run once TheRundown is connected.</p>' :
      !rows.length ? '<p>No college games priced today.</p>' :
      `<div class="tablewrap"><table><thead><tr><th>Game</th><th>Tip</th><th>Projection</th><th>Spread call</th><th>Total call</th></tr></thead><tbody>${rows.map(g =>
        `<tr><td>${esc(g.away)} @ ${esc(g.home)}${g.neutral ? ' <span class="muted small">(neutral)</span>' : ''}</td><td class="small">${time(g.start)}</td>
         <td class="small">${esc(g.home)} by ${g.proj.margin}<br>total ${g.proj.total}${g.proj.gp < 3 ? '<br><span class="muted">early season</span>' : ''}</td>
         ${cell(g.calls.spread, 'spread', g)}${cell(g.calls.total, 'total', g)}</tr>`).join('')}</tbody></table></div>`}
    ${B && B.unmatched ? `<p class="small muted">${B.unmatched} games skipped (couldn't match team names).</p>` : ''}`;
};


// ---------- db: Firestore-style documents over the "docs" table ----------
const cache = new Map(), docL = new Map(), collL = new Map(), loaded = new Set();
const K = (c, i) => c + '|' + i;
const clone = v => v === undefined ? undefined : JSON.parse(JSON.stringify(v));
let uid = null;
const rel = p => { const m = String(p).match(/^data\/users\/[^/]+\/?(.*)$/); return m ? m[1] : String(p); };
const split = r => { const i = r.lastIndexOf('/'); return i < 0 ? ['', r] : [r.slice(0, i), r.slice(i + 1)]; };
const docSnap = (c, i) => { const d = cache.get(K(c, i)); return { id: i, exists: d !== undefined, data: () => clone(d) }; };
const collSnap = c => ({ docs: [...cache.entries()].filter(([k]) => k.startsWith(c + '|') && !k.slice(c.length + 1).includes('|')).map(([k, d]) => ({ id: k.slice(c.length + 1), data: () => clone(d) })) });
function emit(c, i) {
  for (const f of docL.get(K(c, i)) || []) try { f(docSnap(c, i)); } catch (e) { console.error(e); }
  for (const f of collL.get(c) || []) try { f(collSnap(c)); } catch (e) { console.error(e); }
}
async function loadDoc(c, i) {
  const { data, error } = await sb.from('docs').select('data').eq('uid', uid).eq('coll', c).eq('id', i).maybeSingle();
  if (error) throw error; if (data) cache.set(K(c, i), data.data); else cache.delete(K(c, i));
}
async function loadColl(c) {
  for (let from = 0; ; from += 1000) {
    const { data, error } = await sb.from('docs').select('id,data').eq('uid', uid).eq('coll', c).range(from, from + 999);
    if (error) throw error; for (const r of data) cache.set(K(c, r.id), r.data); if (data.length < 1000) break;
  }
}
function subscribe() {
  sb.channel('docs-' + uid).on('postgres_changes', { event: '*', schema: 'public', table: 'docs', filter: 'uid=eq.' + uid }, p => {
    if (p.eventType === 'DELETE') { const o = p.old || {}; if (o.id == null) return; cache.delete(K(o.coll, o.id)); emit(o.coll, o.id); }
    else { const r = p.new; cache.set(K(r.coll, r.id), r.data); emit(r.coll, r.id); }
  }).subscribe();
}
class DocRef {
  constructor(r) { this.r = r; [this.c, this.i] = split(r); }
  collection(name) { return new CollRef(this.r + '/' + name); }
  onSnapshot(cb, onErr) {
    const k = K(this.c, this.i); if (!docL.has(k)) docL.set(k, new Set()); docL.get(k).add(cb);
    (loaded.has('d:' + k) ? Promise.resolve() : loadDoc(this.c, this.i).then(() => loaded.add('d:' + k)))
      .then(() => cb(docSnap(this.c, this.i))).catch(e => onErr && onErr(e));
    return () => docL.get(k).delete(cb);
  }
  async get() { await loadDoc(this.c, this.i); return docSnap(this.c, this.i); }
  async set(body) {
    const k = K(this.c, this.i), prev = cache.get(k), data = clone(body);
    cache.set(k, data); emit(this.c, this.i);
    const { error } = await sb.from('docs').upsert({ uid, coll: this.c, id: this.i, data }, { onConflict: 'uid,coll,id' });
    if (error) { if (prev === undefined) cache.delete(k); else cache.set(k, prev); emit(this.c, this.i); throw err(/quota|size/i.test(error.message) ? 'quota_exceeded' : 'write_failed', error.message); }
  }
  async update(patch) { const cur = cache.get(K(this.c, this.i)) || {}; return this.set({ ...cur, ...patch }); }
  async delete() {
    const k = K(this.c, this.i), prev = cache.get(k); cache.delete(k); emit(this.c, this.i);
    const { error } = await sb.from('docs').delete().eq('uid', uid).eq('coll', this.c).eq('id', this.i);
    if (error) { if (prev !== undefined) { cache.set(k, prev); emit(this.c, this.i); } throw err('write_failed', error.message); }
  }
}
class CollRef {
  constructor(r) { this.r = r; }
  doc(id) { return new DocRef(this.r + '/' + id); }
  onSnapshot(cb, onErr) {
    const c = this.r; if (!collL.has(c)) collL.set(c, new Set()); collL.get(c).add(cb);
    (loaded.has('c:' + c) ? Promise.resolve() : loadColl(c).then(() => loaded.add('c:' + c)))
      .then(() => cb(collSnap(c))).catch(e => onErr && onErr(e));
    return () => collL.get(c).delete(cb);
  }
}
const dbCap = { doc: p => new DocRef(rel(p)), collection: p => new CollRef(rel(p)) };

// ---------- ESPN through the espn edge function ----------
const LG_PATH = { nba: 'basketball/nba', nfl: 'football/nfl', cfb: 'football/college-football', nhl: 'hockey/nhl', wnba: 'basketball/wnba', mlb: 'baseball/mlb' };
// ESPN abbreviations -> the StatMuse-style abbreviations the page uses (NBA_AB / NFL_AB)
const AB_FIX = { nba: { GS: 'GSW', NO: 'NOP', NY: 'NYK', SA: 'SAS', UTAH: 'UTA', UTA: 'UTA', WSH: 'WAS', PHO: 'PHX', BK: 'BKN' }, nfl: { WSH: 'WAS', JAC: 'JAX', LA: 'LAR' } };
const fixAb = (lg, ab) => (AB_FIX[lg] || {})[ab] || ab;
const memo = new Map();
async function espn(path, staleMs = 20000) {
  const hit = memo.get(path); if (hit && Date.now() - hit.at < staleMs) return hit.json;
  const res = await fetch(FN('espn') + '?path=' + encodeURIComponent(path), { headers: { Authorization: 'Bearer ' + await token(), apikey: C.supabaseAnonKey } });
  if (!res.ok) throw err('upstream_error', 'ESPN ' + res.status);
  const json = await res.json(); memo.set(path, { at: Date.now(), json }); return json;
}
const etDate = d => new Date(d).toLocaleDateString('en-CA', { timeZone: 'America/New_York' });
const etTime = d => new Date(d).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }) + ' ET';
const ymd = s => String(s).replaceAll('-', '');
function statusOf(st) {
  const t = st?.type || {}; if (/POSTPONED|CANCELED|SUSPENDED/i.test(t.name || '')) return 'postponed';
  return t.state === 'post' ? 'final' : t.state === 'pre' ? 'scheduled' : 'in progress';
}
function teamOut(lg, c) {
  const t = c?.team || {};
  return { name: lg === 'nba' || lg === 'nfl' ? fixAb(lg, t.abbreviation) : (t.shortDisplayName || t.displayName || t.abbreviation),
    score: c?.score != null && c.score !== '' ? Number(c.score) : null, record: c?.records?.[0]?.summary || null,
    rank: c?.curatedRank?.current && c.curatedRank.current <= 25 ? c.curatedRank.current : null };
}
async function getGames({ league, start_date, end_date }) {
  const lg = String(league).toLowerCase(), p = LG_PATH[lg]; if (!p) throw err('bad_request', 'league');
  const dates = ymd(start_date) + (end_date && end_date !== start_date ? '-' + ymd(end_date) : '');
  const j = await espn(`${p}/scoreboard?dates=${dates}&limit=400${lg === 'cfb' ? '&groups=80' : ''}`);
  const games = (j.events || []).map(e => {
    const comp = e.competitions?.[0] || {}, cs = comp.competitors || [], home = cs.find(c => c.homeAway === 'home'), away = cs.find(c => c.homeAway === 'away');
    const st = statusOf(e.status || comp.status), H = teamOut(lg, home), A = teamOut(lg, away);
    if (st === 'scheduled') { H.score = null; A.score = null; }
    return { id: e.id, league: lg, status: st, statusDetail: e.status?.type?.shortDetail || '', date: etDate(e.date), time: etTime(e.date), start: e.date,
      network: (comp.broadcasts || []).flatMap(b => b.names || []).join('/') || null, week: e.week?.number ?? null, home: H, away: A };
  });
  return { games, total_count: games.length, truncated: false };
}
// box score columns the page reads, by league
const NBA_KEYS = { points: 'Points', rebounds: 'Rebounds', assists: 'Assists', minutes: 'MinutesPlayed', 'threePointFieldGoalsMade-threePointFieldGoalsAttempted': 'ThreePointsMade' };
const NFL_KEYS = { passingYards: 'Passing-Yards', passingTouchdowns: 'Passing-Touchdowns', rushingYards: 'Rushing-Yards', rushingTouchdowns: 'Rushing-Touchdowns',
  receptions: 'Receiving-Receptions', receivingYards: 'Receiving-Yards', receivingTouchdowns: 'Receiving-Touchdowns' };
function sidePlayers(lg, block) {
  const map = lg === 'nfl' || lg === 'cfb' ? NFL_KEYS : NBA_KEYS, cols = ['name', 'status', ...new Set(Object.values(map))], by = new Map();
  for (const grp of block?.statistics || []) {
    const keys = grp.keys || [];
    for (const a of grp.athletes || []) {
      const id = a.athlete?.id || a.athlete?.displayName; if (!by.has(id)) by.set(id, { name: a.athlete?.displayName || '', status: '' });
      const row = by.get(id);
      if (a.didNotPlay || !(a.stats || []).length) { if (lg === 'nba') row.status = 'DNP'; continue; }
      keys.forEach((k, i) => { const col = map[k]; if (!col) return; const raw = String(a.stats[i] ?? ''), v = /^-?\d+(\.\d+)?$/.test(raw) ? raw : raw.split('-')[0]; row[col] = v === '' || v === '--' ? null : Number(v); });
    }
  }
  return { players: { columns: cols, rows: [...by.values()].map(r => cols.map(c => r[c] ?? (c === 'status' ? '' : null))) } };
}
async function getGameDetails({ league, game_ids }) {
  const lg = String(league).toLowerCase(), p = LG_PATH[lg];
  const games = await Promise.all((game_ids || []).map(async id => {
    try {
      const j = await espn(`${p}/summary?event=${id}`, 15000);
      const comp = j.header?.competitions?.[0] || {}, cs = comp.competitors || [];
      const hc = cs.find(c => c.homeAway === 'home'), ac = cs.find(c => c.homeAway === 'away');
      const ab = c => c?.team?.abbreviation;
      const blocks = j.boxscore?.players || [];
      const blockFor = c => blocks.find(b => b.team?.abbreviation === ab(c)) || null;
      const ls = c => (c?.linescores || []).map(x => Number(x.displayValue ?? x.value));
      const game = { id, home: { name: lg === 'cfb' ? hc?.team?.shortDisplayName : fixAb(lg, ab(hc)), score: hc?.score != null ? Number(hc.score) : null },
        away: { name: lg === 'cfb' ? ac?.team?.shortDisplayName : fixAb(lg, ab(ac)), score: ac?.score != null ? Number(ac.score) : null },
        lineScore: { home: ls(hc), away: ls(ac) }, stats: { home: sidePlayers(lg, blockFor(hc)), away: sidePlayers(lg, blockFor(ac)) } };
      return { ok: true, game };
    } catch (e) { return { ok: false, error: 'No box score yet' }; }
  }));
  return { games };
}
const norm = s => String(s || '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/-/g, ' ').replace(/[^a-z ]/g, '').split(/\s+/).filter(w => w && !['jr', 'sr', 'ii', 'iii', 'iv', 'v'].includes(w)).join(' ');
async function searchPlayer({ league, name }) {
  const n = norm(name); const { data, error } = await sb.from('player_games').select('player_id,name,name_norm,date').eq('league', String(league).toLowerCase()).ilike('name_norm', `%${n.split(' ').pop()}%`).order('date', { ascending: false }).limit(200);
  if (error) throw err('upstream_error', error.message);
  const seen = new Map(); for (const r of data || []) if (!seen.has(r.player_id)) seen.set(r.player_id, r);
  const list = [...seen.values()].sort((a, b) => (b.name_norm === n) - (a.name_norm === n));
  return [{ results: list.slice(0, 5).map(r => ({ player_id: r.player_id, name: r.name })) }];
}
const STAT_KEY = { Points: 'pts', Rebounds: 'reb', Assists: 'ast', ThreePointsMade: 'fg3', MinutesPlayed: 'min', 'Passing-Yards': 'pass', 'Rushing-Yards': 'rush', 'Receiving-Yards': 'rec' };
async function getStats(args) {
  const groups = (args.groups || []).map(g => g.type), pf = (args.filters || []).find(f => f.type === 'player');
  if (!(groups.includes('player') && groups.includes('game') && pf?.playerIds?.length)) return { columns: [], rows: [] }; // team/ATS queries: not available
  const { data, error } = await sb.from('player_games').select('date,opp_ab,dnp,stats').eq('league', String(args.league).toLowerCase()).eq('player_id', String(pf.playerIds[0])).order('date', { ascending: false }).limit(args.limit || 40);
  if (error) throw err('upstream_error', error.message);
  const stats = args.stats || [];
  return { columns: ['player', 'date', 'opponent', ...stats], rows: (data || []).map(r => [null, r.date, r.opp_ab, ...stats.map(s => r.dnp ? null : (r.stats?.[STAT_KEY[s]] ?? null))]) };
}

// ---------- scheduled task status / manual refresh ----------
const JOBS = [
  ['pregame', 'Bet Desk: pre-game refresh + shadow log', 'hourly :31, 8am–10pm'],
  ['nbagl', 'Bet Desk: NBA spreads & totals lock', 'hourly :31, 10am–11pm'],
  ['nba_daily', 'Bet Desk: NBA daily refresh', 'daily 9:31am'],
  ['nfl_weekly', 'Bet Desk: weekly NFL refresh', 'Wed 9:31am (+ Thu/Sun/Mon lines)'],
  ['injuries', 'Bet Desk: injury report refresh', 'daily 9:31am + before games'],
  ['settle', 'Bet Desk: settle bets', '12:31am and 2:31am'],
  ['closing', 'Bet Desk: closing lines + status', '4:31am'],
  ['recap', 'Bet Desk: weekly recap', 'Mon 9:31am'],
  ['live', 'Bet Desk: Sunday live updates', 'Sun hourly 11am–11pm']];
async function listTriggers() {
  const { data } = await sb.from('job_runs').select('job,started_at,finished_at,ok,summary').order('started_at', { ascending: false }).limit(300);
  const last = {}; for (const r of data || []) if (!last[r.job]) last[r.job] = r;
  return { data: JOBS.map(([id, name, when]) => { const r = last[id];
    return { id, name, enabled: true, schedule: when, next_run_at: null,
      last_run: r ? { status: r.ok === false ? 'ROUTINE_RUN_STATUS_FAILED' : 'ROUTINE_RUN_STATUS_SUCCEEDED', fired_at: r.started_at, summary: r.summary } : null }; }) };
}
async function fireTrigger({ trigger_id }) {
  const res = await fetch(FN('jobs'), { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + await token(), apikey: C.supabaseAnonKey }, body: JSON.stringify({ job: trigger_id }) });
  if (!res.ok) throw err('upstream_error', 'Could not start ' + trigger_id); return { ok: true };
}

// ---------- mcp-style tool calls ----------
const TOOLS = {
  'Stat Muse': { get_games: getGames, get_game_details: getGameDetails, search_player: searchPlayer, get_stats: getStats },
  'Claude Code Remote': { list_triggers: listTriggers, fire_trigger: fireTrigger } };
const mcpCap = {
  async callTool(server, tool, args = {}) {
    const fn = TOOLS[server]?.[tool]; if (!fn) throw err('server_not_connected', `${server} isn't available in the standalone app`);
    await getSession(); return { payload: await fn(args), cache: { storedAt: Date.now() } };
  },
  watchTool(server, tool, args, cb, opts = {}) {
    let stop = false; const run = () => mcpCap.callTool(server, tool, args).then(result => { if (!stop) cb({ type: 'data', result }); }, error => { if (!stop) cb({ type: 'error', error }); });
    run(); const t = setInterval(run, Math.max(10000, opts.refetchInterval || 30000));
    return () => { stop = true; clearInterval(t); };
  },
  async invalidate() { memo.clear(); } };

// ---------- Claude (your Anthropic key) for slip reading and the parlay check ----------
const toB64 = f => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(',')[1]); r.onerror = rej; r.readAsDataURL(f); });
const sampleCap = {
  async json(prompt, opts = {}) {
    const images = await Promise.all((opts.images || []).map(async f => ({ media_type: f.type || 'image/png', data: await toB64(f) })));
    const res = await fetch(FN('claude'), { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + await token(), apikey: C.supabaseAnonKey }, body: JSON.stringify({ prompt, images }) });
    if (res.status === 404) throw err('not_granted', 'Claude function not deployed');
    const j = await res.json(); if (!res.ok || j.error) throw err('upstream_error', j.error || 'Claude error'); return j.result;
  } };
const downloadsCap = { async save({ filename, data }) { const a = document.createElement('a'); a.href = URL.createObjectURL(data); a.download = filename || 'download'; document.body.append(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000); } };

// ---------- window.claude.use ----------
let ready = null;
function ensureReady() {
  if (!ready) ready = getSession().then(s => { uid = s.user.id; subscribe(); });
  return ready;
}
window.claude = { use: async name => {
  await ensureReady();
  if (name === 'user') return { id: async () => uid };
  if (name === 'db') return dbCap;
  if (name === 'mcp') return mcpCap;
  if (name === 'sample') return C.claudeEnabled === false ? null : sampleCap;
  if (name === 'downloads') return downloadsCap;
  return null; } };

// ---------- Game night: a full-screen live view of Kev's open bets, leg by leg, plus the live scores (ESPN, every 20s) ----------
const GN = { open: false, timer: null, lock: null, teams: new Map(), last: null };
const GN_MKT = [[/p\s*\+\s*r\s*\+\s*a|\bpra\b|points\s*\+\s*rebounds\s*\+\s*assists/i, 'pra'], [/p\s*\+\s*r\b|pts\s*\+\s*reb|points\s*\+\s*rebounds/i, 'pr'],
  [/p\s*\+\s*a\b|pts\s*\+\s*ast|points\s*\+\s*assists/i, 'pa'], [/r\s*\+\s*a\b|reb\s*\+\s*ast|rebounds\s*\+\s*assists/i, 'ra'], [/3pm|3pt|three|\b3s\b|3-pointers/i, 'fg3'],
  [/steal/i, 'stl'], [/block/i, 'blk'], [/\breb/i, 'reb'], [/\bast\b|assist/i, 'ast'], [/\bpts?\b|point/i, 'pts'], [/pass(ing)?\s*(td|touchdown)/i, 'passTd'],
  [/pass(ing)?\s*att/i, 'att'], [/complet/i, 'cmp'], [/intercept|\bints?\b/i, 'int'], [/rush(ing)?\s*att|carries/i, 'car'], [/rush(ing)?\s*\+\s*rec/i, 'rr'],
  [/pass/i, 'pass'], [/rush/i, 'rush'], [/receptions|\brecs\b|catches/i, 'recs'], [/rec|receiv/i, 'rec']];
const GN_NAME = { pts: 'PTS', reb: 'REB', ast: 'AST', fg3: '3PM', stl: 'STL', blk: 'BLK', pra: 'PRA', pr: 'P+R', pa: 'P+A', ra: 'R+A', pass: 'Pass yds', rush: 'Rush yds', rec: 'Rec yds',
  recs: 'Rec', car: 'Rush att', att: 'Pass att', cmp: 'Comp', passTd: 'Pass TD', int: 'INT', rr: 'Rush+Rec yds', td: 'Anytime TD', dd: 'Double-double', td3: 'Triple-double' };
const GN_TEAMS = { nba: { ATL: 'Hawks', BOS: 'Celtics', BKN: 'Nets', CHA: 'Hornets', CHI: 'Bulls', CLE: 'Cavaliers', DAL: 'Mavericks', DEN: 'Nuggets', DET: 'Pistons', GSW: 'Warriors', HOU: 'Rockets', IND: 'Pacers', LAC: 'Clippers', LAL: 'Lakers', MEM: 'Grizzlies', MIA: 'Heat', MIL: 'Bucks', MIN: 'Timberwolves', NOP: 'Pelicans', NYK: 'Knicks', OKC: 'Thunder', ORL: 'Magic', PHI: '76ers', PHX: 'Suns', POR: 'Trail Blazers', SAC: 'Kings', SAS: 'Spurs', TOR: 'Raptors', UTA: 'Jazz', WAS: 'Wizards' },
  nfl: { ARI: 'Cardinals', ATL: 'Falcons', BAL: 'Ravens', BUF: 'Bills', CAR: 'Panthers', CHI: 'Bears', CIN: 'Bengals', CLE: 'Browns', DAL: 'Cowboys', DEN: 'Broncos', DET: 'Lions', GB: 'Packers', HOU: 'Texans', IND: 'Colts', JAX: 'Jaguars', KC: 'Chiefs', LAC: 'Chargers', LAR: 'Rams', LV: 'Raiders', MIA: 'Dolphins', MIN: 'Vikings', NE: 'Patriots', NO: 'Saints', NYG: 'Giants', NYJ: 'Jets', PHI: 'Eagles', PIT: 'Steelers', SEA: 'Seahawks', SF: '49ers', TB: 'Buccaneers', TEN: 'Titans', WAS: 'Commanders' } };
// 'Knicks' / 'New York Knicks' / 'NYK' -> [{lg, ab, name}] (a nickname can exist in both leagues: Cardinals is NFL only, Kings NBA only, so few clashes)
function gnTeam(s) {
  const n = norm(s); if (!n) return null; const out = [];
  for (const lg of ['nba', 'nfl']) for (const [ab, nick] of Object.entries(GN_TEAMS[lg])) { const k = norm(nick); if (n === k || n.endsWith(' ' + k) || n === ab.toLowerCase()) out.push({ lg, ab, name: nick }); }
  return out.length ? out : null;
}
const GN_NBA = new Set(['pts', 'reb', 'ast', 'fg3', 'stl', 'blk', 'pra', 'pr', 'pa', 'ra', 'dd', 'td3']);
function gnLeg(t) {
  t = t.trim(); if (!t) return { text: t };
  let tm = t.match(/^(.+?)\s+(?:ml|moneyline|to win)\s*$/i);
  if (tm && gnTeam(tm[1])) return { text: t, team: gnTeam(tm[1]), kind: 'ml', line: 0 };
  tm = t.match(/^(.+?)\s+([+-]\d+(?:\.\d+)?)\s*(?:spread|pts)?\s*$/i);
  if (tm && gnTeam(tm[1])) return { text: t, team: gnTeam(tm[1]), kind: 'spread', line: +tm[2] };
  let m = t.match(/^(.+?)\s+(?:anytime\s*td|atd|anytime touchdown|to score a td)(?:\s*scorer)?\s*$/i);
  if (m) return { text: t, player: m[1].trim(), market: 'td', side: 'over', line: 0.5 };
  m = t.match(/^(.+?)\s+(?:to record (?:a |an )?)?(triple[\s-]*double|double[\s-]*double)\s*(?:yes)?\s*$/i);
  if (m) return { text: t, player: m[1].trim(), market: /^t/i.test(m[2]) ? 'td3' : 'dd', side: 'over', line: 0.5 };
  m = t.match(/^(.+?)\s+(?:to record\s+)?(\d+)\+\s*(.*)$/i);
  if (m) { const mk = (GN_MKT.find(([rx]) => rx.test(m[3])) || [])[1]; if (mk) return { text: t, player: m[1].trim(), market: mk, side: 'over', line: +m[2] - 0.5 }; }
  m = t.match(/^(.+?)\s+(o|u|over|under)\s*([\d.]+)\+?\s*(.*)$/i);
  if (!m) return { text: t };
  const mk = (GN_MKT.find(([rx]) => rx.test(m[4])) || [])[1]; if (!mk) return { text: t };
  return { text: t, player: m[1].replace(/\s*\([^)]*\)\s*$/, '').trim(), market: mk, side: /^o/i.test(m[2]) ? 'over' : 'under', line: +m[3] };
}
function gnLegs(label) {
  let s = String(label || '').replace(/\([^)]*@[^)]*\)/g, '');
  if (s.slice(0, 40).includes(':')) s = s.split(':').slice(1).join(':');
  return s.split(/\s*(?:,|;|\n|\s\+\s|\s\/\s)\s*/).filter(x => x.trim()).map(gnLeg);
}
// NBA and NFL box score -> {norm name: {stat: value}}
const GN_NBA_COL = { points: 'pts', rebounds: 'reb', assists: 'ast', steals: 'stl', blocks: 'blk', minutes: 'min', 'threePointFieldGoalsMade-threePointFieldGoalsAttempted': 'fg3' };
const GN_NFL_COL = { 'passing/passingYards': 'pass', 'passing/passingTouchdowns': 'passTd', 'passing/interceptions': 'int', 'passing/completions/passingAttempts': 'cmp',
  'rushing/rushingAttempts': 'car', 'rushing/rushingYards': 'rush', 'rushing/rushingTouchdowns': 'rushTd', 'receiving/receptions': 'recs', 'receiving/receivingYards': 'rec', 'receiving/receivingTouchdowns': 'recTd' };
function gnBox(lg, j) {
  const out = {};
  for (const blk of j?.boxscore?.players || []) for (const grp of blk.statistics || []) {
    const gname = (grp.name || '').toLowerCase(), keys = grp.keys || [];
    for (const a of grp.athletes || []) {
      const n = norm(a.athlete?.displayName); if (!n) continue;
      const p = out[n] = out[n] || { name: a.athlete?.displayName, team: blk.team?.abbreviation, dnp: false };
      if (a.didNotPlay || !(a.stats || []).length) { if (lg === 'nba') p.dnp = true; continue; }
      keys.forEach((k, i) => {
        const col = lg === 'nba' ? GN_NBA_COL[k] : GN_NFL_COL[gname + '/' + k]; if (!col) return;
        const raw = String(a.stats[i] ?? '');
        if (col === 'cmp') { const [c, at] = raw.split('/'); p.cmp = +c || 0; p.att = +at || 0; return; }
        p[col] = /^-?\d+(\.\d+)?$/.test(raw) ? +raw : (+raw.split('-')[0] || 0);
      });
    }
  }
  return out;
}
function gnValue(s, mk) {
  if (!s) return null; const g = k => +s[k] || 0;
  if (mk === 'dd' || mk === 'td3') return ['pts', 'reb', 'ast', 'stl', 'blk'].filter(k => g(k) >= 10).length;
  return { pra: g('pts') + g('reb') + g('ast'), pr: g('pts') + g('reb'), pa: g('pts') + g('ast'), ra: g('reb') + g('ast'), td: g('rushTd') + g('recTd'), rr: g('rush') + g('rec') }[mk] ?? g(mk);
}
// game progress 0..1 from the scoreboard status
function gnFrac(lg, st) {
  const t = st?.type || {}; if (t.state === 'post') return 1; if (t.state !== 'in') return 0;
  const per = st.period || 1, n = lg === 'nba' ? 12 : 15, [mm, ss] = String(st.displayClock || '0:00').split(':').map(Number);
  const left = (mm || 0) + (ss || 0) / 60, regs = 4;
  if (per > regs) return 0.99;
  return Math.min(0.99, ((per - 1) * n + (n - left)) / (regs * n));
}
async function gnTeamOf(lg, player) {
  const k = lg + ':' + norm(player); if (GN.teams.has(k)) return GN.teams.get(k);
  const n = norm(player), last = n.split(' ').pop();
  const { data } = await sb.from('player_games').select('name_norm,team_ab,date').eq('league', lg).ilike('name_norm', `%${last}%`).order('date', { ascending: false }).limit(60);
  const rows = data || [];
  const hit = rows.find(r => r.name_norm === n) || rows.find(r => r.name_norm.split(' ').pop() === last && r.name_norm[0] === n[0] && n.split(' ').length > 1)
    || (n.split(' ').length === 1 && new Set(rows.filter(r => r.name_norm.split(' ').pop() === last).map(r => r.name_norm)).size === 1 ? rows.find(r => r.name_norm.split(' ').pop() === last) : null);
  const v = hit ? { ab: hit.team_ab, name: hit.name_norm } : null; GN.teams.set(k, v); return v;
}
const gnAb = (lg, ab) => fixAb(lg, ab);
const gnMoney = v => (v < 0 ? '−$' : '$') + Math.abs(v).toFixed(2).replace(/\.00$/, '');
function gnToWin(b) { const o = Number(b.odds); const d = o > 0 ? o / 100 : 100 / -o; return (Number(b.stake) || 0) * d * (1 + (Number(b.boost) || 0) / 100); }
async function gnLoad() {
  const me = (await getSession()).user.id, since = etDate(Date.now() - 36 * 3600e3), today = etDate(Date.now());
  const { data: rows } = await sb.from('docs').select('id,data').eq('uid', me).eq('coll', 'ledger/bets');
  const bets = (rows || []).map(r => ({ id: r.id, ...r.data })).filter(b => b.status === 'open' && (b.date || '') >= since);
  // scoreboards for both leagues (NFL: this week's games)
  const boards = {};
  await Promise.all(['nba', 'nfl'].map(async lg => {
    try { const j = await espn(`${LG_PATH[lg]}/scoreboard?dates=${ymd(lg === 'nfl' ? etDate(Date.now() - 3 * 864e5) : today)}${lg === 'nfl' ? '-' + ymd(etDate(Date.now() + 4 * 864e5)) : ''}&limit=100`, 15000);
      boards[lg] = (j.events || []).map(e => { const c = e.competitions?.[0] || {}, cs = c.competitors || [], h = cs.find(x => x.homeAway === 'home'), a = cs.find(x => x.homeAway === 'away'), st = e.status || c.status;
        return { lg, id: e.id, start: e.date, st, state: st?.type?.state, detail: st?.type?.shortDetail || '', home: { ab: gnAb(lg, h?.team?.abbreviation), score: h?.score }, away: { ab: gnAb(lg, a?.team?.abbreviation), score: a?.score },
          frac: gnFrac(lg, st), net: (c.broadcasts || []).flatMap(x => x.names || []).join('/') }; }); }
    catch (e) { boards[lg] = []; }
  }));
  // place each leg in a game, then read that game's box score
  const legs = []; for (const b of bets) { b._legs = gnLegs(b.label); for (const L of b._legs) if (L.player) legs.push(L); }
  await Promise.all(legs.map(async L => {
    const lgs = GN_NBA.has(L.market) ? ['nba'] : ['nfl'];
    for (const lg of lgs) { const t = await gnTeamOf(lg, L.player).catch(() => null); if (!t) continue;
      const g = (boards[lg] || []).filter(x => x.home.ab === gnAb(lg, t.ab) || x.away.ab === gnAb(lg, t.ab)).sort((x, y) => (x.state === 'in' ? -1 : 0) - (y.state === 'in' ? -1 : 0) || Math.abs(Date.parse(x.start) - Date.now()) - Math.abs(Date.parse(y.start) - Date.now()))[0];
      if (g) { L.lg = lg; L.game = g; L.key = t.name; } }
  }));
  for (const b of bets) for (const L of b._legs) if (L.team) {
    const near = g => Math.abs(Date.parse(g.start) - Date.now());
    const cands = L.team.flatMap(t => (boards[t.lg] || []).filter(g => g.home.ab === t.ab || g.away.ab === t.ab).map(g => ({ g, t })))
      .filter(x => b.sport ? x.t.lg === String(b.sport).toLowerCase() : true).sort((x, y) => (x.g.state === 'in' ? -1 : 0) - (y.g.state === 'in' ? -1 : 0) || near(x.g) - near(y.g));
    if (!cands.length) { L.status = 'unknown'; continue; }
    const { g, t } = cands[0]; L.game = g; L.lg = t.lg; L.name = t.name;
    const home = g.home.ab === t.ab, us = Number(home ? g.home.score : g.away.score) || 0, them = Number(home ? g.away.score : g.home.score) || 0;
    L.margin = us - them; const v = L.margin + (L.kind === 'spread' ? L.line : 0);
    L.status = g.state === 'pre' ? 'pre' : g.state === 'post' ? (v > 0 ? 'hit' : v === 0 ? (L.kind === 'ml' ? 'lost' : 'push') : 'lost') : 'live';
    L.ahead = v > 0 ? 'win' : v < 0 ? 'lose' : 'tie';
  }
  const boxes = new Map();
  await Promise.all([...new Set(legs.filter(L => L.game && L.game.state !== 'pre').map(L => L.lg + ':' + L.game.id))].map(async k => {
    const [lg, id] = k.split(':'); try { boxes.set(k, gnBox(lg, await espn(`${LG_PATH[lg]}/summary?event=${id}`, 15000))); } catch (_) {}
  }));
  for (const L of legs) {
    if (!L.game) { L.status = 'unknown'; continue; }
    const g = L.game, box = boxes.get(L.lg + ':' + g.id) || {}, s = box[L.key] || box[norm(L.player)] || null;
    L.v = g.state === 'pre' ? null : s ? gnValue(s, L.market) : 0; L.dnp = s?.dnp; L.min = s?.min;
    const need = L.market === 'dd' ? 2 : L.market === 'td3' ? 3 : L.market === 'td' ? 1 : null;
    const over = L.side === 'over', done = g.state === 'post';
    if (g.state === 'pre') L.status = 'pre';
    else if (L.dnp && done) L.status = 'void';
    else if (need != null) L.status = L.v >= need ? 'hit' : done ? 'lost' : 'live';
    else if (over) L.status = L.v > L.line ? 'hit' : done ? (L.v === L.line ? 'push' : 'lost') : 'live';
    else L.status = L.v > L.line ? 'lost' : done ? (L.v === L.line ? 'push' : 'hit') : 'live';
    if (L.status === 'live' && need == null && g.frac > 0.05) L.pace = L.v / g.frac;
  }
  for (const b of bets) {
    const ls = b._legs, tracked = ls.filter(L => L.player || L.team);
    b._lost = ls.find(L => L.status === 'lost'); b._hit = ls.filter(L => L.status === 'hit').length;
    b._state = b._lost ? 'lost' : tracked.length === ls.length && ls.every(L => L.status === 'hit' || L.status === 'push' || L.status === 'void') ? 'won' : ls.some(L => L.status === 'live') ? 'live' : 'pre';
  }
  return { bets, boards, at: Date.now() };
}
function gnCss() {
  if (document.getElementById('gnCss')) return;
  const c = document.createElement('style'); c.id = 'gnCss';
  c.textContent = `#bdGN{position:fixed;inset:0;z-index:60;background:#0B0F16;color:#E8EDF5;overflow-y:auto;padding:calc(env(safe-area-inset-top,0px) + 12px) 16px calc(env(safe-area-inset-bottom,0px) + 24px);font-size:15px}
#bdGN .gn-top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}
#bdGN h1{font:800 28px var(--display,sans-serif);text-transform:uppercase;margin:0;letter-spacing:.02em}
#bdGN .gn-sum{display:flex;gap:18px;flex-wrap:wrap;color:#93A0B5;font-size:13px;margin-bottom:14px} #bdGN .gn-sum b{color:#E8EDF5;font-size:18px;font-family:var(--display,sans-serif)}
#bdGN .gn-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}
#bdGN .gn-bet{background:#161D29;border:1px solid #2C3647;border-radius:12px;padding:12px 14px}
#bdGN .gn-bet[data-s=lost]{opacity:.55} #bdGN .gn-bet[data-s=won]{border-color:#3FCB82}
#bdGN .gn-bh{display:flex;justify-content:space-between;gap:8px;align-items:baseline;margin-bottom:8px} #bdGN .gn-bh .t{font-weight:700} #bdGN .gn-bh .w{color:#93A0B5;font-size:13px;white-space:nowrap}
#bdGN .gn-leg{padding:6px 0;border-top:1px solid #222B3A} #bdGN .gn-leg:first-of-type{border-top:0}
#bdGN .gn-lt{display:flex;justify-content:space-between;gap:8px;font-size:14px} #bdGN .gn-lt .v{font-family:var(--display,sans-serif);font-size:22px;font-weight:800;line-height:1}
#bdGN .gn-bar{height:7px;background:#222B3A;border-radius:4px;position:relative;margin:6px 0 3px;overflow:hidden} #bdGN .gn-bar i{position:absolute;left:0;top:0;bottom:0;border-radius:4px;background:#F2B544}
#bdGN .gn-bar u{position:absolute;top:-2px;bottom:-2px;width:2px;background:#E8EDF5;opacity:.6}
#bdGN .gn-ls{font-size:12px;color:#93A0B5;display:flex;justify-content:space-between;gap:8px}
#bdGN .gn-leg[data-s=hit] .v,#bdGN .ok{color:#3FCB82} #bdGN .gn-leg[data-s=hit] .gn-bar i{background:#3FCB82} #bdGN .gn-leg[data-s=lost] .v,#bdGN .bad{color:#FF7A70} #bdGN .gn-leg[data-s=lost] .gn-bar i{background:#FF7A70}
#bdGN .gn-games{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:8px;margin-top:18px}
#bdGN .gn-g{background:#121823;border:1px solid #222B3A;border-radius:10px;padding:8px 10px;font-size:14px} #bdGN .gn-g.mine{border-color:#6E93FF}
#bdGN .gn-g .r{display:flex;justify-content:space-between} #bdGN .gn-g .d{font-size:12px;color:#93A0B5;margin-top:3px} #bdGN .gn-g .d.live{color:#FF7A70;font-weight:600}
#bdGN h2{font:700 14px sans-serif;text-transform:uppercase;letter-spacing:.08em;color:#93A0B5;margin:18px 0 8px}
#bdGN button{background:#1D2533;color:#E8EDF5;border:1px solid #2C3647;border-radius:8px;padding:8px 12px;font-weight:600}`;
  document.head.append(c);
}
function gnLegHtml(L) {
  if (L.team) {
    const g = L.game, what = L.kind === 'ml' ? 'to win' : `${L.line > 0 ? '+' : ''}${L.line}`;
    if (!g) return `<div class="gn-leg"><div class="gn-lt"><span>${esc(L.text)}</span></div><div class="gn-ls"><span>No game found today</span></div></div>`;
    const when = g.state === 'pre' ? new Date(g.start).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : g.detail;
    const mg = L.margin == null || g.state === 'pre' ? '—' : (L.margin > 0 ? '+' : '') + L.margin;
    const right = L.status === 'hit' ? '<span class="ok">✓ Hit</span>' : L.status === 'lost' ? '<span class="bad">✗ Lost</span>' : L.status === 'push' ? 'Push'
      : L.status === 'live' ? (L.ahead === 'win' ? `<span class="ok">${L.kind === 'ml' ? 'Winning' : 'Covering'}</span>` : L.ahead === 'tie' ? 'Even' : `<span class="bad">${L.kind === 'ml' ? 'Losing' : 'Not covering'}</span>`) : '';
    return `<div class="gn-leg" data-s="${L.status}"><div class="gn-lt"><span>${esc(L.name)} <b>${what}</b></span><span class="v">${mg}</span></div>
      <div class="gn-ls"><span>${esc(`${g.away.ab} ${g.state === 'pre' ? '' : g.away.score ?? ''} @ ${g.home.ab} ${g.state === 'pre' ? '' : g.home.score ?? ''} · ${when}`)}</span><span>${right}</span></div></div>`;
  }
  if (!L.player) return `<div class="gn-leg"><div class="gn-lt"><span>${esc(L.text)}</span></div><div class="gn-ls"><span>Can't follow this one live</span></div></div>`;
  const nm = GN_NAME[L.market] || L.market, g = L.game;
  const tag = L.market === 'td' ? '' : L.market === 'dd' || L.market === 'td3' ? '' : `${L.side === 'over' ? 'o' : 'u'}${L.line}`;
  if (!g) return `<div class="gn-leg"><div class="gn-lt"><span>${esc(L.player)} ${tag} ${nm}</span></div><div class="gn-ls"><span>No game found today</span></div></div>`;
  const tgt = L.market === 'dd' ? 2 : L.market === 'td3' ? 3 : L.market === 'td' ? 1 : L.line;
  const pct = L.v == null ? 0 : Math.min(100, (L.v / Math.max(tgt, 0.5)) * 100), mark = L.side === 'under' ? 100 : Math.min(100, (L.line / Math.max(tgt, 0.5)) * 100);
  const when = g.state === 'pre' ? new Date(g.start).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : g.detail;
  const right = L.status === 'hit' ? '<span class="ok">✓ Hit</span>' : L.status === 'lost' ? '<span class="bad">✗ Lost</span>' : L.status === 'void' ? 'Did not play' : L.status === 'push' ? 'Push'
    : L.pace != null ? `on pace for ${L.pace.toFixed(L.pace < 10 ? 1 : 0)}` : '';
  const left = `${g.away.ab} ${g.away.score ?? ''} @ ${g.home.ab} ${g.home.score ?? ''} · ${when}${L.min != null && L.lg === 'nba' && g.state !== 'pre' ? ` · ${L.min} min` : ''}`;
  return `<div class="gn-leg" data-s="${L.status}"><div class="gn-lt"><span>${esc(L.player)} <b>${tag}</b> ${nm}</span><span class="v">${L.v == null ? '—' : L.v}${['dd', 'td3'].includes(L.market) && L.v != null ? `<small style="font-size:13px;opacity:.7">/${tgt}</small>` : ''}</span></div>
    <div class="gn-bar"><i style="width:${pct}%"></i>${L.side === 'over' && !['td', 'dd', 'td3'].includes(L.market) ? `<u style="left:calc(${mark}% - 1px)"></u>` : ''}</div>
    <div class="gn-ls"><span>${esc(left)}</span><span>${right}</span></div></div>`;
}
function gnRender(D) {
  const box = document.getElementById('bdGN'); if (!box) return;
  const bets = D.bets.sort((a, b) => ({ live: 0, pre: 1, won: 2, lost: 3 }[a._state] - { live: 0, pre: 1, won: 2, lost: 3 }[b._state]));
  const alive = bets.filter(b => b._state !== 'lost'), risk = bets.reduce((s, b) => s + (b.bonus ? 0 : Number(b.stake) || 0), 0), win = alive.reduce((s, b) => s + gnToWin(b), 0);
  const mine = new Set(bets.flatMap(b => b._legs.filter(L => L.game).map(L => L.lg + ':' + L.game.id)));
  const games = ['nba', 'nfl'].flatMap(lg => (D.boards[lg] || []).filter(g => g.state === 'in' || mine.has(lg + ':' + g.id) || (lg === 'nba' && etDate(g.start) === etDate(Date.now()))))
    .sort((a, b) => (a.state === 'in' ? 0 : a.state === 'pre' ? 1 : 2) - (b.state === 'in' ? 0 : b.state === 'pre' ? 1 : 2) || Date.parse(a.start) - Date.parse(b.start));
  box.querySelector('.body').innerHTML = `
    <div class="gn-sum"><span>Open bets <b>${bets.length}</b></span><span>Still alive <b>${alive.length}</b></span><span>At risk <b>${gnMoney(risk)}</b></span><span>Live to win <b class="ok">${gnMoney(win)}</b></span><span>Updated ${new Date(D.at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' })}</span></div>
    <div class="gn-grid">${bets.map(b => {
      const n = b._legs.length, head = n > 1 ? `${b._hit}/${n} legs hit` : '';
      const st = b._state === 'won' ? '<span class="ok">✓ Winner</span>' : b._state === 'lost' ? '<span class="bad">✗ Lost</span>' : head;
      return `<div class="gn-bet" data-s="${b._state}"><div class="gn-bh"><span class="t">${n > 1 ? `${n}-leg ${esc(b.type || 'parlay')}` : esc(b._legs[0]?.player || b.label)} ${st ? '· ' + st : ''}</span><span class="w">$${esc(b.stake)} → ${gnMoney(gnToWin(b))} · ${Number(b.odds) > 0 ? '+' : ''}${esc(b.odds)}</span></div>${b._legs.map(gnLegHtml).join('')}</div>`; }).join('')
      || '<p style="color:#93A0B5">No open bets right now. Log a bet and it shows up here with live stats.</p>'}</div>
    <h2>Scores</h2>
    <div class="gn-games">${games.map(g => `<div class="gn-g ${mine.has(g.lg + ':' + g.id) ? 'mine' : ''}"><div class="r"><span>${esc(g.away.ab)}</span><b>${g.state === 'pre' ? '' : esc(g.away.score ?? '')}</b></div><div class="r"><span>${esc(g.home.ab)}</span><b>${g.state === 'pre' ? '' : esc(g.home.score ?? '')}</b></div>
      <div class="d ${g.state === 'in' ? 'live' : ''}">${g.lg.toUpperCase()} · ${g.state === 'pre' ? new Date(g.start).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }) + (g.net ? ' · ' + esc(g.net) : '') : esc(g.detail)}</div></div>`).join('') || '<p style="color:#93A0B5">No games on right now.</p>'}</div>`;
}
async function gnTick() {
  if (!GN.open) return;
  try { const D = await gnLoad(); GN.last = D; gnRender(D); } catch (e) { console.error('game night', e); const s = document.querySelector('#bdGN .gn-sum span:last-child'); if (s) s.textContent = 'Could not refresh: retrying'; }
  if (GN.open) GN.timer = setTimeout(gnTick, 20000);
}
async function gnWake() { try { if ('wakeLock' in navigator && GN.open && document.visibilityState === 'visible') GN.lock = await navigator.wakeLock.request('screen'); } catch (_) {} }
function gnOpen() {
  gnCss(); GN.open = true;
  const box = document.createElement('div'); box.id = 'bdGN'; box.setAttribute('role', 'dialog'); box.setAttribute('aria-label', 'Game night');
  box.innerHTML = `<div class="gn-top"><h1>Game night</h1><div style="display:flex;gap:8px">${(document.documentElement.requestFullscreen || document.documentElement.webkitRequestFullscreen) ? '<button type="button" data-a="full">Full screen</button>' : ''}<button type="button" data-a="x">Close</button></div></div><div class="body"><p style="color:#93A0B5">Loading your bets…</p></div>`;
  document.body.append(box); document.body.style.overflow = 'hidden';
  box.addEventListener('click', e => { const a = e.target.closest('[data-a]')?.dataset.a;
    if (a === 'x') gnClose(); if (a === 'full') { const f = box.requestFullscreen || box.webkitRequestFullscreen; if (document.fullscreenElement) document.exitFullscreen?.(); else f?.call(box); } });
  gnWake(); gnTick();
}
function gnClose() { GN.open = false; clearTimeout(GN.timer); try { GN.lock?.release(); } catch (_) {} GN.lock = null; if (document.fullscreenElement) document.exitFullscreen?.(); document.getElementById('bdGN')?.remove(); document.body.style.overflow = ''; }
document.addEventListener('visibilitychange', () => { if (GN.open && document.visibilityState === 'visible') { gnWake(); clearTimeout(GN.timer); gnTick(); } });
window.BD_gameNight = gnOpen;
function gnButton() {
  const sec = document.getElementById('tab-score'); const h = sec?.querySelector('.panel .row.spread'); if (!h || document.getElementById('gnBtn')) return;
  const b = document.createElement('button'); b.id = 'gnBtn'; b.type = 'button'; b.className = 'primary'; b.textContent = 'Game night'; b.title = 'Full-screen live view of your open bets'; b.onclick = gnOpen;
  h.append(b);
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', gnButton); else gnButton();
})();
