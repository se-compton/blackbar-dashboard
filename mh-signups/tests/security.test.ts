import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";
import { build } from "vite";
import { afterAll, describe, expect, it, vi } from "vitest";
import { createLeadDocketSource } from "../server/lead-docket.ts";
import { sanitizeFields } from "../server/log.ts";

const root = new URL("..", import.meta.url).pathname;

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)]));
}
const read = (p: string) => readFileSync(join(root, p), "utf8");

describe("secrets stay out of the browser bundle", () => {
  const SENTINELS = {
    LEAD_DOCKET_API_KEY: "SENTINEL-LD-KEY-9f3a1c",
    SESSION_SECRET: "SENTINEL-SESSION-SECRET-77d2e0-padpadpadpad",
    DASHBOARD_PASSPHRASE_HASH: "SENTINEL-HASH-4b8c",
    LEAD_DOCKET_BASE_URL: "https://sentinel-tenant.example.invalid",
    VITE_LEAKY: "should-never-matter",
  };
  const outDir = mkdtempSync(join(tmpdir(), "mh-dist-"));
  afterAll(() => rmSync(outDir, { recursive: true, force: true }));

  it("a production build with every secret set contains none of them, nor their names", async () => {
    const saved = { ...process.env };
    Object.assign(process.env, SENTINELS);
    try {
      await build({ root, logLevel: "silent", build: { outDir, emptyOutDir: true } });
    } finally {
      for (const k of Object.keys(SENTINELS)) delete process.env[k];
      Object.assign(process.env, saved);
    }
    const files = walk(outDir);
    expect(files.length).toBeGreaterThan(3);
    for (const f of files) {
      if (/\.(png)$/.test(f)) continue;
      const text = readFileSync(f, "utf8");
      for (const [name, value] of Object.entries(SENTINELS)) {
        expect(text, `${relative(outDir, f)} leaks ${name}`).not.toContain(value);
      }
      expect(text, relative(outDir, f)).not.toMatch(/LEAD_DOCKET_API_KEY|SESSION_SECRET|DASHBOARD_PASSPHRASE_HASH|LEAD_DOCKET_BASE_URL/);
    }
  });

  it("the build output holds only static assets: no data files, no baked-in counts, no service worker", () => {
    const names = walk(outDir).map((f) => relative(outDir, f));
    expect(names.filter((n) => /\.json$/.test(n))).toEqual([]);
    expect(names.some((n) => /sw\.js|service-worker/.test(n))).toBe(false);
    const html = readFileSync(join(outDir, "index.html"), "utf8");
    expect(html).toContain('name="robots" content="noindex, nofollow"');
    expect(html).not.toMatch(/<script(?![^>]*src=)[^>]*>[^<]+/); // no inline script: CSP stays strict
  });
});

