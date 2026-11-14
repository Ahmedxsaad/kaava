// Screenshot the running app: node web/scripts/shot.mjs <url> <out.png> [waitMs] [width] [height] [click selector] [then wait for selector]
import { chromium } from "playwright-core";

const [url, out, wait = "3500", width = "1600", height = "950", click, until] = process.argv.slice(2);
const browser = await chromium.launch({
  executablePath: process.env.CHROME ?? "/usr/bin/google-chrome-stable",
  args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
});
const page = await browser.newPage({ viewport: { width: +width, height: +height }, deviceScaleFactor: 1 });
page.on("console", (m) => m.type() === "error" && console.log("console error:", m.text()));
page.on("pageerror", (e) => console.log("page error:", e.message));
await page.goto(url, { waitUntil: "networkidle" });
await page.waitForTimeout(+wait);
if (click) {
  await page.click(click);
  if (until) await page.waitForSelector(until, { timeout: 90000 }).catch(() => console.log("never appeared:", until));
  await page.waitForTimeout(1800);
}
await page.screenshot({ path: out });
await browser.close();
