// Renders the placeholder "MH" icons with the Chromium that Playwright already provides.
// Placeholder only: replace public/icons/* with real artwork (same file names) to rebrand.
import { chromium } from "playwright-core";
import { mkdirSync, writeFileSync } from "node:fs";

const out = new URL("../public/icons/", import.meta.url);
mkdirSync(out, { recursive: true });

// `pad` is the fraction of the canvas kept clear on each side (maskable icons need a safe zone).
const jobs = [
  ["icon-192.png", 192, 0.1, true],
  ["icon-512.png", 512, 0.1, true],
  ["icon-maskable-512.png", 512, 0.22, false],
  ["apple-touch-icon.png", 180, 0.12, false],
  ["favicon-32.png", 32, 0.05, true],
];

const executablePath = process.env.CHROMIUM_PATH || undefined;
const browser = await chromium.launch({ executablePath });
try {
  for (const [name, size, pad, rounded] of jobs) {
    const page = await browser.newPage({ viewport: { width: size, height: size } });
    const radius = rounded ? Math.round(size * 0.2) : 0;
    const fontSize = Math.round(size * (1 - 2 * pad) * 0.62);
    await page.setContent(`<!doctype html><body style="margin:0;background:transparent">
      <div style="width:${size}px;height:${size}px;background:#0e1116;border-radius:${radius}px;display:flex;align-items:center;justify-content:center;
        color:#f5f7fa;font:800 ${fontSize}px Helvetica,Arial,sans-serif;letter-spacing:-0.02em">MH</div></body>`);
    const png = await page.screenshot({ omitBackground: true, type: "png" });
    writeFileSync(new URL(name, out), png);
    await page.close();
  }
} finally {
  await browser.close();
}
console.log("icons written to public/icons");
