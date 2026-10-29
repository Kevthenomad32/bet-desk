// Pass-through to ESPN's public site API for the Bet Desk app (scores, box scores, injuries).
// Only scoreboard / summary / injuries / teams paths are allowed. Requires a signed-in user (Supabase checks the JWT).
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'GET, OPTIONS',
};
const ALLOW = /^(basketball|football|hockey|baseball)\/[a-z-]+\/(scoreboard|summary|injuries|teams)(\?.*)?$/;

// Only a signed-in Bet Desk user may call this (the public anon key alone is refused).
async function signedIn(req: Request): Promise<boolean> {
  const auth = req.headers.get('Authorization') || '';
  if (!auth.startsWith('Bearer ')) return false;
  const r = await fetch(`${Deno.env.get('SUPABASE_URL')}/auth/v1/user`, { headers: { Authorization: auth, apikey: Deno.env.get('SUPABASE_ANON_KEY') || '' } });
  return r.ok;
}

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS });
  if (!(await signedIn(req))) return new Response(JSON.stringify({ error: 'sign in first' }), { status: 401, headers: { ...CORS, 'Content-Type': 'application/json' } });
  const path = new URL(req.url).searchParams.get('path') || '';
  if (!ALLOW.test(path)) return new Response(JSON.stringify({ error: 'path not allowed' }), { status: 400, headers: { ...CORS, 'Content-Type': 'application/json' } });
  const r = await fetch('https://site.api.espn.com/apis/site/v2/sports/' + path, { headers: { 'User-Agent': 'Mozilla/5.0 (BetDesk)' } });
  return new Response(await r.text(), { status: r.status, headers: { ...CORS, 'Content-Type': 'application/json', 'Cache-Control': 'public, max-age=15' } });
});
