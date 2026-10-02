import { existsSync, mkdirSync } from "node:fs";
import { chromium, type Browser } from "playwright-core";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { AUTH_ENV, PASSPHRASE } from "../helpers.ts";

/**
 * Real browser, real handlers, demo data. Needs a Chromium: set CHROMIUM_PATH, or it uses the
 * one Playwright is configured with in this environment. Skipped if none is found.
 */
const executablePath = process.env.CHROMIUM_PATH ?? "/opt/pw-browsers/chromium";
const available = existsSync(executablePath);
const shots = process.env.E2E_SHOTS_DIR ?? "test-results";
mkdirSync(shots, { recursive: true });

const ENV_KEYS = ["DATA_MODE", "DASHBOARD_PASSPHRASE_HASH", "SESSION_SECRET", "SESSION_DAYS", "DASHBOARD_TIMEZONE", "WEEK_START"];
function setEnv(env: Record<string, string>) {
  for (const k of ENV_KEYS) delete process.env[k];
  Object.assign(process.env, env);
}

async function start(env: Record<string, string>): Promise<{ server: ViteDevServer; url: string }> {
  setEnv(env);
  const server = await createServer({ root: process.cwd(), logLevel: "silent", server: { port: 0, host: "127.0.0.1" } });
  await server.listen();
  const address = server.httpServer!.address();
  const port = typeof address === "object" && address ? address.port : 0;
  return { server, url: `http://127.0.0.1:${port}` };
}

describe.skipIf(!available)("phone screen (demo data, no authentication configured)", () => {
  let browser: Browser;
  let server: ViteDevServer;
  let url: string;

  beforeAll(async () => {
    ({ server, url } = await start({ DATA_MODE: "demo" }));
    browser = await chromium.launch({ executablePath });
  });
  afterAll(async () => {
    await browser?.close();
    await server?.close();
  });

  const phones = [
    ["iPhone SE (small)", 320, 568],
    ["Android small", 360, 640],
    ["iPhone 8", 375, 667],
    ["iPhone 15", 390, 844],
    ["iPhone Pro Max", 430, 932],
  ] as const;

  for (const [name, width, height] of phones) {
    it(`shows all three totals without scrolling: ${name} ${width}x${height}`, async () => {
      const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 2, hasTouch: true, isMobile: true });
      await page.goto(url);
      await page.waitForSelector(".metric-value:not(:has-text('--'))");

      const labels = await page.locator(".metric-label").allTextContents();
      expect(labels).toEqual(["SIGNED TODAY", "SIGNED THIS WEEK", "SIGNED THIS MONTH"]);
      await expect(page.locator(".demo-flag").isVisible()).resolves.toBe(true);
      expect(await page.locator(".demo-flag").textContent()).toBe("DEMO DATA");

      const layout = await page.evaluate(() => ({
        scrollH: document.documentElement.scrollHeight,
        innerH: window.innerHeight,
        scrollW: document.documentElement.scrollWidth,
        innerW: window.innerWidth,
        today: parseFloat(getComputedStyle(document.querySelector(".metric-today .metric-value")!).fontSize),
        week: parseFloat(getComputedStyle(document.querySelector(".metric-week .metric-value")!).fontSize),
        month: parseFloat(getComputedStyle(document.querySelector(".metric-month .metric-value")!).fontSize),
        tops: ["today", "week", "month"].map((k) => document.querySelector(`.metric-${k}`)!.getBoundingClientRect().top),
        footerBottom: document.querySelector(".foot")!.getBoundingClientRect().bottom,
      }));
      expect(layout.scrollH).toBeLessThanOrEqual(layout.innerH); // no vertical scroll
      expect(layout.scrollW).toBeLessThanOrEqual(layout.innerW); // no horizontal scroll
      expect(layout.today).toBeGreaterThan(layout.week * 1.8); // today dominates
      expect(layout.week).toBeCloseTo(layout.month, 0);
      expect(layout.tops[0]!).toBeLessThan(layout.tops[1]!);
      expect(layout.tops[1]!).toBeLessThan(layout.tops[2]!); // single column, in order
      expect(layout.footerBottom).toBeLessThanOrEqual(layout.innerH);

      const footer = await page.locator(".foot-text").textContent();
      expect(footer).toMatch(/^Updated \w{3}, \w{3} \d{1,2}, \d{1,2}:\d{2} [AP]M E[SD]T$/);
      await page.screenshot({ path: `${shots}/phone-${width}x${height}.png` });
      await page.close();
    });
  }

  it("copes with enlarged text: no horizontal overflow and everything still reachable", async () => {
    const page = await browser.newPage({ viewport: { width: 360, height: 640 }, hasTouch: true, isMobile: true });
    await page.addInitScript(() => {
      document.addEventListener("DOMContentLoaded", () => (document.documentElement.style.fontSize = "200%"));
    });
    await page.goto(url);
    await page.waitForSelector(".metric-value:not(:has-text('--'))");
    const o = await page.evaluate(() => ({ w: document.documentElement.scrollWidth, iw: window.innerWidth }));
    expect(o.w).toBeLessThanOrEqual(o.iw);
    await page.locator(".foot").scrollIntoViewIfNeeded();
    await expect(page.locator("text=Refresh view").isVisible()).resolves.toBe(true);
    await page.screenshot({ path: `${shots}/phone-enlarged-text.png`, fullPage: true });
    await page.close();
  });

  it("has no chart, menu, list or table elements", async () => {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await page.goto(url);
    await page.waitForSelector(".metric-value:not(:has-text('--'))");
    expect(await page.locator("canvas, svg, table, ul, ol, nav, select").count()).toBe(0);
    expect(await page.locator(".metric").count()).toBe(3);
    await page.close();
  });

  it("serves manifest and icons, and the stats response contains no individual records", async () => {
    const page = await browser.newPage();
    const manifest = await (await page.request.get(`${url}/manifest.webmanifest`)).json();
    expect(manifest.display).toBe("standalone");
    for (const icon of manifest.icons) expect((await page.request.get(`${url}${icon.src}`)).status()).toBe(200);
    const res = await page.request.get(`${url}/api/stats`);
    expect(res.headers()["cache-control"]).toBe("private, no-store");
    const text = await res.text();
    expect(text).not.toMatch(/DEMO-\d{4}/); // synthetic lead ids never leave the server
    expect(Object.keys(JSON.parse(text)).sort()).toEqual(
      ["asOf", "lastSuccessfulSyncAt", "month", "periodStarts", "source", "status", "timeZone", "today", "unavailableReason", "week", "weekStartsOn"],
    );
    await page.close();
  });

  it("Refresh view re-reads the saved snapshot and the footer time does not jump", async () => {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await page.goto(url);
    await page.waitForSelector(".metric-value:not(:has-text('--'))");
    const before = await page.locator(".foot-text").textContent();
    const requests: string[] = [];
    page.on("request", (r) => requests.push(`${r.method()} ${new URL(r.url()).pathname}`));
    await Promise.all([
      page.waitForResponse((r) => r.url().endsWith("/api/stats")),
      page.getByRole("button", { name: "Refresh view" }).click(),
    ]);
    expect(requests).toEqual(["GET /api/stats"]);
    expect(await page.locator(".foot-text").textContent()).toBe(before);
    await page.close();
  });

  it("shows '--' and an offline label (not zeros) when the server is unreachable at launch", async () => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await context.newPage();
    await page.route("**/api/stats", (r) => r.abort("connectionrefused"));
    await page.goto(url);
    await page.waitForSelector(".banner");
    expect(await page.locator(".metric-value").allTextContents()).toEqual(["--", "--", "--"]);
    expect(await page.locator(".banner").textContent()).toContain("Offline");
    await page.screenshot({ path: `${shots}/phone-offline.png` });
    await context.close();
  });
});

