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
await page.waitForSelector(".example", { timeout: 60000 });
await page.click(".example");
await page.waitForSelector(".answer .verdict", { timeout: 120000 });
const count = Number(process.env.STOPS ?? 10);
for (let i = 0; i < count; i++) {
  if (i > 0) await page.locator("body").press("ArrowRight");
  await page.waitForSelector(".answer .verdict", { timeout: 120000 });
  await page.waitForTimeout(5200);
  const seen = await page.evaluate(() => ({
    state: document.querySelector(".verdict .pill")?.textContent,
    value: document.querySelector(".figure")?.textContent,
    question: document.querySelector(".asked")?.textContent?.slice(0, 70),
    marks: document.querySelectorAll(".mark.count").length,
    rooms: document.querySelectorAll(".mark.room").length,
    tags3d: document.querySelectorAll(".tag3d").length,
    listed: document.querySelectorAll(".contact button, .chips button, .rows li").length,
  }));
  console.log(i + 1, JSON.stringify(seen));
  if (i === 0) {  // a click on a mark leaves it out; "Put back" restores it
    await page.locator(".mark.count .dot circle").first().click({ force: true });  // marks can overlap: the top one takes it
    const off = await page.locator(".mark.count.off").count();
    await page.getByRole("button", { name: "Put back" }).click();
    const back = await page.locator(".mark.count.off").count();
    if (off !== 1 || back !== 0) problems.push(`clicking a mark did not leave it out and put it back (${off}, ${back})`);
  }
  if (out) await page.screenshot({ path: `${out}/stop-${String(i + 1).padStart(2, "0")}.png` });
}
console.log(problems.length ? `PROBLEMS:\n${problems.join("\n")}` : "no console errors, no failed requests");
await browser.close();
