// Record the app walking through the pitch, timed to the narration clips:
//   node pitch/video/record.mjs <base url> <timings.json in> <marks.json out> <frames dir>
// Each segment: do its action, note when it started (seconds into the recording), hold for the clip's length.
// The picture is taken from Chrome's own screencast: one JPEG each time the page changes, with its time.
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(here, "../../web/package.json"));
const { chromium } = require("playwright-core");

const [base, timingsFile, marksFile, videoDir] = process.argv.slice(2);
const segments = JSON.parse(fs.readFileSync(timingsFile, "utf8"));
const tour = fs.readFileSync(path.join(here, "../../web/src/tour.ts"), "utf8");
const stops = [...tour.matchAll(/\{\s*label: "[^"]+",([\s\S]*?)\n  \},/g)].map((m) => ({
  drawing: /drawing: "([^"]+)"/.exec(m[1])[1], question: /question: "([^"]+)"/.exec(m[1])[1],
}));

const size = { width: 1920, height: 1080 };
const browser = await chromium.launch({
  executablePath: process.env.CHROME ?? "/usr/bin/google-chrome-stable",
  args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
});
const context = await browser.newContext({ viewport: size, deviceScaleFactor: 1 });
const page = await context.newPage();
// Chrome sends a picture only when the page changes, and can miss a change that lands between two
// pictures. A two-pixel mark that never stops moving keeps the pictures coming at a steady rate.
await page.addInitScript(() => {
  const tick = () => {
    if (!document.body) return void requestAnimationFrame(tick);
    const dot = document.createElement("i");
    dot.style.cssText = "position:fixed;right:0;bottom:0;width:2px;height:2px;opacity:.02;background:#000;z-index:2147483647;pointer-events:none";
    document.body.appendChild(dot);
    let n = 0;
    const move = () => { dot.style.transform = `translateX(${-(n++ % 2)}px)`; requestAnimationFrame(move); };
    move();
  };
  tick();
});
const started = Date.now();
const at = () => (Date.now() - started) / 1000;
const frames = [];
const cdp = await context.newCDPSession(page);
cdp.on("Page.screencastFrame", (frame) => {
  const file = path.join(videoDir, `f${String(frames.length).padStart(6, "0")}.jpg`);
  fs.writeFileSync(file, Buffer.from(frame.data, "base64"));
  frames.push({ file, t: at() });
  cdp.send("Page.screencastFrameAck", { sessionId: frame.sessionId }).catch(() => undefined);
});
await cdp.send("Page.startScreencast", { format: "jpeg", quality: 88, maxWidth: size.width, maxHeight: size.height, everyNthFrame: 2 });
const sleep = (s) => page.waitForTimeout(Math.max(0, s * 1000));

await page.goto(base, { waitUntil: "networkidle" });
await page.waitForSelector(".example", { timeout: 60000 });
await sleep(1.2);

const marks = [];
for (const seg of segments) {
  const begin = at();
  if (seg.stop) {
    const stop = stops[seg.stop - 1];
    const params = new URLSearchParams({ d: stop.drawing, q: stop.question, ...(seg.view ? { v: seg.view } : {}) });
    await page.evaluate((hash) => { window.location.hash = hash; }, params.toString());
  } else if (seg.go !== undefined) {
    await page.evaluate((hash) => { window.location.hash = hash; }, seg.go);
  }
  marks.push({ id: seg.id, start: begin, duration: seg.duration });
  const until = begin + seg.duration + (seg.pad ?? 0.5);
  if (seg.stop) await page.waitForSelector(".answer .verdict", { timeout: 120000 }).catch(() => undefined);
  if (seg.hover !== undefined) {
    await sleep(1.5);
    await page.locator(".mark.count .dot circle").nth(seg.hover).hover({ timeout: 3000 }).catch(() => undefined);
  }
  if (seg.leave_out) {
    await sleep(begin + seg.leave_out_after - at());
    for (const n of seg.leave_out) {
      await page.locator(".mark.count .dot circle").nth(n).click({ timeout: 3000 }).catch((e) => console.log("could not leave out", n, e.message.split("\n")[0]));
      await sleep(1.1);
    }
  }
  if (seg.scroll) {
    await sleep(begin + seg.scroll_after - at());
    await page.mouse.move(size.width / 2, size.height / 2);
    for (let i = 0; i < 22; i++) { await page.mouse.wheel(0, seg.scroll / 22); await sleep(0.04); }
  }
  await sleep(until - at());
}
const total = at();
await cdp.send("Page.stopScreencast").catch(() => undefined);
// A list ffmpeg can read: each picture and how long it stays on screen.
const list = frames.map((f, i) => `file '${f.file}'\nduration ${Math.max(0.001, (frames[i + 1]?.t ?? total) - f.t).toFixed(4)}`);
list.push(`file '${frames[frames.length - 1].file}'`);
const video = path.join(videoDir, "frames.txt");
fs.writeFileSync(video, list.join("\n") + "\n");
fs.writeFileSync(marksFile, JSON.stringify({ video, first: frames[0].t, total, marks }, null, 1));
console.log(`recorded ${total.toFixed(1)} s, ${frames.length} pictures`);
await browser.close();
