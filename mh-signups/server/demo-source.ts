import type { FetchWindow, SignupRecord, SignupSource, SourceResult } from "./adapter.ts";

/**
 * SYNTHETIC DATA ONLY. Generates made-up signups so the phone display can be reviewed before
 * Lead Docket is connected. Ids look like DEMO-2026-10-02-3. Nothing here comes from a real client.
 *
 * Deliberately includes the cases the counting logic must handle:
 *  - repeat signing events for one lead (must count once)
 *  - leads whose current status is now closed/lost (must still count: gross signups)
 */

interface DemoLead {
  leadId: string;
  signedAt: string;
  currentStatus: "Signed" | "Closed - Lost" | "Transferred";
}

function hash(n: number): number {
  let x = (n ^ 0x9e3779b9) >>> 0;
  x = Math.imul(x ^ (x >>> 16), 0x85ebca6b) >>> 0;
  x = Math.imul(x ^ (x >>> 13), 0xc2b2ae35) >>> 0;
  return (x ^ (x >>> 16)) >>> 0;
}

export function generateDemoLeads(asOf: Date, days = 70): DemoLead[] {
  const leads: DemoLead[] = [];
  const base = Date.UTC(asOf.getUTCFullYear(), asOf.getUTCMonth(), asOf.getUTCDate());
  for (let i = 0; i < days; i++) {
    const dayStart = base - i * 86_400_000;
    const day = new Date(dayStart);
    const dayKey = day.toISOString().slice(0, 10);
    const dow = day.getUTCDay();
    const seed = Math.floor(dayStart / 86_400_000);
    const count = dow === 0 || dow === 6 ? hash(seed) % 3 : 4 + (hash(seed) % 7);
    for (let j = 0; j < count; j++) {
      // Business hours in Eastern time (about 13:00 to 22:59 UTC). The first signup of a day lands
      // early (about 8:00 to 8:30 AM Eastern) so a morning demo view usually shows a non-zero "today".
      const minuteOfDay = j === 0 ? 12 * 60 + (hash(seed) % 30) : 13 * 60 + (hash(seed * 31 + j) % 600);
      const signedAt = new Date(dayStart + minuteOfDay * 60_000).toISOString();
      const r = hash(seed * 17 + j) % 10;
      leads.push({
        leadId: `DEMO-${dayKey}-${j}`,
        signedAt,
        currentStatus: r === 0 ? "Closed - Lost" : r === 1 ? "Transferred" : "Signed",
      });
    }
  }
  return leads;
}

export function createDemoSource(now: () => Date = () => new Date()): SignupSource {
  return {
    kind: "demo",
    isConfigured: () => true,
    async fetchSignups(window: FetchWindow): Promise<SourceResult> {
      const records: SignupRecord[] = [];
      for (const lead of generateDemoLeads(now())) {
        const t = Date.parse(lead.signedAt);
        if (t < window.from.getTime() || t >= window.to.getTime()) continue;
        // Status is deliberately ignored: a later loss or transfer does not erase a signup.
        records.push({ leadId: lead.leadId, signedAt: lead.signedAt });
        // Every fifth lead is re-signed later (e.g. amended contract): same lead, extra event.
        if (hash(t) % 5 === 0) {
          records.push({ leadId: lead.leadId, signedAt: new Date(Math.min(t + 3_600_000, window.to.getTime() - 1)).toISOString() });
        }
      }
      return { type: "records", records };
    },
  };
}
