-- Kev's Bet Desk: database schema for Supabase.
-- Run this once in the Supabase dashboard: SQL Editor -> New query -> paste -> Run.

-- 1) App documents. Mirrors the old artifact database: every document belongs to one user,
--    lives in a collection path ('' for top-level docs like 'settings', 'ledger/bets' for bets) and has an id.
create table if not exists public.docs (
  uid        uuid        not null references auth.users(id) on delete cascade,
  coll       text        not null default '',
  id         text        not null,
  data       jsonb       not null default '{}'::jsonb,
  version    integer     not null default 1,
  updated_at timestamptz not null default now(),
  primary key (uid, coll, id)
);
create index if not exists docs_coll_idx on public.docs (uid, coll);
alter table public.docs replica identity full;

create or replace function public.docs_bump() returns trigger language plpgsql as $$
begin
  if tg_op = 'UPDATE' then new.version := old.version + 1; end if;
  new.updated_at := now();
  return new;
end $$;
drop trigger if exists docs_bump on public.docs;
create trigger docs_bump before update on public.docs for each row execute function public.docs_bump();

alter table public.docs enable row level security;
drop policy if exists "own docs" on public.docs;
create policy "own docs" on public.docs for all to authenticated
  using (uid = auth.uid()) with check (uid = auth.uid());

-- Merge fields into a document (creates it if missing). A field set to {"__delete__": true} is removed.
create or replace function public.doc_merge(p_uid uuid, p_coll text, p_id text, p_patch jsonb) returns jsonb
language plpgsql security invoker as $$
declare cur jsonb; k text; v jsonb;
begin
  select data into cur from public.docs where uid = p_uid and coll = p_coll and id = p_id for update;
  if cur is null then cur := '{}'::jsonb; end if;
  for k, v in select * from jsonb_each(p_patch) loop
    if jsonb_typeof(v) = 'object' and v ? '__delete__' then cur := cur - k; else cur := jsonb_set(cur, array[k], v, true); end if;
  end loop;
  insert into public.docs (uid, coll, id, data) values (p_uid, p_coll, p_id, cur)
    on conflict (uid, coll, id) do update set data = excluded.data;
  return cur;
end $$;

-- 2) Game archive: every final NBA / NFL game's box score, filled by the scheduled jobs from ESPN.
--    The jobs compute team stats, player averages and recent form from it (this replaces StatMuse).
create table if not exists public.games (
  league     text        not null,           -- 'nba' | 'nfl'
  id         text        not null,           -- ESPN event id
  date       date        not null,           -- US Eastern game day
  start_at   timestamptz,
  season     integer,
  season_type integer,                       -- 2 = regular season, 3 = postseason
  week       integer,
  status     text,
  home       text,                           -- team nickname (e.g. 'Pacers')
  away       text,
  home_score integer,
  away_score integer,
  box        jsonb,                          -- compact box score (see jobs/espn.py)
  updated_at timestamptz not null default now(),
  primary key (league, id)
);
create index if not exists games_date_idx on public.games (league, date);

create table if not exists public.player_games (
  league     text        not null,
  game_id    text        not null,
  player_id  text        not null,           -- ESPN athlete id
  name       text        not null,
  name_norm  text        not null,
  team       text,                           -- nickname
  team_ab    text,                           -- StatMuse-style abbreviation (e.g. 'GSW')
  opp        text,
  opp_ab     text,
  date       date        not null,
  season     integer,
  starter    boolean,
  dnp        boolean     not null default false,
  stats      jsonb       not null default '{}'::jsonb,  -- e.g. {"pts":22,"reb":8,"ast":3,"fg3":1,"min":34}
  primary key (league, game_id, player_id)
);
create index if not exists player_games_name_idx on public.player_games (league, name_norm);
create index if not exists player_games_player_idx on public.player_games (league, player_id, date desc);

alter table public.games enable row level security;
alter table public.player_games enable row level security;
drop policy if exists "read games" on public.games;
create policy "read games" on public.games for select to authenticated using (true);
drop policy if exists "read player games" on public.player_games;
create policy "read player games" on public.player_games for select to authenticated using (true);

-- 3) Job runs, shown on the app's "Data & tasks status" card.
create table if not exists public.job_runs (
  id          bigserial primary key,
  job         text        not null,
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  ok          boolean,
  summary     text
);
create index if not exists job_runs_job_idx on public.job_runs (job, started_at desc);
alter table public.job_runs enable row level security;
drop policy if exists "read job runs" on public.job_runs;
create policy "read job runs" on public.job_runs for select to authenticated using (true);

-- 4) Live updates: the app listens for changes to its own documents.
do $$ begin
  alter publication supabase_realtime add table public.docs;
exception when duplicate_object then null; end $$;
