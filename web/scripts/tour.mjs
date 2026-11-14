// Step through every example with the arrow key and report what each one shows:
//   node web/scripts/tour.mjs <base url> [out dir for screenshots]
import { chromium } from "playwright-core";

const [base = "http://127.0.0.1:8000", out] = process.argv.slice(2);
const browser = await chromium.launch({
  executablePath: process.env.CHROME ?? "/usr/bin/google-chrome-stable",
  args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 950 } });
const problems = [];
page.on("console", (m) => m.type() === "error" && problems.push(`console: ${m.text()}`));
page.on("pageerror", (e) => problems.push(`page: ${e.message}`));
page.on("response", (r) => r.status() >= 400 && problems.push(`${r.status()} ${r.url()}`));
await page.goto(base, { waitUntil: "domcontentloaded" });
await page.waitForSelector(".sheet.loaded, .viewer3d", { timeout: 60000 });
const stops = await page.evaluate(() => document.querySelectorAll(".tour li").length);
await page.click(".tour > button");
const count = await page.evaluate(() => document.querySelectorAll(".tour li").length);
await page.click(".tour > button");
for (let i = 0; i < count; i++) {
  await page.locator("body").press("ArrowRight");
  await page.waitForSelector(".panel .verdict", { timeout: 120000 });
  await page.waitForTimeout(5200);
  const seen = await page.evaluate(() => ({
    state: document.querySelector(".verdict strong")?.textContent,
    value: document.querySelector(".value")?.textContent,
    question: document.querySelector(".asked")?.textContent?.slice(0, 70),
    loops: document.querySelectorAll(".mark.count").length,
    rooms: document.querySelectorAll(".mark.room").length,
    tags3d: document.querySelectorAll(".tag3d").length,
    listed: document.querySelectorAll(".contact button, .chips button, .rows li").length,
    stamp: document.querySelector(".stamp .main")?.textContent,
  }));
  console.log(i + 1, JSON.stringify(seen));
  if (out) await page.screenshot({ path: `${out}/stop-${String(i + 1).padStart(2, "0")}.png` });
}
console.log(problems.length ? `PROBLEMS:\n${problems.join("\n")}` : "no console errors, no failed requests");
await browser.close();
