// Scroll through the landing page's story and save what each moment looks like:
//   node web/scripts/story.mjs <base url> <out dir> [fractions, comma separated]
import { chromium } from "playwright-core";

const [base = "http://127.0.0.1:8000", out = ".", list = "0,0.2,0.42,0.63,0.78,0.95"] = process.argv.slice(2);
const browser = await chromium.launch({
  executablePath: process.env.CHROME ?? "/usr/bin/google-chrome-stable",
  args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 950 } });
page.on("console", (m) => m.type() === "error" && console.log("console error:", m.text()));
page.on("pageerror", (e) => console.log("page error:", e.message));
await page.goto(base, { waitUntil: "networkidle" });
await page.waitForSelector(".story canvas", { timeout: 60000 });
await page.waitForTimeout(2500);
for (const f of list.split(",").map(Number)) {
  await page.evaluate((share) => {
    const el = document.querySelector(".story");
    const span = el.getBoundingClientRect().height - window.innerHeight;
    window.scrollTo(0, el.offsetTop + span * share - 64);
  }, f);
  await page.waitForTimeout(2600);
  await page.screenshot({ path: `${out}/story-${String(Math.round(f * 100)).padStart(3, "0")}.png` });
}
await browser.close();
