# Rex handoff: finishing MH Signups

Rex, the app, the demo, authentication, the scheduled sync, snapshot storage, and the tests are built. What is left is the Lead Docket connection, confirming the reporting definitions, and the authorized deployment. This is the exact list.

Nothing has been deployed, no remote repository was created for this app, and nothing has touched Lead Docket. No credentials were needed or requested to build it.

## 1. What you own

| # | Task | Where |
| --- | --- | --- |
| 1 | Verify the tenant's API and implement the adapter | `server/lead-docket.ts` |
| 2 | Confirm the reporting definitions in section 3 against the firm's existing report | this file, then the adapter |
| 3 | Create the passphrase hash and session secret, set all variables in Netlify | section 5 |
| 4 | Deploy from a private firm-controlled repo | section 7 |
| 5 | Run the first sync, reconcile numbers, verify a scheduled run with the app closed | sections 8 and 9 |

You should not need to edit anything outside `server/lead-docket.ts` (plus, if you add tests, `tests/`). The rest of the app depends only on the contract in `server/adapter.ts`, never on Lead Docket's raw response shape.

## 2. The adapter (`server/lead-docket.ts`)

Today it is a stub on purpose. Without settings it throws `not_configured`. With settings it throws `integration_incomplete`. It makes no network calls. The tenant's API schema was not supplied, so the stub does not guess endpoint paths, auth header names, field names, status ids, date formats, pagination rules, or an aggregate-count API. Lead Docket's public documentation points account holders to their own tenant API console and warns that API keys can read and modify data, so treat the key accordingly.

### Contract (`server/adapter.ts`)

Implement `fetchSignups(window)` and return one of:

```ts
{ type: "records", records: [{ leadId: string, signedAt: string }] }   // app counts them
{ type: "aggregates", today: number, week: number, month: number }    // source counted them, you verified it
```

- `window.from` is the earlier of the week start and the month start (inclusive). `window.to` is the cutoff (exclusive). `window.signal` aborts at the refresh deadline; pass it to every request.
- `leadId` is the source's stable lead/case identifier. Never a name, phone, or signature count.
- `signedAt` is an ISO timestamp with an offset or `Z` for the authoritative signup event. If a lead has several signing events, return the first qualifying one. Strings without an offset are rejected on purpose so no timezone is guessed.
- Return the complete result or throw an `AdapterError`. Use `collectPages()` from `server/pagination.ts` for paged endpoints. It throws on any failed page, repeated cursor, or runaway paging, so a partial set can never be counted.
- Map failures with `codeForHttpStatus()` and `parseRetryAfter()` from `server/adapter.ts`. Error messages are fixed strings. Never put response bodies, URLs, or client data in errors or logs.
- Read-only. No creating leads, changing statuses, or writing notes.
- If the API cannot support the agreed metric (for example no way to find leads by signup event time), raise it as an integration blocker. Do not substitute creation date, modified date, or "currently in a signed status".

### Verify in the tenant API console and write down the answers

Fill this in (in your PR or in this file) before implementing. None of it is assumed.

| Item | Answer |
| --- | --- |
| Tenant base URL, and whether it includes a path prefix | |
| Authentication method and the header or parameter name for the key | |
| Authoritative signed date/event, and the field that carries it | |
| Is there a signed-date range query? If not, the full alternative retrieval strategy | |
| Qualifying status ids, and how "signed" maps to them (statuses are tenant-configurable) | |
| Stable lead/case identifier field | |
| Timestamp format and timezone of the signed date | |
| Scope filters (all markets and offices), test-record exclusion | |
| Pagination mechanism and page-size limits | |
| Rate limits and `Retry-After` behavior | |
| Cancellation, reopened-case, and duplicate treatment (section 3) | |
| Measured sync duration (section 6) | |

If the API only exposes current status and not when a lead became signed, a status-based query will miss leads that signed and then moved on. That is exactly the case the brief warns about, so stop and escalate rather than approximating.

### Local testing

```bash
cp .env.example .env     # git-ignored; wrap values containing $ in single quotes
# set DATA_MODE=live, LEAD_DOCKET_BASE_URL, LEAD_DOCKET_API_KEY,
# DASHBOARD_PASSPHRASE_HASH, SESSION_SECRET
npm run dev
```

In live mode, `npm run dev` runs the sync at startup and every 30 minutes on a local timer, using an in-memory store. Sign in, and the numbers appear after the first successful sync. Until then the screen honestly shows `--` with "Waiting for first update" (or "Connection not configured" if base URL or key is missing). Look for `refresh_succeeded` or `refresh_failed` (with a `code`) in the terminal.