describe("source layout", () => {
  it("frontend and shared code never import from server/ or read environment variables", () => {
    for (const dir of ["src", "shared"]) {
      for (const f of walk(join(root, dir))) {
        if (!/\.(ts|css)$/.test(f)) continue;
        const text = readFileSync(f, "utf8");
        expect(text, f).not.toMatch(/from\s+["'][./]*server\//);
        expect(text, f).not.toMatch(/process\.env|import\.meta\.env|netlify\/blobs/);
      }
    }
  });

  it("no browser storage or service worker is used for counts or tokens", () => {
    for (const f of walk(join(root, "src"))) {
      if (!f.endsWith(".ts")) continue;
      expect(readFileSync(f, "utf8"), f).not.toMatch(/localStorage|sessionStorage|indexedDB|serviceWorker|document\.cookie/);
    }
  });

  it("no secrets or live data files are tracked, and .env files are ignored", () => {
    const tracked = execFileSync("git", ["ls-files", "--cached", "--others", "--exclude-standard", "."], { cwd: root, encoding: "utf8" })
      .split("\n").filter(Boolean);
    expect(tracked.filter((f) => /(^|\/)\.env($|\.)/.test(f) && !f.endsWith(".env.example"))).toEqual([]);
    expect(tracked.some((f) => f.startsWith("node_modules/") || f.startsWith("dist/"))).toBe(false);
    const example = read(".env.example");
    for (const name of ["LEAD_DOCKET_API_KEY", "LEAD_DOCKET_BASE_URL", "DASHBOARD_PASSPHRASE_HASH", "SESSION_SECRET"]) {
      expect(example).toMatch(new RegExp(`^${name}=$`, "m")); // placeholders only
    }
    expect(example).not.toMatch(/VITE_/);
  });

  it("netlify.toml carries no secrets, schedules nothing as a build, and sets security headers", () => {
    const toml = read("netlify.toml");
    expect(toml).not.toMatch(/API_KEY|SESSION_SECRET|PASSPHRASE|VITE_/);
    expect(toml).toContain('publish = "dist"');
    expect(toml).toContain('command = "npm run build"');
    expect(toml).toContain("X-Robots-Tag");
    expect(toml).toContain("Content-Security-Policy");
    expect(toml).toContain('NODE_VERSION = "22"');
  });

  it("the scheduled function runs every 30 minutes and is not a build", () => {
    expect(read("netlify/functions/refresh.mts")).toContain('schedule: "*/30 * * * *"');
  });
});

describe("Home Screen assets", () => {
  const manifest = JSON.parse(read("public/manifest.webmanifest"));

  it("has the required manifest fields", () => {
    expect(manifest).toMatchObject({ name: "MH Signups", short_name: "MH Signups", start_url: "/", scope: "/", display: "standalone" });
    const sizes = manifest.icons.map((i: { sizes: string; purpose: string }) => `${i.sizes}:${i.purpose}`);
    expect(sizes).toEqual(expect.arrayContaining(["192x192:any", "512x512:any", "512x512:maskable"]));
  });

  it("ships real PNGs at the declared sizes, plus an Apple touch icon and favicon", () => {
    const dims = (p: string) => {
      const b = readFileSync(join(root, "public", p));
      expect(b.subarray(1, 4).toString()).toBe("PNG");
      return `${b.readUInt32BE(16)}x${b.readUInt32BE(20)}`;
    };
    for (const icon of manifest.icons) expect(dims(icon.src.slice(1))).toBe(icon.sizes);
    expect(dims("icons/apple-touch-icon.png")).toBe("180x180");
    expect(existsSync(join(root, "public/favicon.svg"))).toBe(true);
    const html = read("index.html");
    expect(html).toContain('rel="apple-touch-icon"');
    expect(html).toContain("viewport-fit=cover");
    expect(html).toContain('rel="manifest"');
  });

  it("does not ship an invented official logo, only the MH placeholder", () => {
    expect(readdirSync(join(root, "public/icons")).sort()).toEqual(["apple-touch-icon.png", "favicon-32.png", "icon-192.png", "icon-512.png", "icon-maskable-512.png"]);
    expect(statSync(join(root, "public/icons/icon-512.png")).size).toBeLessThan(50_000);
  });
});

describe("Lead Docket adapter stub", () => {
  it("is unconfigured without settings and fails visibly, with no network access", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const s = createLeadDocketSource({});
    expect(s.isConfigured()).toBe(false);
    await expect(s.fetchSignups({ from: new Date(), to: new Date(), signal: new AbortController().signal })).rejects.toMatchObject({ code: "not_configured" });
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("is honest about being unimplemented once settings exist, and still makes no request", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const s = createLeadDocketSource({ LEAD_DOCKET_BASE_URL: "https://tenant.example.invalid", LEAD_DOCKET_API_KEY: "placeholder-key" });
    expect(s.isConfigured()).toBe(true);
    await expect(s.fetchSignups({ from: new Date(), to: new Date(), signal: new AbortController().signal })).rejects.toMatchObject({ code: "integration_incomplete" });
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("rejects a non-HTTPS base URL", () => {
    expect(createLeadDocketSource({ LEAD_DOCKET_BASE_URL: "http://tenant.example.invalid", LEAD_DOCKET_API_KEY: "k" }).isConfigured()).toBe(false);
    expect(createLeadDocketSource({ LEAD_DOCKET_BASE_URL: "not a url", LEAD_DOCKET_API_KEY: "k" }).isConfigured()).toBe(false);
  });

  it("does not hard-code any endpoint, header name, or field name", () => {
    const code = read("server/lead-docket.ts").split("\n").filter((l) => !l.trim().startsWith("//") && !l.trim().startsWith("*") && !l.trim().startsWith("/*")).join("\n");
    expect(code).not.toMatch(/https?:\/\/[a-z]/i);
    expect(code).not.toMatch(/Authorization|Bearer|X-Api|apikey=/i);
  });
});

describe("log redaction", () => {
  it("redacts sensitive keys and drops non-primitive values", () => {
    expect(
      sanitizeFields({ code: "timeout", attempt: 2, apiKey: "abc", cookie: "x", leadId: "123", payload: { a: 1 }, nested: { b: 2 }, ok: true }),
    ).toEqual({ code: "timeout", attempt: 2, apiKey: "[redacted]", cookie: "[redacted]", leadId: "[redacted]", payload: "[redacted]", nested: "[omitted]", ok: true });
  });
});
