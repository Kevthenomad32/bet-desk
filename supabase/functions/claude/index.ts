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

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS });
  if (!(await signedIn(req))) return json({ error: 'sign in first' }, 401);
  const key = Deno.env.get('ANTHROPIC_API_KEY');
  if (!key) return json({ error: 'ANTHROPIC_API_KEY is not set' }, 500);
  let body: { prompt?: string; images?: { media_type: string; data: string }[] };
  try { body = await req.json(); } catch { return json({ error: 'bad request' }, 400); }
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
