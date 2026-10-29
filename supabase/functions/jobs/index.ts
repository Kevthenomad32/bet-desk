// Starts a Bet Desk job now (the app's "Refresh all data" button): triggers the GitHub Actions workflow with that job.
// Secrets: GH_TOKEN (fine-grained token with Actions: read and write on the repo), GH_REPO ("you/bet-desk").
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
};
const JOBS = new Set(['pregame', 'nbagl', 'nba_daily', 'nfl_weekly', 'injuries', 'settle', 'closing', 'recap', 'live', 'cfb_lines']);
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { ...CORS, 'Content-Type': 'application/json' } });

// Only a signed-in Bet Desk user may call this (the public anon key alone is refused).
async function signedIn(req: Request): Promise<boolean> {
  const auth = req.headers.get('Authorization') || '';
  if (!auth.startsWith('Bearer ')) return false;
  const r = await fetch(`${Deno.env.get('SUPABASE_URL')}/auth/v1/user`, { headers: { Authorization: auth, apikey: Deno.env.get('SUPABASE_ANON_KEY') || '' } });
  return r.ok;
}

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS });
  if (!(await signedIn(req))) return json({ error: 'sign in first' }, 401);
  let job = '';
  try { job = String((await req.json()).job || ''); } catch { /* ignore */ }
  if (!JOBS.has(job)) return json({ error: 'unknown job' }, 400);
  const token = Deno.env.get('GH_TOKEN'), repo = Deno.env.get('GH_REPO');
  if (!token || !repo) return json({ error: 'GH_TOKEN / GH_REPO not set' }, 500);
  const r = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/jobs.yml/dispatches`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json', 'Content-Type': 'application/json', 'User-Agent': 'bet-desk' },
    body: JSON.stringify({ ref: 'main', inputs: { job } }),
  });
  if (r.status !== 204) return json({ error: `GitHub said ${r.status}` }, 502);
  return json({ ok: true, job });
});
