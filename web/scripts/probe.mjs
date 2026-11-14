// Run a snippet in the page and print its result: node web/scripts/probe.mjs <url> <waitMs> "<js expression>"
import { chromium } from "playwright-core";

const [url, wait, expression] = process.argv.slice(2);
const browser = await chromium.launch({
  executablePath: process.env.CHROME ?? "/usr/bin/google-chrome-stable",
  args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 950 } });
page.on("console", (m) => m.type() === "error" && console.log("console error:", m.text()));
page.on("pageerror", (e) => console.log("page error:", e.message));
await page.goto(url, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(+wait);
console.log(JSON.stringify(await page.evaluate(expression), null, 1));
await browser.close();
