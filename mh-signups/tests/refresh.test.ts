import { beforeEach, describe, expect, it } from "vitest";
import { AdapterError, type SourceResult } from "../server/adapter.ts";
import { memoryKv, resetMemoryStores, type KeyValueStore } from "../server/kv.ts";
import { runRefresh, type RefreshDeps } from "../server/refresh.ts";
import { SnapshotStore } from "../server/snapshot-store.ts";
import { capturingLogger, failingSource, LIVE_CONFIG, store, stubSource } from "./helpers.ts";

const T1 = new Date("2026-10-02T18:00:00Z"); // Fri 2:00 PM EDT
const clock = (t: () => Date) => t;

function deps(over: Partial<RefreshDeps> & Pick<RefreshDeps, "source">): RefreshDeps {
  const { snapshots } = store();
  return {
    config: LIVE_CONFIG,
    snapshots,
    now: () => T1,
    sleep: async () => {},
    log: { info() {}, warn() {}, error() {} },
    minAttemptSpacingMs: 0,
    ...over,
  };
}

const oneToday: SourceResult = { type: "records", records: [{ leadId: "L1", signedAt: "2026-10-02T15:00:00Z" }] };

beforeEach(() => resetMemoryStores());

describe("refresh", () => {
  it("writes all three totals, their period starts and honest timestamps together", async () => {
    const d = deps({ source: stubSource(() => oneToday) });
    expect(await runRefresh(d)).toEqual({ status: "success", write: "written" });
    const snap = await d.snapshots.readSnapshot();
    expect(snap).toMatchObject({
      counts: { today: 1, week: 1, month: 1 },
      periodStarts: { today: "2026-10-02T04:00:00.000Z", week: "2026-09-28T04:00:00.000Z", month: "2026-10-01T04:00:00.000Z" },
      asOf: T1.toISOString(),
      source: "leaddocket",
    });
  });

  it("asks the source for the earlier of the week start and month start, up to the cutoff", async () => {
    let seen: { from: string; to: string } | null = null;
    const d = deps({ source: stubSource((w) => ((seen = { from: w.from.toISOString(), to: w.to.toISOString() }), oneToday)) });
    await runRefresh(d);
    expect(seen).toEqual({ from: "2026-09-28T04:00:00.000Z", to: T1.toISOString() });
  });

  it("stores a true zero when a complete query finds nothing", async () => {
    const d = deps({ source: stubSource(() => ({ type: "records", records: [] })) });
    await runRefresh(d);
    expect((await d.snapshots.readSnapshot())?.counts).toEqual({ today: 0, week: 0, month: 0 });
  });

  describe("failures keep the last good snapshot and its original timestamp", () => {
    const cases: [string, ReturnType<typeof failingSource>, string, number][] = [
      ["401 unauthorized", failingSource("unauthorized"), "unauthorized", 1], // never retried
      ["malformed response", failingSource("malformed"), "malformed", 1],
      ["incomplete pagination", failingSource("incomplete"), "incomplete", 1],
      ["429 rate limit with a long Retry-After", failingSource("rate_limited", { retryAfterMs: 120_000 }), "rate_limited", 1],
      ["persistent 5xx", failingSource("upstream"), "upstream", 3], // bounded retries
      ["persistent timeout", failingSource("timeout"), "timeout", 3],
    ];
    for (const [label, source, code, attempts] of cases) {
      it(label, async () => {
        const good = deps({ source: stubSource(() => oneToday), now: () => new Date("2026-10-02T17:00:00Z") });
        await runRefresh(good);
        const before = await good.snapshots.readSnapshot();

        const outcome = await runRefresh({ ...good, source, now: () => T1 });
        expect(outcome).toEqual({ status: "failed", code });
        expect(source.calls).toBe(attempts);
        expect(await good.snapshots.readSnapshot()).toEqual(before); // untouched, not zeroed
        const sync = await good.snapshots.readSyncState();
        expect(sync).toMatchObject({ lastFailureCode: code, consecutiveFailures: 1 });
      });
    }

    it("never writes zero or partial counts when no snapshot exists yet", async () => {
      const d = deps({ source: failingSource("upstream") });
      await runRefresh(d);
      expect(await d.snapshots.readSnapshot()).toBeNull();
    });

    it("does not let a record the source got wrong become a partial count", async () => {
      const d = deps({ source: stubSource(() => ({ type: "records", records: [{ leadId: "A", signedAt: "not a date" }] })) });
      expect(await runRefresh(d)).toEqual({ status: "failed", code: "malformed" });
      expect(await d.snapshots.readSnapshot()).toBeNull();
    });
  });

  it("retries a rate limit after the stated delay and then succeeds", async () => {
    const sleeps: number[] = [];
    const source = stubSource((_w, call) => {
      if (call === 1) throw new AdapterError("rate_limited", { retryAfterMs: 2000 });
      return oneToday;
    });
    const d = deps({ source, sleep: async (ms) => void sleeps.push(ms) });
    expect(await runRefresh(d)).toMatchObject({ status: "success" });
    expect(sleeps).toEqual([2000]);
    expect(source.calls).toBe(2);
    expect(await d.snapshots.readSyncState()).toMatchObject({ consecutiveFailures: 0, lastFailureCode: null });
  });

  it("gives up at the overall deadline instead of truncating results", async () => {
    const source = stubSource(() => new Promise<SourceResult>(() => {})); // never answers
    const d = deps({ source, deadlineMs: 30 });
    expect(await runRefresh(d)).toEqual({ status: "failed", code: "timeout" });
    expect(await d.snapshots.readSnapshot()).toBeNull();
  });

  it("passes an abort signal that fires at the deadline", async () => {
    let aborted = false;
    const source = stubSource(
      (w) =>
        new Promise<SourceResult>((_, reject) => {
          w.signal.addEventListener("abort", () => {
            aborted = true;
            reject(new Error("aborted"));
          });
        }),
    );
    expect(await runRefresh(deps({ source, deadlineMs: 30 }))).toMatchObject({ status: "failed", code: "timeout" });
    expect(aborted).toBe(true);
  });

  it("fails visibly, and never falls back to demo data, when live settings are missing", async () => {
    const source = stubSource(() => oneToday, { configured: false });
    const d = deps({ source });
    expect(await runRefresh(d)).toEqual({ status: "failed", code: "not_configured" });
    expect(source.calls).toBe(0);
    expect(await d.snapshots.readSnapshot()).toBeNull();
  });

  it("an older overlapping run cannot replace a newer snapshot, even if it finishes last", async () => {
    const { snapshots } = store("overlap");
    let releaseOld!: (r: SourceResult) => void;
    const oldSource = stubSource(() => new Promise<SourceResult>((res) => (releaseOld = res)));
    const newSource = stubSource(() => ({ type: "records", records: [{ leadId: "N", signedAt: "2026-10-02T18:10:00Z" }] }));

    const oldRun = runRefresh(deps({ snapshots, source: oldSource, now: clock(() => new Date("2026-10-02T18:00:00Z")) }));
    await new Promise((r) => setTimeout(r, 5)); // old run is now in flight
    const newOutcome = await runRefresh(deps({ snapshots, source: newSource, now: clock(() => new Date("2026-10-02T18:30:00Z")) }));
    expect(newOutcome).toEqual({ status: "success", write: "written" });

    releaseOld({ type: "records", records: [] }); // old run finishes last, with older data
    expect(await oldRun).toEqual({ status: "success", write: "skipped_not_newer" });
    expect((await snapshots.readSnapshot())?.asOf).toBe("2026-10-02T18:30:00.000Z");
  });

  it("retries a write that lost a race on the stored version", async () => {
    const inner = memoryKv("race");
    let interfered = false;
    const racy: KeyValueStore = {
      get: (k) => inner.get(k),
      async put(k, v, expect) {
        if (k === "snapshot" && !interfered) {
          interfered = true;
          await inner.put("snapshot", { ...(v as object), asOf: "2026-10-02T17:00:00.000Z" }); // someone else wrote first
        }
        return inner.put(k, v, expect);
      },
    };
    // The interfering write is an invalid-shape-free older snapshot; our newer one must still land after one retry.
    const snapshots = new SnapshotStore(racy);
    const d = deps({ snapshots, source: stubSource(() => oneToday) });
    expect(await runRefresh(d)).toMatchObject({ status: "success", write: "written" });
    expect((await snapshots.readSnapshot())?.asOf).toBe(T1.toISOString());
  });

  it("ignores a refresh that starts right after another attempt", async () => {
    const source = stubSource(() => oneToday);
    const d = deps({ source, minAttemptSpacingMs: 60_000 });
    expect(await runRefresh(d)).toMatchObject({ status: "success" });
    expect(await runRefresh(d)).toEqual({ status: "skipped_recent" });
    expect(source.calls).toBe(1);
  });

  it("logs codes only, never payloads, identifiers or secrets", async () => {
    const log = capturingLogger();
    const leaky = stubSource(() => {
      throw new Error("boom SECRET-SENTINEL client John Doe 555-0100 lead-9912");
    });
    await runRefresh(deps({ source: leaky, log }));
    const out = log.lines.join("\n");
    expect(out).toContain("upstream");
    expect(out).not.toMatch(/SECRET-SENTINEL|John Doe|555-0100|lead-9912/);
  });
});
