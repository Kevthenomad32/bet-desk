// "Ask StatMuse" for the Bet Desk app: forwards a plain-English stats question to StatMuse's REST API with your key
// (secret STATMUSE_API_KEY) and returns the answer, a small table, and how much of your StatMuse quota is left.
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
};
const LEAGUES = new Set(['nba', 'nfl', 'nhl', 'mlb', 'wnba', 'cfb', 'pga', 'fc']);
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { ...CORS, 'Content-Type': 'application/json' } });

// Only a signed-in Bet Desk user may call this (the public anon key alone is refused).
async function signedIn(req: Request): Promise<boolean> {
  const auth = req.headers.get('Authorization') || '';
  if (!auth.startsWith('Bearer ')) return false;
  const r = await fetch(`${Deno.env.get('SUPABASE_URL')}/auth/v1/user`, { headers: { Authorization: auth, apikey: Deno.env.get('SUPABASE_ANON_KEY') || '' } });
  return r.ok;
}

type Cell = { value?: unknown; display?: string };
type Row = { context?: { type?: string; name?: string; display?: string; value?: unknown }[]; stats?: Record<string, Cell> };

function table(data: { results?: Row[] } | undefined) {
  const rows = (data?.results || []).slice(0, 25);
  if (!rows.length) return null;
  const ctxCols: string[] = [];
  for (const r of rows) for (const c of r.context || []) if (c.type && !ctxCols.includes(c.type)) ctxCols.push(c.type);
  const statCols: string[] = [];
  for (const r of rows) for (const k of Object.keys(r.stats || {})) if (!statCols.includes(k)) statCols.push(k);
  const label = (c: { name?: string; display?: string; value?: unknown }) => String(c.display ?? c.name ?? c.value ?? '');
  return {
    columns: [...ctxCols, ...statCols],
    rows: rows.map(r => [
      ...ctxCols.map(t => { const c = (r.context || []).find(x => x.type === t); return c ? label(c) : ''; }),
      ...statCols.map(k => { const s = (r.stats || {})[k]; return s ? String(s.display ?? s.value ?? '') : ''; }),
    ]),
  };
}

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS });
  if (!(await signedIn(req))) return json({ error: 'sign in first' }, 401);
  const key = Deno.env.get('STATMUSE_API_KEY');
  if (!key) return json({ error: 'StatMuse key not set up yet (STATMUSE_API_KEY).' }, 500);
  let body: { league?: string; q?: string };
  try { body = await req.json(); } catch { return json({ error: 'bad request' }, 400); }
  const league = String(body.league || 'nba').toLowerCase();
  const q = String(body.q || '').trim().slice(0, 200);
  if (!LEAGUES.has(league) || !q) return json({ error: 'Pick a league and type a question.' }, 400);
  const r = await fetch(`https://api.statmuse.com/sports/${league}/v1/ask?input=${encodeURIComponent(q)}`, { headers: { 'x-statmuse-api-key': key } });
  const quota = { limit: r.headers.get('x-quota-limit'), remaining: r.headers.get('x-quota-remaining'), reset: r.headers.get('x-quota-reset') };
  let j: Record<string, any> = {};
  try { j = await r.json(); } catch { /* ignore */ }
  if (!r.ok) {
    const why = r.status === 402 ? 'Your StatMuse quota is used up for this period.' : r.status === 429 ? 'Too many questions at once; try again in a second.'
      : r.status === 401 || r.status === 403 ? 'StatMuse refused the API key.' : (j?.error?.message || j?.message || `StatMuse said ${r.status}`);
    return json({ error: why, quota }, 502);
  }
  return json({ type: j.type, answer: j.answer?.text || (typeof j.answer === 'string' ? j.answer : ''), table: j.type === 'stats' ? table(j.data) : null, quota });
});