Add unit tests for your adapter next to the existing ones. Use synthetic fixtures only. Never commit a real Lead Docket response, a real client name, or a real lead id.

## 3. Reporting definitions to confirm

These are proposed defaults from the brief, not verified facts about our configuration. Confirm or change each one, then record the decision here.

| Topic | Proposed default | Decision |
| --- | --- | --- |
| Scope | Firmwide, all markets and offices | |
| Time zone | `America/New_York` (`DASHBOARD_TIMEZONE`) | |
| Today | Local midnight to the cutoff | |
| Week | Monday local midnight to the cutoff (`WEEK_START=SUNDAY` switches it) | |
| Month | First of the month, local midnight, to the cutoff. Not rolling 30 days | |
| Signed | Gross new signups by the actual signup event date, one count per distinct lead/case id | |
| Later close, transfer, loss | Does not erase an earlier signup | |
| Cancellations | Open question | |
| Reopened cases | Open question (first signing event wins in the app's counting) | |
| Test records | Open question | |
| Duplicates (same person, two lead ids) | Open question | |

How the app counts, so your adapter output lines up:

- One cutoff (`asOf`) for all three totals, with half-open intervals `[periodStart, asOf)`. A record exactly at the cutoff is not counted until the next sync.
- Each lead counts once. If the same id appears more than once, its earliest `signedAt` decides which periods it falls in.
- Status is not an input to counting. Exclusions (tests, cancellations, duplicates) must be applied inside the adapter before it returns records.
- Retrieval covers the earlier of the week start and the month start. A week can begin in the previous month or year, so the week total can exceed the month total (for example Thursday Oct 1: the week began Monday Sep 28).
- Totals are recomputed from source every sync, so corrections in Lead Docket flow through. There is no increment-only counter.

## 4. Where state lives

- **Netlify Blobs**, site-wide stores, strong-consistency reads. Only totals and operational metadata are stored: counts, period starts, `asOf`, `lastSuccessfulSyncAt`, and sync failure codes. No lead data.
- Store names carry the mode and, outside production, the deploy context:

  | Situation | Store |
  | --- | --- |
  | Production, live | `mh-signups-live` |
  | Production, demo | `mh-signups-demo` |
  | Deploy preview, live | `mh-signups-live-deploy-preview` |
  | Branch deploy, demo | `mh-signups-demo-branch-deploy` |
  | Login throttle counters | `mh-signups-auth` |

- Site-wide stores are shared by every deploy, so a normal code redeploy does not erase the last good production snapshot. A preview deploy cannot read or write the production live snapshot because its store name differs.
- A snapshot written under one `DASHBOARD_TIMEZONE`, `WEEK_START`, or mode is ignored if those settings change, until the next sync rewrites it. You will see `--` and "Waiting for first update" briefly. That is intended, so definitions are never mixed.

## 5. Configuration and secrets

Set these in the Netlify UI (Site configuration, Environment variables), via the Netlify CLI, or via the API. They cannot be set in `netlify.toml`, and the app never reads them in the browser. Scope secrets to **Functions**, and mark them as secret values where Netlify offers it. Variables are read when a function starts, so **redeploy after any change** (Deploys, trigger a new deploy).

| Variable | Production | Notes |
| --- | --- | --- |
| `DATA_MODE` | `live` | Only `demo` or `live`. Unset means demo. A typo fails closed. Set `live` for the **Production** context only, so previews stay on demo or their own namespace. |
| `DASHBOARD_TIMEZONE` | `America/New_York` | Default if unset. |
| `WEEK_START` | `MONDAY` | `MONDAY` or `SUNDAY`. |
| `LEAD_DOCKET_BASE_URL` | from the tenant console | Must be HTTPS. |
| `LEAD_DOCKET_API_KEY` | from the tenant console | Functions scope only. It can modify data in Lead Docket, so use the narrowest key the tenant console allows. |
| `DASHBOARD_PASSPHRASE_HASH` | bcrypt hash | `npm run hash-passphrase` |
| `SESSION_SECRET` | 32+ random characters | `npm run gen-session-secret` |
| `SESSION_DAYS` | `30` | 1 to 365. |

Generate the two secrets:

```bash
npm run hash-passphrase       # prompts twice with hidden input; prints $2b$12$...
npm run gen-session-secret    # prints a random value
```

Paste the hash exactly as printed into the Netlify UI. If you use a shell or a `.env` file, protect the `$` characters (single quotes). A hash with `$` stripped is detected as malformed and the app refuses to serve counts (503) rather than guessing. Give Mike the passphrase through a channel the firm already trusts for credentials. It is never stored in plaintext anywhere.

**Fail-closed behavior to expect:** in live mode, missing or partial auth settings return 503 with no counts. Missing `LEAD_DOCKET_*` settings show "Connection not configured". Neither ever falls back to demo data.

### Rotation

| What | How |
| --- | --- |
| Lead Docket API key | Create the new key in the tenant console, update `LEAD_DOCKET_API_KEY`, redeploy, run the sync (section 8), then revoke the old key. |
| Passphrase | Run `npm run hash-passphrase`, update `DASHBOARD_PASSPHRASE_HASH`, redeploy. Existing sessions stay valid until you also rotate the session secret. |
| Sign everyone out (revoke all sessions) | Change `SESSION_SECRET`, redeploy. Every cookie stops working immediately. |

## 6. Sync behavior, limits, and the 30-second budget

- A Scheduled Function (`netlify/functions/refresh.mts`, cron `*/30 * * * *`) runs the sync. Netlify cron is in UTC. Business calendar math still uses `DASHBOARD_TIMEZONE`. It does not trigger site builds.
- Per sync: up to 3 attempts, with backoff for timeouts, 5xx, and 429 (honoring `Retry-After` up to 10 seconds; longer than that fails the run). 401, malformed responses, and incomplete paging are not retried. The whole sync has a 25-second deadline, under Netlify's documented 30-second limit for Scheduled Functions. Confirm the current limit in Netlify's docs.
- Any failure keeps the last good snapshot and its original timestamp. The phone shows **Update delayed** once a failure is recorded or the cutoff passes 40 minutes.
- A second sync starting within 60 seconds of the previous attempt is ignored, which absorbs stray hits on the function URL.
- **Measure it.** Each successful run logs `refresh_succeeded` with `durationMs`. If a realistic run approaches roughly 20 seconds, or you see `timeout`, do not truncate. The documented fallback is a longer-running worker path (for example a Netlify Background Function started by the scheduled one, or an external worker) that writes the same snapshot through `SnapshotStore.writeIfNewer`. Evaluate and confirm limits in Netlify's current docs before building it. It was deliberately not added here.
- Netlify runs scheduled functions automatically only on the **published production deploy**. Previews and branch deploys do not run on schedule.

## 7. Authorized deployment sequence

Do these only with explicit authorization. None have been done.

1. **Repository.** Create a **private**, firm-controlled GitHub repository and give Rex access. Recommended: make this folder the root of that new repo. The code was prepared in an `mh-signups/` subfolder only because the existing `blackbar-dashboard` repo holds unrelated files. If you keep it in that repo instead, set Netlify's base directory to `mh-signups`. A private repo or an obscure URL is not the access control. The passphrase login is.
2. **Connect to Netlify.** Add a new site from that repository. Settings: build command `npm run build`, publish directory `dist`, functions directory `netlify/functions`, Node 22. (Base directory only if the app stays in a subfolder.) Deploy once with `DATA_MODE=demo` if you want a first look, or go straight to step 3.
3. **Configure the environment** from section 5. Leave `DATA_MODE=demo` until the adapter is finished. Redeploy after changing variables.
4. **Finish and test the adapter** (section 2), then set `DATA_MODE=live` for the Production context and redeploy.
5. **Validate access control** (section 8, checks A).
6. **Run the first sync** with Netlify's "Run now" for the `refresh` function (see section 8).
7. **Reconcile live numbers** (section 9).
8. **Verify a full scheduled refresh with the app closed** (section 9).
9. **Add to Mike's Home Screen** (below) and confirm it loads the saved snapshot.

### Home Screen

- **iPhone (Safari):** open the production URL in Safari, tap Share, **Add to Home Screen**, **Add**. Leave "Open as Web App" on if shown. Launch from the icon.
- **Android (Chrome):** open the URL in Chrome, three-dot menu, **Add to Home screen** or **Install app**, confirm.

Menu labels shift between OS versions. Test the saved icon independently of a browser tab, including after closing it and reopening, because browser or device policies can still require a new sign-in.

## 8. Verification

### A. Access control (after deploy)

```bash
SITE=https://YOUR-SITE.netlify.app
curl -s -i $SITE/api/stats                      # expect 401, body {"error":"unauthorized"}, Cache-Control: private, no-store
curl -s -i $SITE/.netlify/functions/stats       # same 401 (direct function route is protected identically)
curl -s -i -X POST $SITE/api/login -H 'content-type: application/json' -d '{"passphrase":"x"}'
                                                # expect 403 (no Origin header): login only works from the page
curl -s $SITE/ | grep -ci "today"               # page HTML must not contain live numbers
```

Also confirm in a browser: wrong passphrase is rejected, six rapid wrong attempts lock out for 15 minutes, the session survives closing and reopening the Home Screen app, and "Sign out" returns to the login screen.

### B. First sync

In the Netlify UI, open the site's Functions, open `refresh`, and use **Run now** (a Scheduled Function is not an ordinary public URL; labels may differ, confirm against Netlify's Scheduled Functions docs). Then open the function log. Expect `refresh_succeeded`. A failure logs `refresh_failed` with a `code`:

