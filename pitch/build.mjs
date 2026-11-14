// Render the pitch deck to PDF, and each slide to a PNG for checking:
//   node pitch/build.mjs
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import fs from "node:fs";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(here, "../web/package.json"));
const { chromium } = require("playwright-core");

const browser = await chromium.launch({ executablePath: process.env.CHROME ?? "/usr/bin/google-chrome-stable" });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
await page.goto(pathToFileURL(path.join(here, "deck.html")).href, { waitUntil: "networkidle" });
await page.evaluate(() => document.fonts.ready);
await page.pdf({ path: path.join(here, "blueprints-talk-deck.pdf"), width: "1600px", height: "900px", printBackground: true });

const out = path.join(here, "preview");
fs.mkdirSync(out, { recursive: true });
const slides = await page.locator(".slide").count();
for (let i = 0; i < slides; i++) {
  await page.locator(".slide").nth(i).screenshot({ path: path.join(out, `slide-${i + 1}.png`) });
}
console.log(`${slides} slides -> pitch/blueprints-talk-deck.pdf`);
await browser.close();
