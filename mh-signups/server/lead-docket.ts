/**
 * LEAD DOCKET ADAPTER (server-only). OWNED BY REX. NOT IMPLEMENTED.
 *
 * This file is intentionally a stub. The tenant's real API schema was not supplied, so nothing
 * below invents endpoint paths, auth header names, field names, status ids, date formats,
 * pagination rules, or an aggregate-count API. Verify every item in the tenant's Lead Docket
 * API console (per Lead Docket's "API Key and Console" support article), then implement
 * fetchSignups() against the contract in ./adapter.ts. Details: REX_HANDOFF.md.
 *
 * Rules this implementation must follow:
 *  - READ-ONLY. Issue only retrieval requests. Never create leads, change statuses, or write notes.
 *  - Return the COMPLETE result for [window.from, window.to) or throw an AdapterError. Use
 *    collectPages() from ./pagination.ts for paged endpoints so a partial page set cannot be counted.
 *  - Pass window.signal to every outbound request so the refresh deadline is honored.
 *  - Map failures with codeForHttpStatus() and parseRetryAfter() from ./adapter.ts. Use only
 *    fixed error messages. Never include response bodies, URLs, or client data in errors or logs.
 *  - Filter by the authoritative SIGNUP EVENT time, not creation, modified, appointment, or export
 *    date, and not only by "currently in a signed status". If the API cannot do that, raise it as
 *    an integration blocker rather than substituting another definition.
 *  - Return only { leadId, signedAt } (or verified aggregates). No names, phones, or other client data.
 *  - Credentials come only from runtime environment variables (never VITE_*, never committed).
 */
import { AdapterError, type FetchWindow, type SignupSource, type SourceResult } from "./adapter.ts";
import type { Env } from "./config.ts";

export function createLeadDocketSource(env: Env): SignupSource {
  const baseUrl = (env.LEAD_DOCKET_BASE_URL ?? "").trim();
  const apiKey = (env.LEAD_DOCKET_API_KEY ?? "").trim();

  return {
    kind: "leaddocket",

    isConfigured(): boolean {
      if (baseUrl === "" || apiKey === "") return false;
      try {
        return new URL(baseUrl).protocol === "https:";
      } catch {
        return false;
      }
    },

    async fetchSignups(_window: FetchWindow): Promise<SourceResult> {
      if (!this.isConfigured()) throw new AdapterError("not_configured");

      // TODO(Rex): confirm in the tenant API console, then implement. None of these are assumed:
      //  1. Tenant base URL form and whether LEAD_DOCKET_BASE_URL already includes any path prefix.
      //  2. Authentication method and header/parameter name for the API key.
      //  3. Authoritative signed date/event and the field that carries it.
      //  4. Whether a signed-date RANGE query exists. If not, document the full alternative
      //     retrieval strategy (never "filter by creation date and hope").
      //  5. Qualifying status ids and how they map to "signed" (statuses are tenant-configurable).
      //  6. Stable lead/case identifier field.
      //  7. Timestamp format and timezone of the signed date (convert to ISO with offset or Z).
      //  8. Scope filters (all markets/offices), test-record exclusion, duplicate handling.
      //  9. Pagination mechanism and page-size limits.
      // 10. Rate limits and Retry-After behavior.
      // 11. Cancellation / reopened-case treatment, to match the firm's existing report.
      //
      // Skeleton (fill in after verification, keep it read-only):
      //   const items = await collectPages(async (cursor) => {
      //     const res = await fetch(<VERIFY: request>, { signal: window.signal, headers: <VERIFY: auth> });
      //     if (!res.ok) throw new AdapterError(codeForHttpStatus(res.status), { status: res.status,
      //       retryAfterMs: parseRetryAfter(res.headers.get("retry-after")) });
      //     return <VERIFY: normalize page into { items, nextCursor }>;
      //   });
      //   return { type: "records", records: items.map((x) => ({ leadId: <VERIFY>, signedAt: <VERIFY> })) };
      throw new AdapterError("integration_incomplete");
    },
  };
}