| Code | Meaning | Fix |
| --- | --- | --- |
| `not_configured` | Base URL or key missing, or not HTTPS | Set variables in the Functions scope, redeploy |
| `integration_incomplete` | Adapter still a stub | Finish `server/lead-docket.ts` |
| `unauthorized` | Lead Docket rejected the key | Check key, scope, tenant URL |
| `rate_limited` | 429 | Check limits, lengthen spacing |
| `timeout` | Request or the 25 s deadline | See the 30-second budget (section 6) |
| `upstream` | 5xx or network error | Retry; investigate if persistent |
| `malformed` | Response did not match the adapter's expectations, or a bad record | Fix normalization; see the `errorClass` field |
| `incomplete` | Paging could not finish | Fix paging; never count a partial set |

Logs contain codes, counts, durations, and attempt numbers only. Source payloads, identifiers, and secrets are never logged. Find them in the Netlify UI under the site's Logs, Functions, for `refresh` and `stats`.

### C. Scheduled runs

Scheduled runs appear in the `refresh` function log every 30 minutes (:00 and :30 UTC) on the published production deploy. Confirm at least two consecutive runs.

## 9. Go-live acceptance

Live release is not accepted just because the demo screen works.

1. **Reconcile all three totals** against the firm's chosen Lead Docket report, using the same scope, time windows, signed definition, and data cutoff. Compare with the phone's `asOf` (visible in `/api/stats` when signed in), not "now". Check at least: a normal day, a day with repeat signings, a lead that signed and was later closed or lost, and (if possible) a week that began in the previous month.
2. **Verify one full scheduled refresh with the app closed.** Close the Home Screen app, wait for a scheduled run to appear in the log, open the app, and confirm the footer time and numbers match that run.
3. **Confirm failure behavior** if you can do so safely: temporarily set a wrong key in a non-production context and confirm the screen shows **Update delayed** and keeps the last good numbers with the original timestamp.
4. **Confirm Eastern boundaries** around midnight or a week or month change if timing allows: the affected number shows `--` and "Waiting for update" until the next sync, then the new period's value.

