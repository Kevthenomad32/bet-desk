# Kev's Bet Desk — standalone

Your Bet Desk running on your own accounts instead of inside Claude. Same page, same model (v6), same tracker. It works on your Mac and iPad, and the hourly jobs run in the cloud.

| Piece | Runs on | Cost |
|---|---|---|
| The app (web page) | GitHub Pages | free |
| Your data (bets, picks, model logs) + sign-in | Supabase | free tier |
| Hourly jobs (lines, model, settling, recap) | GitHub Actions | free (public repo) |
| Slip reading, settling odd parlays | Claude API (your Anthropic key) | pay per use, a few $/month |
| Odds | SportsGameOdds (your key) | free tier, 2,500 events/month |
| Scores, box scores, injuries | ESPN's public feeds | free |
| Phone alerts (optional) | ntfy app | free |

Setup takes about 20 minutes. Do the steps in order.

---

## 1. Put the code on GitHub

Create an empty **public** repository named `bet-desk` (no README). Public keeps GitHub Pages and Actions minutes free; nothing private is in the code. Never add your backup file (`bet-desk-backup.json`) to it. Then push this folder to it (or have Claude push it for you).

## 2. Create a Supabase project

Sign up at supabase.com → **New project** (name `bet-desk`, region East US, any strong database password; you won't need it again).
Then make an access token: your avatar → **Account preferences → Access Tokens → Generate new token**. Copy it.

## 3. Add the keys to GitHub (the only copying you do)

Repo **Settings → Secrets and variables → Actions → New repository secret**, one per row:

| Secret | Value |
|---|---|
| `SUPABASE_ACCESS_TOKEN` | the Supabase access token from step 2 |
| `APP_EMAIL` | the email you'll sign in to the app with |
| `APP_PASSWORD` | the password you'll sign in to the app with |
| `SGO_API_KEY` | your SportsGameOdds key |
| `ANTHROPIC_API_KEY` | your key from console.anthropic.com (slip reading, settling odd parlays) |
| `GH_TOKEN` | optional, for the app's Refresh button: github.com → Settings → Developer settings → Fine-grained tokens → only the `bet-desk` repo, **Actions: Read and write** |
| `NTFY_TOPIC` | optional phone alerts: a long random name like `kev-betdesk-7f3k29`; subscribe to it in the **ntfy** app |
| `SUPABASE_PROJECT_REF` | only if your Supabase account has more than one project (the id in the project's URL) |

The project URL and Supabase keys are looked up automatically from the access token; you never copy them.

Optional **Variables** (same page, Variables tab): `CLAUDE_MODEL` (defaults to `claude-sonnet-5-5`), `BETDESK_PICKS_N` (`0` = best-bet locking paused, like now; `3` turns the top 3 back on).

## 4. Turn on Pages, then run setup

1. Repo **Settings → Pages → Build and deployment → Source: GitHub Actions**.
2. **Actions → Set up Supabase → Run workflow.** In about two minutes it creates the database, your app login (with public sign-ups turned off), the three server functions and their keys, then publishes the app and starts loading this season's games (10–15 minutes).

Your app is at `https://your-github-username.github.io/bet-desk/`. If you add or change a key later, just run **Set up Supabase** again; it's safe to repeat.

## 5. Automatic runs

The **Bet Desk jobs** workflow runs by itself every hour at :31 and does what's due on the Eastern clock:

| When (ET) | Job |
|---|---|
| Every hour 8am–10pm | Pre-game: refresh lines + injuries for games in the next hour, log every prop the model prices |
| Every hour 10am–11pm | NBA spreads & totals locks |
| 9:31am daily | Injuries, NBA team stats / players / slate / lines |
| 9:31am Wed | NFL defense, schedule, players, lines (Thu/Sun/Mon: lines refresh) |
| 9:31am Fri, Sat | College football lines |
| 12:31am and 2:31am | Settle your bets, grade picks and logged props |
| 4:31am | Closing lines (CLV) + SportsGameOdds usage |
| 9:31am Tue | Weekly recap |
| Sundays hourly 11am–11pm | Live progress on open bets, "one leg away" alert |

You can run any job by hand: **Actions → Bet Desk jobs → Run workflow** (`backfill` reloads the season).

## 6. Bring your history over

1. Open `https://your-github-username.github.io/bet-desk/import.html`, sign in, pick `bet-desk-backup.json`, tap **Import**.
2. Open the app. Your bets, record, profit chart, the model's logged props (60%+ Tracker), NBA game-line calls and model weights are all there.

## Your data

Tracker → **Data & tasks status** → **Download my data** saves everything (bets, picks, the model's graded props, settings, weights) as one file. It's your backup (the import page reads it) and what to send Claude when you want the model checked or refit.

## 7. iPad / iPhone

Open the app in Safari → **Share → Add to Home Screen**. It opens full screen like an app and stays signed in.

---

## Running both versions for now

- The two versions don't share data. The import copies everything up to the moment of the export; bets you log afterwards only exist in the app you logged them in. Log new bets in one place.
- **SportsGameOdds usage doubles while both run their jobs** (the free tier is 2,500 events a month). Keep the overlap short, or pause the Claude scheduled tasks that pull odds. The status card shows usage.
- When you're ready to switch: ask Claude for a fresh export of Bet Desk, import it here again (same documents are replaced, nothing duplicates), then turn off the Claude scheduled tasks.

## What's different from the Claude version

- **Lines and prices** are SportsGameOdds consensus, not Hard Rock (FL) specifically, and there's no Pinnacle anchor or "best price elsewhere" (those came from Optimal).
- **College football** shows lines only. Its model ran on Optimal's projections, so there's no CFB model record here.
- **Stats come from ESPN box scores** the app archives itself, instead of StatMuse. Team ratings and pace are computed from those box scores, so numbers can differ slightly from StatMuse's.
- **Injury reports** come from ESPN (in practice more up to date than StatMuse). Your same-day overrides still work the same way.
- A player who hasn't played this season isn't in the injured-teammate pool (StatMuse let the old task borrow last season's numbers).
- **Game logs** in the player popup are this season only, and team ATS records aren't shown on game cards.
- The **weekly recap** is written from a template instead of by Claude.
- GitHub can start scheduled runs a few minutes late. GitHub also pauses scheduled workflows in a repo with no activity for 60 days; it emails you, and one click on **Enable workflow** turns it back on.

## Updating the app later

The page is built from the Claude version by `tools/build_page.py`:

```
python3 tools/build_page.py tools/source_artifact.html web/index.html
```

If the Claude Bet Desk page changes and you want those changes here, save its HTML over `tools/source_artifact.html` and rerun. If a patch no longer fits, the script stops and names it.

## Files

- `web/` — the app (`index.html` is the Bet Desk page; `shim.js` connects it to Supabase, ESPN and Claude; `import.html` restores a backup)
- `supabase/schema.sql` — database tables and security rules (applied by the Set up Supabase workflow)
- `tools/sb_env.sh` — looks up the project URL and keys from the access token
- `supabase/functions/` — `espn` (scores/box scores), `claude` (slip reading), `jobs` (Refresh button)
- `jobs/` — the scheduled jobs (`run.py` decides what's due; `model/` holds the v6 picks model, the NBA game-line model and the closing-line script, unchanged)
- `tests/` — `pytest tests/` for the jobs; `tests/smoke/run_smoke.py` loads the page in a headless browser against a fake database
