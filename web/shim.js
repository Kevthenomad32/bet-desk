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

// ---------- "Ask StatMuse": plain-English stats questions, answered by StatMuse (your API key, via the statmuse function) ----------
const esc = v => String(v ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
async function askStatMuse(league, q) {
  const r = await fetch(FN('statmuse'), { method: 'POST', headers: { Authorization: `Bearer ${await token()}`, apikey: C.supabaseAnonKey, 'Content-Type': 'application/json' },
    body: JSON.stringify({ league, q }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(j.error || `StatMuse unavailable (${r.status})`), { quota: j.quota });
  return j;
}
window.BD_askStatMuse = askStatMuse;
function addAskPanel(tabId, league) {
  const tab = document.getElementById(tabId);
  if (!tab || tab.querySelector('.bd-ask')) return;
  const p = document.createElement('div');
  p.className = 'panel bd-ask';
  p.innerHTML = `<div class="row spread"><h3>Ask StatMuse</h3><span class="small muted bd-ask-q"></span></div>
    <form class="row" style="gap:8px;flex-wrap:wrap">
      <select class="bd-ask-lg" aria-label="League" style="flex:0 0 auto;width:auto">${['nba', 'nfl', 'cfb', 'nhl', 'mlb', 'wnba'].map(l => `<option value="${l}"${l === league ? ' selected' : ''}>${l.toUpperCase()}</option>`).join('')}</select>
      <input class="bd-ask-in" type="text" maxlength="200" placeholder="${league === 'nfl' ? 'e.g. most rushing yards allowed to RBs last 4 weeks' : 'e.g. Brunson points last 5 games'}" style="flex:1 1 200px;min-width:0">
      <button type="submit" class="primary small">Ask</button>
    </form>
    <div class="bd-ask-out small" aria-live="polite"></div>`;
  tab.prepend(p);
  const out = p.querySelector('.bd-ask-out'), qinfo = p.querySelector('.bd-ask-q');
  const showQuota = qt => { if (qt && qt.limit && qt.remaining != null) qinfo.textContent = `${Number(qt.remaining).toLocaleString()} of ${Number(qt.limit).toLocaleString()} StatMuse requests left`; };
  p.querySelector('form').addEventListener('submit', async e => {
    e.preventDefault();
    const q = p.querySelector('.bd-ask-in').value.trim(); if (!q) return;
    const btn = p.querySelector('button'); btn.disabled = true; out.textContent = 'Asking StatMuse…';
    try {
      const a = await askStatMuse(p.querySelector('.bd-ask-lg').value, q);
      let html = `<p style="margin:0 0 8px;font-size:15px">${esc(a.answer || 'StatMuse had no answer for that.')}</p>`;
      if (a.table && a.table.rows.length) {
        html += `<div class="tablewrap"><table><thead><tr>${a.table.columns.map(c => `<th>${esc(c.replace(/([a-z])([A-Z])/g, '$1 $2'))}</th>`).join('')}</tr></thead><tbody>${
          a.table.rows.map(r => `<tr>${r.map(v => `<td>${esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
      }
      out.innerHTML = html; showQuota(a.quota);
    } catch (err) { out.textContent = err.message; showQuota(err.quota); }
    btn.disabled = false;
  });
}
function addAskPanels() { addAskPanel('tab-nba', 'nba'); addAskPanel('tab-nfl', 'nfl'); }
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', addAskPanels); else addAskPanels();

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
      keys.forEach((k, i) => { const col = map[k]; if (!col) return; const v = String(a.stats[i] ?? '').split('-')[0]; row[col] = v === '' || v === '--' ? null : Number(v); });
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
})();
