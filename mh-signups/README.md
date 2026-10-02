# MH Signups

A small, phone-first web app for Mike Hostilo to check the firm's Lead Docket signups. Mike saves the site to his Home Screen, taps the icon, and sees three numbers:

1. **SIGNED TODAY** (large)
2. **SIGNED THIS WEEK**
3. **SIGNED THIS MONTH**

That is the whole product. No charts, menus, market breakdowns, goals, or record lists.

**Status: demo build.** It runs today on clearly labeled synthetic data (the screen says **DEMO DATA**). The Lead Docket connection is a separate server-side module for Rex to finish. See [REX_HANDOFF.md](./REX_HANDOFF.md).

## Run the demo

Requires Node 22.12 or newer.

```bash
cd mh-signups
npm ci
npm run dev
```

Open http://localhost:5173 (the terminal also prints a Network URL if you want to look at it on a phone on the same Wi-Fi). With no settings, it runs in demo mode: synthetic numbers, **DEMO DATA** pill, no login.

To see the sign-in flow locally, copy `.env.example` to `.env`, then fill in `DASHBOARD_PASSPHRASE_HASH` and `SESSION_SECRET` (see [Credentials](#credentials)). Wrap the hash in single quotes in `.env` because it contains `$`.

The local server runs the same handlers as the Netlify Functions, with an in-memory snapshot store. To run on the real Netlify runtime locally (Blobs sandbox, function routing), use `npm run dev:netlify`.

## Commands

| Command | What it does |
| --- | --- |
| `npm run dev` | Vite dev server with the API handlers mounted. Demo mode by default. |
| `npm run dev:netlify` | Netlify's local dev runtime (pinned `netlify-cli`, fetched on first use). |
| `npm run build` | Production build into `dist/`. |
| `npm run preview` | Serves `dist/` (static only; no API). |
| `npm run typecheck` | TypeScript checks for browser code and server code. |
| `npm test` | Unit tests (Vitest). Includes a real production build scanned for leaked secrets. |
| `npm run test:e2e` | Browser tests in Chromium at phone sizes (see below). |
| `npm run check` | typecheck, tests, and build. |
| `npm run hash-passphrase` | Prompts for a passphrase and prints the bcrypt hash. |
| `npm run gen-session-secret` | Prints a random value for `SESSION_SECRET`. |
| `npm run gen-icons` | Regenerates the placeholder "MH" icons (needs a Chromium; set `CHROMIUM_PATH`). |

`npm run test:e2e` needs a Chromium. It uses `CHROMIUM_PATH` if set, otherwise `/opt/pw-browsers/chromium`, and skips itself if neither exists. Screenshots go to `test-results/` (or `E2E_SHOTS_DIR`).

## How it works

```text
Lead Docket
    -> scheduled Netlify function, every 30 minutes (netlify/functions/refresh.mts)
    -> private aggregate snapshot in Netlify Blobs (three totals + metadata, nothing else)
    -> authenticated GET /api/stats (netlify/functions/stats.mts)
    -> Mike's phone
```

- **The phone never talks to Lead Docket.** It reads the saved snapshot on launch, when the app comes back to the foreground, when "Refresh view" is tapped, and about once a minute while the screen is visible. "Refresh view" does not force a sync.
- **One sync, one cutoff.** Each sync recomputes all three totals from source data using a single cutoff (`asOf`) and half-open intervals `[periodStart, asOf)`. Totals are written together, only after a complete and validated retrieval. A failed sync (401, 429, timeout, bad response, incomplete paging) never overwrites the last good numbers with zero or partial counts, and an older overlapping run cannot replace a newer snapshot.
- **Honest timestamps.** "Updated ..." is the time the last complete sync finished, in Eastern time. Reading the snapshot never makes old data look new. The screen says **Update delayed** if a sync failure is known or the data cutoff is over 40 minutes old.
- **Rollover safety.** Each saved total carries the period it was calculated for. At midnight, Monday, or the 1st, any total whose period has ended shows `--` and "Waiting for update" instead of last period's number. Other totals stay. Note that a week can start in the previous month, so the week can legitimately exceed the month early in a month.
- **Zero versus unavailable.** `0` appears only when a complete query established zero. Otherwise `--` with a reason: "Waiting for first update", "Connection not configured", "Waiting for update", or an offline note.

### Proposed reporting rules (Rex to confirm)

These are defaults, not verified facts about the firm's Lead Docket setup. Details and open questions are in REX_HANDOFF.md.

- Scope: firmwide, all markets and offices.
- Time zone: `America/New_York` for every calendar boundary, regardless of phone or server zone. Daylight saving is handled by the time zone database, never a fixed offset.
- Today: local midnight to the cutoff. Week: Monday (configurable to Sunday) at local midnight to the cutoff. Month: first of the month at local midnight to the cutoff (not a rolling 30 days).
- Signed: gross new signups by the actual signup event date, one count per distinct lead/case id. A later close, transfer, or loss does not erase a genuine earlier signup.

### Stats response

`GET /api/stats` returns this application-owned contract (validated by `shared/stats.ts` on both ends). It is not Lead Docket's API.

| Field | Meaning |
| --- | --- |
| `today`, `week`, `month` | Distinct signups in `[periodStarts.x, asOf)`. `null` means unavailable, never zero. |
| `timeZone`, `weekStartsOn` | The calendar the numbers were calculated with. |
| `periodStarts` | `{today, week, month}` period start instants (UTC, ISO with `Z`) the saved numbers belong to. `null` before the first sync. |
| `asOf` | The common data cutoff for all three totals. Not the time the phone asked. |
| `lastSuccessfulSyncAt` | When the last complete, validated sync finished. |
| `status` | `ok`, `delayed` (counts present but stale or a sync failed), or `unavailable` (no counts). |
| `source` | `demo` or `leaddocket`. |
| `unavailableReason` | `not_configured` or `waiting_first_update` when no snapshot exists, else `null`. |

No individual intake records, names, or lead ids are ever returned.

## Security model

- **Authentication.** One passphrase, verified on the server against a salted bcrypt hash. A successful login sets a signed, expiring session cookie: `HttpOnly`, `Secure`, `SameSite=Strict`, host-pinned (`__Host-` prefix on HTTPS). Default life 30 days (`SESSION_DAYS`). Nothing is stored in local storage, and no token appears in a URL.
- **Revocation.** Rotate `SESSION_SECRET` and redeploy. Every outstanding session stops working.
- **Throttling.** Five failed logins from one client lock that client out for 15 minutes. The counters live in Netlify Blobs, so they hold across serverless instances.
- **CSRF.** Login and logout require JSON, POST, and a matching `Origin` header. Cookies are `SameSite=Strict`.
- **Fail closed.** In live mode, missing, partial, or malformed auth settings return 503 with no counts. An unrecognized `DATA_MODE` also fails closed. Live mode never falls back to demo data.
- **Every route checks.** The checks live inside the handlers, so `/api/stats` and `/.netlify/functions/stats` behave identically.
- **Caching and indexing.** Auth and stats responses are `Cache-Control: private, no-store`. The snapshot is never a static file or baked into HTML. `noindex` is set as housekeeping only.
- **Secrets.** Lead Docket credentials, the passphrase hash, and the session secret exist only as Netlify runtime environment variables. A test builds the site with sentinel secrets set and fails if any appear in the output.
- **Read-only.** The app never writes to Lead Docket.
- **Demo without auth.** If `DATA_MODE` is demo (or unset) and no auth settings exist, `/api/stats` serves synthetic data without a login so the display can be reviewed. The moment either auth setting is present, login is required.

## Deploying on Netlify

Nothing has been deployed. See REX_HANDOFF.md for the authorized sequence. Settings:

| Setting | Value |
| --- | --- |
| Base directory | `mh-signups` if this folder stays inside the existing `blackbar-dashboard` repo; empty if it becomes the root of its own repo |
| Build command | `npm run build` |
| Publish directory | `dist` |
| Functions directory | `netlify/functions` |
| Node | 22 (`NODE_VERSION` is set in `netlify.toml`) |

`netlify.toml` also sets security headers, a strict Content Security Policy, and `noindex`. It contains no secrets.

## Credentials

```bash
npm run hash-passphrase      # prompts twice, hidden; prints the value for DASHBOARD_PASSPHRASE_HASH
npm run gen-session-secret   # prints a value for SESSION_SECRET
```

Pick a passphrase of at least 12 characters (max 72 bytes, a bcrypt limit). Nothing is written to disk or sent anywhere. Paste the outputs into Netlify's environment variables UI. Never commit them. Variable names and meanings are in `.env.example`.

## Putting it on a phone

**iPhone (Safari):** open the production URL in Safari, tap Share, choose **Add to Home Screen**, then **Add**. If the sheet offers "Open as Web App", leave it on. Launch from the new icon.

**Android (Chrome):** open the URL in Chrome, tap the three-dot menu, choose **Add to Home screen** (or **Install app**), then confirm.

Browser or device policies can still require a fresh sign-in, so test the Home Screen icon separately from a browser tab. Menu wording changes with OS versions.

## Branding

Everything brand-related is in two places, so it is easy to replace:

- `src/branding.ts`: app name and the small firm line above it.
- `public/`: `favicon.svg`, `manifest.webmanifest` (names, colors), and `public/icons/` (`icon-192.png`, `icon-512.png`, `icon-maskable-512.png`, `apple-touch-icon.png`, `favicon-32.png`). Keep the file names and sizes. The maskable icon needs its artwork inside the central safe area.

The "MH" icon is a placeholder. It is not an official logo.

## Project layout

```text
index.html, public/, src/        Phone UI (Vite + TypeScript, no framework)
shared/                          Stats contract, calendar math, status rules (used by server and phone)
server/                          Server-only logic
  lead-docket.ts                 REX: the Lead Docket adapter (stub)
  adapter.ts                     The adapter contract and error codes
  demo-source.ts                 Synthetic data for the demo
  refresh.ts                     Sync with retries, deadline, last-good protection
  stats-service.ts               Builds the response from the saved snapshot
  auth.ts, handlers.ts, http.ts  Sessions, login throttling, request handling
netlify/functions/               stats, login, logout, refresh (scheduled)
dev/vite-dev-api.ts              Local-only API bridge (not in the production build)
tests/                           Unit tests; tests/e2e has the browser tests
```

## What is tested

`npm test` (132 tests) and `npm run test:e2e` (11 browser tests) cover: three-metric layout with no scrolling from 320x568 to 430x932 and with enlarged text; zero versus unavailable; DEMO DATA labeling; complete pagination and failure mid-pagination; duplicate and repeated signing events; signups kept after later status changes; UTC/Eastern conversion; spring and fall daylight saving days; Sunday/Monday; midnight, month, and year rollover; a week starting in the previous month; timeout, 429, 401, malformed and incomplete responses; last-good retention and honest timestamps; rollover during a failed sync; app resume, debounce, and pause when hidden; offline behavior; session expiry, tampering, and secret rotation; login throttling across instances; direct function route protection; secret absence from the build; demo and production snapshot isolation.

**Not covered by automation:** anything that needs the real Lead Docket tenant, the deployed Netlify environment, or a physical iPhone and Android phone. Those are on Rex's verification list.