## 10. Account and plan limits to confirm (nothing here is promised as free)

- **Netlify plan:** Scheduled Functions, Netlify Blobs, and Functions invocation or compute allowances for your plan. A 30-minute schedule is about 1,440 runs a month plus stats reads. Confirm current pricing and limits.
- **Netlify Blobs** usage limits and regional behavior (site-wide stores created without an explicit region use Netlify's default region).
- **Lead Docket:** that API access is included in the firm's plan, key permissions available, and the rate limits.
- **GitHub:** a private repository on the firm's plan.
- **Domain:** a custom domain is optional. Netlify provides HTTPS on its own address.

## 11. Known design notes and limits

- Login throttling is a read-then-write counter in Blobs. It is not atomic, so a burst of truly simultaneous attempts could slip a few extra guesses past the count. Bcrypt cost and the long passphrase are the real brake. It is per client IP as reported by Netlify.
- Passphrases are limited to 72 bytes (bcrypt).
- Demo mode with no auth settings serves synthetic data without a login. Live mode always requires one.
- No service worker, push notifications, background phone polling, or offline count cache. Version one is online-first. While offline, values still in memory are labeled "not current".
- The phone checks the saved snapshot about once a minute while visible. A server refresh at :00 or :30 therefore shows up within about a minute without a 30-minute browser delay.
- `/api/stats` is same-origin only. No CORS headers are set.

## 12. Not done (needs authorization or your access)

- No GitHub repository was created for this app, and nothing was published to Netlify.
- No Lead Docket calls were made. The adapter is a stub, so there is no live integration testing yet.
- No testing on physical iPhone or Android devices. Layout was verified in Chromium at phone sizes only.
- No purchases or paid services were set up.
