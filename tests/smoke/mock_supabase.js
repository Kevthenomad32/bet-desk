// In-memory stand-in for supabase-js, for the browser smoke test. Seed: window.__SEED = [{coll, id, data}], window.__PLAYER_GAMES = [...]
(() => {
  const uid = 'test-user';
  const tables = { docs: (window.__SEED || []).map(d => ({ uid, coll: d.coll, id: d.id, data: d.data })), player_games: window.__PLAYER_GAMES || [], job_runs: [] };
  window.__WRITES = [];
  class Q {
    constructor(t) { this.t = t; this.f = []; this.op = 'select'; this.lim = null; this.one = false; this.rng = null; this.ord = null; }
    select() { return this; }
    eq(c, v) { this.f.push(r => r[c] === v); return this; }
    ilike(c, p) { const re = new RegExp('^' + p.replace(/%/g, '.*') + '$', 'i'); this.f.push(r => re.test(r[c] || '')); return this; }
    order(c, o) { this.ord = [c, o?.ascending !== false]; return this; }
    limit(n) { this.lim = n; return this; }
    range(a, b) { this.rng = [a, b]; return this; }
    maybeSingle() { this.one = true; return this; }
    upsert(rows) { this.op = 'upsert'; this.rows = Array.isArray(rows) ? rows : [rows]; return this; }
    delete() { this.op = 'delete'; return this; }
    then(res, rej) { try { res(this.run()); } catch (e) { rej(e); } }
    run() {
      const T = tables[this.t] || (tables[this.t] = []);
      if (this.op === 'upsert') {
        for (const r of this.rows) { const i = T.findIndex(x => x.uid === r.uid && x.coll === r.coll && x.id === r.id); if (i >= 0) T[i] = { ...T[i], ...r }; else T.push(r); window.__WRITES.push(r); }
        return { data: null, error: null };
      }
      let rows = T.filter(r => this.f.every(f => f(r)));
      if (this.op === 'delete') { tables[this.t] = T.filter(r => !rows.includes(r)); return { data: null, error: null }; }
      if (this.ord) { const [c, asc] = this.ord; rows = [...rows].sort((a, b) => (a[c] > b[c] ? 1 : -1) * (asc ? 1 : -1)); }
      if (this.rng) rows = rows.slice(this.rng[0], this.rng[1] + 1);
      if (this.lim != null) rows = rows.slice(0, this.lim);
      if (this.one) return { data: rows[0] || null, error: null };
      return { data: rows, error: null };
    }
  }
  const session = { access_token: 't', user: { id: uid } };
  window.supabase = { createClient: () => ({
    auth: { getSession: async () => ({ data: { session } }), signInWithPassword: async () => ({ data: { session } }), signOut: async () => ({}) },
    from: t => new Q(t),
    channel: () => { const ch = { on: () => ch, subscribe: () => ch }; return ch; } }) };
})();