describe.skipIf(!available)("sign in (authentication configured)", () => {
  let browser: Browser;
  let server: ViteDevServer;
  let url: string;

  beforeAll(async () => {
    ({ server, url } = await start({ DATA_MODE: "demo", ...(AUTH_ENV as Record<string, string>) }));
    browser = await chromium.launch({ executablePath });
  });
  afterAll(async () => {
    await browser?.close();
    await server?.close();
    setEnv({});
  });

  it("blocks direct API access, requires the passphrase, remembers the session, and signs out", async () => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const direct = await context.request.get(`${url}/api/stats`);
    expect(direct.status()).toBe(401);
    expect(await direct.text()).not.toMatch(/today|week|month/);

    const page = await context.newPage();
    await page.goto(url);
    await page.waitForSelector("input[type=password]");
    expect(await page.locator(".metric").count()).toBe(0); // no numbers before sign in
    await page.screenshot({ path: `${shots}/phone-login.png` });

    await page.fill("input[type=password]", "not the passphrase");
    await page.click("button[type=submit]");
    await page.waitForSelector("text=That passphrase did not work.");

    await page.fill("input[type=password]", PASSPHRASE);
    await page.click("button[type=submit]");
    await page.waitForSelector(".metric-value:not(:has-text('--'))");
    expect(await page.locator(".demo-flag").textContent()).toBe("DEMO DATA");

    const cookies = await context.cookies();
    const session = cookies.find((c) => c.name === "mh_session")!; // plain name on http://localhost
    expect(session).toMatchObject({ httpOnly: true, sameSite: "Strict" });
    expect(await page.evaluate(() => document.cookie)).toBe(""); // not readable from script
    expect(await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]))).toBe("[{},{}]"); // nothing stored

    await page.reload(); // like reopening the Home Screen app
    await page.waitForSelector(".metric-value:not(:has-text('--'))");

    await page.getByRole("button", { name: "Sign out" }).click();
    await page.waitForSelector("input[type=password]");
    expect((await context.request.get(`${url}/api/stats`)).status()).toBe(401);
    await context.close();
  });
});
