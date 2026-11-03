// Claude for the Bet Desk app (your Anthropic API key, stored as the ANTHROPIC_API_KEY secret):
// reads bet-slip screenshots and checks how parlay legs relate. Returns {result: <parsed JSON>}.
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { ...CORS, 'Content-Type': 'application/json' } });

// Only a signed-in Bet Desk user may call this (the public anon key alone is refused).
async function signedIn(req: Request): Promise<boolean> {
  const auth = req.headers.get('Authorization') || '';
  if (!auth.startsWith('Bearer ')) return false;
  const r = await fetch(`${Deno.env.get('SUPABASE_URL')}/auth/v1/user`, { headers: { Authorization: auth, apikey: Deno.env.get('SUPABASE_ANON_KEY') || '' } });
  return r.ok;
}

// ---------- "Ask Claude" chat: answers with the user's own Bet Desk data as context (read with their sign-in, so only their rows) ----------
const etDay = (offsetDays = 0) => new Date(Date.now() - 5 * 3600e3 - offsetDays * 864e5).toISOString().slice(0, 10);
// deno-lint-ignore no-explicit-any
type Doc = { id: string; data: any };
async function docs(req: Request, coll: string, ids?: string[], since?: string): Promise<Doc[]> {
  const u = new URL(`${Deno.env.get('SUPABASE_URL')}/rest/v1/docs`);
  u.searchParams.set('select', 'id,data'); u.searchParams.set('coll', `eq.${coll}`);
  if (ids) u.searchParams.set('id', `in.(${ids.join(',')})`);
  if (since) u.searchParams.set('data->>date', `gte.${since}`);
  const r = await fetch(u.toString(), { headers: { Authorization: req.headers.get('Authorization') || '', apikey: Deno.env.get('SUPABASE_ANON_KEY') || '' } });
  return r.ok ? await r.json() : [];
}
// deno-lint-ignore no-explicit-any
const am = (v: any) => v == null ? '' : (Number(v) > 0 ? '+' + Number(v) : String(v));
async function context(req: Request): Promise<string> {
  const [top, bets, shadow, cbb] = await Promise.all([
    docs(req, '', ['settings', 'nbaOdds', 'nflOdds', 'nbaInjuries', 'nflInjuries', 'calibration', 'cbbBoard']),
    docs(req, 'ledger/bets', undefined, etDay(21)),
    docs(req, 'ledger/shadow', undefined, etDay(3)),
    docs(req, 'ledger/cbbgl', undefined, etDay(14))]);
  const D = Object.fromEntries(top.map((d) => [d.id, d.data]));
  const out: string[] = [`Today (Eastern): ${etDay()}.`];
  const st = D.settings || {};
  out.push(`Kev's settings: unit $${st.unit ?? '?'}, bankroll $${st.bankroll ?? '?'}.`);
  const B = bets.map((b) => b.data).sort((a, b) => String(b.date).localeCompare(String(a.date)));
  out.push(`\nKev's bets, last 21 days (${B.length}): date | book | type | label | stake | odds | boost% | status`);
  for (const b of B.slice(0, 80)) out.push(`${b.date} | ${b.book} | ${b.type} | ${b.label} | $${b.stake}${b.bonus ? ' (bonus bet)' : ''} | ${am(b.odds)} | ${b.boost || 0} | ${b.status}`);
  for (const lg of ['nba', 'nfl']) {
    const o = D[lg + 'Odds']; if (!o) continue;
    out.push(`\n${lg.toUpperCase()} lines (${o.source || ''}, updated ${o.updated || '?'}):`);
    for (const g of o.games || []) out.push(`${g.away} @ ${g.home} ${g.start || ''} | home spread ${g.spread?.home ?? '—'} (Pinnacle ${g.spread?.pinHome ?? '—'}) | total ${g.total?.line ?? '—'} | ML away/home ${g.ml?.away ?? ''}/${g.ml?.home ?? ''}`);
    out.push(`${lg.toUpperCase()} props (player | team | market | line | Hard Rock over/under | Pinnacle over/under at same line | season avg):`);
    for (const p of (o.props || []).slice(0, lg === 'nba' ? 450 : 250)) out.push(`${p.player} | ${p.team} | ${p.market} | ${p.line} | ${p.odds}/${p.uodds} | ${p.pinO ?? '—'}/${p.pinU ?? '—'} | ${p.avg ?? '—'}`);
  }
  const sh = shadow.map((d) => d.data);
  if (sh.length) {
    out.push(`\nThe app model's priced props, last 3 days (date league game | player | market | line | model P(over) | book P(over) | actual result):`);
    for (const d of sh) for (const r of d.rows || []) out.push(`${d.date} ${d.league} ${d.game} | ${r.player} | ${r.market} | ${r.line} | ${typeof r.pModel === 'number' ? r.pModel.toFixed(3) : '—'} | ${typeof r.pBook === 'number' ? r.pBook.toFixed(3) : '—'} | ${r.result ?? 'pending'}`);
  }
  for (const k of ['nbaInjuries', 'nflInjuries']) {
    const inj = D[k]; if (!inj) continue;
    // deno-lint-ignore no-explicit-any
    const rows = (inj.players || inj.rows || []).slice(0, 150).map((x: any) => `${x.name} (${x.team}) ${x.status}${x.desc ? ' – ' + x.desc : ''}`);
    if (rows.length) out.push(`\n${k} (${inj.source || ''} ${inj.updated || ''}): ${rows.join('; ')}`);
  }
  if (D.cbbBoard) out.push(`\nCollege basketball board: ${JSON.stringify(D.cbbBoard).slice(0, 6000)}`);
  if (cbb.length) out.push(`College basketball locked calls: ${JSON.stringify(cbb.map((d) => d.data)).slice(0, 4000)}`);
  if (D.calibration) out.push(`\nModel calibration by market (overRate = how often overs actually hit, predOver = model's average P(over)): ${JSON.stringify(D.calibration.markets)}`);
  return out.join('\n').slice(0, 150000);
}
const SYSTEM = `You are Claude, built into "Kev's Bet Desk", Kev's personal sports-betting app (NBA and NFL player props, college hoops, parlays; he bets at Hard Rock Bet).
Answer his questions using the app data below: his logged bets, today's Hard Rock lines with Pinnacle's prices (Pinnacle is the sharp reference: compare after removing the vig), the app model's win chances, injuries and results.
Be direct and brief: a few sentences or a short table, no long preambles. Use American odds; units are his $ unit from settings.
Be honest about edges: the model has been close to a coin flip, overs have hit less often than unders, and a bet is only good value when its win chance beats the break-even of its price. Don't hype, never promise wins, and if the data doesn't cover something, say so.
You can search the web for late injury or lineup news when it matters. You can't change anything in the app: to log a bet he uses "Add bet".`;
async function chat(req: Request, key: string, msgs: { role: string; content: string }[]) {
  const messages = msgs.filter((m) => (m.role === 'user' || m.role === 'assistant') && m.content).slice(-16)
    .map((m) => ({ role: m.role, content: String(m.content).slice(0, 8000) }));
  if (!messages.length || messages[messages.length - 1].role !== 'user') return json({ error: 'missing question' }, 400);
  const ctx = await context(req);
  const ask = (search: boolean) => fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: { 'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json' },
    body: JSON.stringify({
      model: Deno.env.get('CLAUDE_MODEL') || 'claude-sonnet-5-5', max_tokens: 1500,
      system: [{ type: 'text', text: SYSTEM }, { type: 'text', text: 'APP DATA\n' + ctx, cache_control: { type: 'ephemeral' } }],
      ...(search ? { tools: [{ type: 'web_search_20250305', name: 'web_search', max_uses: 3 }] } : {}), messages,
    }),
  });
  let r = await ask(true);
  if (!r.ok && r.status !== 429 && r.status !== 401) r = await ask(false);  // web search not enabled on the key's org: answer without it
  const j = await r.json();
  if (!r.ok) return json({ error: j?.error?.message || 'Claude error' }, 502);
  const reply = (j.content || []).filter((c: { type: string }) => c.type === 'text').map((c: { text: string }) => c.text).join('').trim();
  return json({ reply: reply || 'No answer came back. Try asking again.' });
}

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS });
  if (!(await signedIn(req))) return json({ error: 'sign in first' }, 401);
  const key = Deno.env.get('ANTHROPIC_API_KEY');
  if (!key) return json({ error: 'ANTHROPIC_API_KEY is not set' }, 500);
  let body: { prompt?: string; images?: { media_type: string; data: string }[]; mode?: string; messages?: { role: string; content: string }[] };
  try { body = await req.json(); } catch { return json({ error: 'bad request' }, 400); }
  if (body.mode === 'chat') return chat(req, key, body.messages || []);
  const prompt = String(body.prompt || '').slice(0, 20000);
  const images = (body.images || []).slice(0, 4);
  if (!prompt) return json({ error: 'missing prompt' }, 400);
  const content = [
    ...images.map((im) => ({ type: 'image', source: { type: 'base64', media_type: im.media_type || 'image/png', data: im.data } })),
    { type: 'text', text: prompt + '\n\nReply with only the JSON object, no other text.' },
  ];
  const r = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: { 'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json' },
    body: JSON.stringify({ model: Deno.env.get('CLAUDE_MODEL') || 'claude-sonnet-5-5', max_tokens: 1500, messages: [{ role: 'user', content }] }),
  });
  const j = await r.json();
  if (!r.ok) return json({ error: j?.error?.message || 'Claude error' }, 502);
  const text = (j.content || []).filter((c: { type: string }) => c.type === 'text').map((c: { text: string }) => c.text).join('');
  const m = text.match(/\{[\s\S]*\}/);
  try { return json({ result: JSON.parse(m ? m[0] : text) }); } catch { return json({ error: 'Claude did not return JSON' }, 502); }
});
