import { describe, expect, it } from "vitest";
import { Controller, POLL_MS, RENDER_TICK_MS, RESUME_DEBOUNCE_MS, type Environment } from "../src/controller.ts";
import type { StatsResponse } from "../shared/stats.ts";

const stats: StatsResponse = {
  today: 3, week: 12, month: 40, timeZone: "America/New_York", weekStartsOn: "MONDAY",
  periodStarts: { today: "2026-10-02T04:00:00.000Z", week: "2026-09-28T04:00:00.000Z", month: "2026-10-01T04:00:00.000Z" },
  asOf: "2026-10-02T18:00:00.000Z", lastSuccessfulSyncAt: "2026-10-02T18:00:05.000Z",
  status: "ok", source: "leaddocket", unavailableReason: null,
};

type Reply = Response | Error;
function harness(replies: Reply[] = []) {
  let now = Date.parse("2026-10-02T18:10:00Z");
  let visible = true;
  const timers = new Map<number, { fn: () => void; ms: number }>();
  let nextId = 1;
  const calls: { url: string; method: string }[] = [];
  const queue = [...replies];
  const env: Environment = {
    fetch: async (url, init) => {
      calls.push({ url, method: init?.method ?? "GET" });
      const r = queue.shift() ?? new Response(JSON.stringify(stats), { status: 200 });
      if (r instanceof Error) throw r;
      return r;
    },
    now: () => now,
    setInterval: (fn, ms) => { const id = nextId++; timers.set(id, { fn, ms }); return id; },
    clearInterval: (h) => void timers.delete(h as number),
    isVisible: () => visible,
  };
  return {
    controller: new Controller(env), calls, timers, queue,
    advance: (ms: number) => void (now += ms),
    setVisible: (v: boolean) => void (visible = v),
    json: (body: unknown, status = 200) => new Response(JSON.stringify(body), { status }),
  };
}
const settle = () => new Promise((r) => setTimeout(r, 0));

describe("launch, polling and resume", () => {
  it("fetches the saved snapshot immediately on launch", async () => {
    const h = harness();
    h.controller.start();
    await settle();
    expect(h.calls).toEqual([{ url: "/api/stats", method: "GET" }]);
    expect(h.controller.state).toMatchObject({ phase: "ready", offline: false });
    expect(h.controller.state.stats?.today).toBe(3);
  });

  it("polls about once a minute while visible, and pauses when hidden", async () => {
    const h = harness();
    h.controller.start();
    await settle();
    const intervals = [...h.timers.values()].map((t) => t.ms).sort((a, b) => a - b);
    expect(intervals).toEqual([RENDER_TICK_MS, POLL_MS]);
    h.setVisible(false);
    h.controller.onVisibilityChange();
    expect(h.timers.size).toBe(0); // no background polling
    h.setVisible(true);
    h.advance(10_000);
    h.controller.onVisibilityChange();
    await settle();
    expect(h.timers.size).toBe(2);
  });

  it("debounces repeated resume events but not manual refresh", async () => {
    const h = harness();
    h.controller.start();
    await settle();
    h.calls.length = 0;
    h.advance(RESUME_DEBOUNCE_MS + 1);
    h.controller.onVisibilityChange();
    h.controller.onVisibilityChange(); // visibilitychange + pageshow + focus fire together
    h.controller.onOnline();
    await settle();
    expect(h.calls.length).toBe(1);
    h.advance(100);
    h.controller.onVisibilityChange(); // inside the debounce window
    await settle();
    expect(h.calls.length).toBe(1);
    await h.controller.refreshView();
    expect(h.calls.length).toBe(2);
  });

  it("'Refresh view' only re-reads the saved snapshot and never touches timestamps", async () => {
    const h = harness();
    h.controller.start();
    await settle();
    const before = h.controller.state.stats!.lastSuccessfulSyncAt;
    h.advance(20 * 60_000);
    await h.controller.refreshView();
    expect(h.calls.every((c) => c.url === "/api/stats" && c.method === "GET")).toBe(true);
    expect(h.controller.state.stats!.lastSuccessfulSyncAt).toBe(before);
  });

  it("does not stack requests while one is in flight", async () => {
    const h = harness();
    h.controller.start();
    void h.controller.refreshView();
    void h.controller.refreshView();
    await settle();
    expect(h.calls.length).toBe(1);
  });
});

describe("offline and errors", () => {
  it("keeps in-memory numbers but flags them as not current when the network fails, then recovers", async () => {
    const h = harness();
    h.controller.start();
    await settle();
    h.queue.push(new TypeError("Failed to fetch"));
    await h.controller.refreshView();
    expect(h.controller.state).toMatchObject({ offline: true, phase: "ready" });
    expect(h.controller.state.stats?.today).toBe(3);
    await h.controller.refreshView();
    expect(h.controller.state.offline).toBe(false);
  });

  it("treats a server error or an unreadable body as offline, not as zeros", async () => {
    const h = harness([h500()]);
    h.controller.start();
    await settle();
    expect(h.controller.state).toMatchObject({ offline: true, stats: null });
    h.queue.push(new Response("<html>oops</html>", { status: 200 }));
    await h.controller.refreshView();
    expect(h.controller.state).toMatchObject({ offline: true, stats: null });
    function h500() { return new Response("{}", { status: 500 }); }
  });
});

describe("authentication on the phone", () => {
  it("shows login on 401 and loads numbers after a successful sign in", async () => {
    const h = harness([new Response("{}", { status: 401 })]);
    h.controller.start();
    await settle();
    expect(h.controller.state).toMatchObject({ phase: "login", stats: null });
    h.queue.push(h.json({ ok: true })); // login
    const outcome = await h.controller.submitLogin("secret passphrase");
    expect(outcome.kind).toBe("ok");
    expect(h.calls.map((c) => `${c.method} ${c.url}`)).toEqual(["GET /api/stats", "POST /api/login", "GET /api/stats"]);
    expect(h.controller.state).toMatchObject({ phase: "ready", loginError: null });
  });

  it("reports wrong passphrase and throttling", async () => {
    const h = harness([new Response("{}", { status: 401 }), h0(401), h0(429, { retryAfterSeconds: 600 })]);
    h.controller.start();
    await settle();
    await h.controller.submitLogin("x");
    expect(h.controller.state.loginError).toBe("invalid");
    await h.controller.submitLogin("x");
    expect(h.controller.state).toMatchObject({ loginError: "throttled", retryAfterSeconds: 600 });
    function h0(status: number, body: unknown = {}) { return new Response(JSON.stringify(body), { status }); }
  });

  it("returns to login when the session expires mid-use, and drops the numbers", async () => {
    const h = harness();
    h.controller.start();
    await settle();
    h.queue.push(new Response("{}", { status: 401 }));
    await h.controller.refreshView();
    expect(h.controller.state).toMatchObject({ phase: "login", stats: null });
  });

  it("shows a blocked state when the server has no authentication configured", async () => {
    const h = harness([new Response("{}", { status: 503 })]);
    h.controller.start();
    await settle();
    expect(h.controller.state).toMatchObject({ phase: "auth_unconfigured", stats: null });
  });

  it("sign out clears in-memory numbers", async () => {
    const h = harness();
    h.controller.start();
    await settle();
    await h.controller.signOut();
    expect(h.calls.at(-1)).toEqual({ url: "/api/logout", method: "POST" });
    expect(h.controller.state).toMatchObject({ phase: "login", stats: null });
  });
});
