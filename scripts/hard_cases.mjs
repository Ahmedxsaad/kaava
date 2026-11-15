#!/usr/bin/env node
// The hard cases (examples/hard_cases.json), asked in the app one after another, live, and filmed.
//
// What it does, in one run:
//   1. opens the app in a headless Chrome and, for each case, opens the drawing, types the question
//      and waits for the answer, taking pictures of the page all the while;
//   2. takes each answer from the app's own request (the page's /api/ask reply: nothing is asked twice),
//      holds it against what the case expects (counts exact, measures within 2%, a refusal where one is
//      due) and writes the lot to results/hard_cases/<time>.json;
//   3. joins the pictures into a video. The time spent waiting for an answer is shown faster, never
//      cut: the band under the app shows the real time elapsed while it waits.
//
// Run it against a server started with KAAVA_FRESH=1, so that no answer is a replay of a kept one:
//   KAAVA_FRESH=1 BLUEPRINTS_UI_BUDGET_EUR=3 ./bp serve --port 8011
//   node scripts/hard_cases.mjs --base=http://127.0.0.1:8011 --out=docs/hard-cases.mp4
// Options: --only=id,id (some cases), --no-video (the run and its log only), --replay (allow a server
// that is not in fresh mode; the log then says which answers were shown again).
// It pays for model calls: about EUR 0.60 for the twelve cases.

import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const args = Object.fromEntries(process.argv.slice(2).map((a) => { const [k, ...v] = a.replace(/^--/, "").split("="); return [k, v.length ? v.join("=") : "1"]; }));
const BASE = (args.base ?? "http://127.0.0.1:8011").replace(/\/$/, "");
const OUT = path.resolve(ROOT, args.out ?? "docs/hard-cases.mp4");
const FRAMES = path.resolve(ROOT, args.frames ?? "cache/hard_cases_frames");
const CHROME = process.env.CHROME ?? "/usr/bin/google-chrome-stable";
const WIDTH = 1600, HEIGHT = 900, BAND = 58;
const WAIT_SHOWN_S = 4.5;      // however long an answer takes, its wait fills at most this much of the video
const PER_CASE_LIMIT_S = 600;  // an answer that has not come by then is logged as not having come

const { chromium } = await import(`${ROOT}/web/node_modules/playwright-core/index.mjs`);
const all = JSON.parse(readFileSync(`${ROOT}/examples/hard_cases.json`, "utf8"));
const cases = args.only ? all.filter((c) => args.only.split(",").includes(c.id)) : all;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const clock = (ms) => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`;
const euro = (v) => (v === null || v === undefined ? "cost not recorded" : v === 0 ? "€0" : v < 0.01 ? `€${v.toFixed(4)}` : `€${v.toFixed(3)}`);
const UNITS = { m2: " m²", m3: " m³", m: " m", ft2: " ft²", in: " in", mm: " mm", count: "" };
const shown = (value, unit) => (value === null || value === undefined ? "no number" : `${typeof value === "number" && !Number.isInteger(value) ? value.toFixed(2) : value}${UNITS[unit] ?? (unit ? ` ${unit}` : "")}`);

const stats = await (await fetch(`${BASE}/api/stats`)).json();
if (!stats.fresh_mode && !args.replay) {
  console.error("This server shows kept answers again. Start it with KAAVA_FRESH=1 so each case is worked out during the run, or pass --replay.");
  process.exit(2);
}
const commit = execFileSync("git", ["-C", ROOT, "rev-parse", "--short", "HEAD"]).toString().trim();
const filming = !args["no-video"];
if (filming) { rmSync(FRAMES, { recursive: true, force: true }); mkdirSync(FRAMES, { recursive: true }); }

// ------------------------------------------------------------------ the page, and what is drawn over it

const browser = await chromium.launch({ executablePath: CHROME, args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: WIDTH, height: HEIGHT } });
// The app keeps to the top of the picture; a band under it, which is not part of the app, says which
// case is running and how long the wait really is. The examples' own caption is reduced to its title:
// its note quotes the answer kept with the app, and this run works each answer out again.
await page.addInitScript(([band]) => {
  const make = () => {
    if (document.getElementById("rec-band")) return;
    const style = document.createElement("style");
    style.textContent = `
      #root { height: calc(100% - ${band}px) !important; }
      .board header .caption span, .board header .caption i, .board header .caption button { display: none !important; }
      #rec-band { position: fixed; left: 0; right: 0; bottom: 0; height: ${band}px; z-index: 9000; display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 22px; padding: 0 26px; background: #0b1f0d; color: #eef3ee; font: 500 15px/1.25 system-ui, sans-serif; }
      #rec-band .l { font-weight: 700; letter-spacing: .08em; font-size: 13px; color: #9fd8a3; white-space: nowrap; }
      #rec-band .m small { display: block; font-size: 12px; letter-spacing: .06em; text-transform: uppercase; color: #a9b8aa; }
      #rec-band .m b { font-weight: 600; font-size: 16.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block; }
      #rec-band .r { font-variant-numeric: tabular-nums; white-space: nowrap; color: #ffd45e; font-weight: 600; }
      #rec-band .r.done { color: #8be28f; }
      #rec-card { position: fixed; inset: 0; z-index: 9100; display: none; place-items: center; background: #0b1f0d; color: #eef3ee; font: 400 20px/1.5 system-ui, sans-serif; }
      #rec-card.on { display: grid; }
      #rec-card .in { width: min(1320px, 92%); }
      #rec-card h1 { font-size: 46px; line-height: 1.12; margin: 0 0 18px; font-weight: 700; letter-spacing: -.01em; }
      #rec-card p { margin: 0 0 12px; color: #cfdacf; max-width: 60ch; }
      #rec-card p b { color: #fff; }
      #rec-card table { width: 100%; border-collapse: collapse; font-size: 15.5px; line-height: 1.3; font-variant-numeric: tabular-nums; }
      #rec-card th { text-align: left; font-weight: 500; color: #a9b8aa; font-size: 12.5px; letter-spacing: .05em; text-transform: uppercase; padding: 0 12px 8px 0; border-bottom: 1px solid #2c4a2f; }
      #rec-card td { padding: 7px 12px 7px 0; border-bottom: 1px solid #1c3a1f; vertical-align: top; }
      #rec-card td.n { text-align: right; white-space: nowrap; }
      #rec-card .ok { color: #8be28f; font-weight: 600; } #rec-card .no { color: #ff9a8a; font-weight: 600; } #rec-card .open { color: #ffd45e; }
      #rec-card h2 { font-size: 27px; margin: 0 0 14px; font-weight: 700; }
      #rec-card .foot { margin-top: 14px; font-size: 14.5px; color: #a9b8aa; max-width: none; }`;
    document.head.appendChild(style);
    const bar = document.createElement("div");
    bar.id = "rec-band";
    bar.innerHTML = `<span class="l"></span><span class="m"><small></small><b></b></span><span class="r"></span>`;
    const card = document.createElement("div");
    card.id = "rec-card";
    card.innerHTML = `<div class="in"></div>`;
    document.body.append(bar, card);
  };
  if (document.body) make(); else document.addEventListener("DOMContentLoaded", make);
}, [BAND]);

const frames = [];
async function shoot(mode = "live") {
  if (!filming) return;
  const file = `${String(frames.length).padStart(5, "0")}.jpg`;
  await page.screenshot({ path: `${FRAMES}/${file}`, type: "jpeg", quality: 90 });
  frames.push({ file, t: Date.now(), mode });
}
async function film(ms, mode = "live") {
  const end = Date.now() + ms;
  if (!filming) return sleep(Math.min(ms, 300));
  while (Date.now() < end) await shoot(mode);
}
const band = (left, small, title, right, done = false) => page.evaluate(([l, s, t, r, d]) => {
  const bar = document.getElementById("rec-band");
  if (!bar) return;
  bar.querySelector(".l").textContent = l;
  bar.querySelector(".m small").textContent = s;
  bar.querySelector(".m b").textContent = t;
  bar.querySelector(".r").textContent = r;
  bar.querySelector(".r").classList.toggle("done", d);
}, [left, small, title, right, done]);
const card = (html) => page.evaluate((h) => {
  const el = document.getElementById("rec-card");
  el.querySelector(".in").innerHTML = h;
  el.classList.toggle("on", Boolean(h));
}, html);

// ------------------------------------------------------------------ the scoring rule

function judge(c, a) {
  const e = c.expect;
  if (!a) return { ok: false, verdict: "no answer came back" };
  if (a.failed) return { ok: false, verdict: "the model call failed: nothing computed" };  // not a refusal: a failure
  if (e.kind === "refusal") return a.answer === null ? { ok: true, verdict: "refused, as it should" } : { ok: false, verdict: `answered ${shown(a.answer, a.unit)} where it should refuse` };
  if (a.answer === null) return { ok: e.kind === "none" ? null : false, verdict: "gave no number" };
  if (e.kind === "dataset") {
    const status = a.reference?.status;
    return status === "correct" ? { ok: true, verdict: `matches the dataset's answer (${shown(a.reference.answer, a.reference.unit)})` }
      : { ok: status ? false : null, verdict: status ? `differs from the dataset's answer (${shown(a.reference.answer, a.reference.unit)})` : "no dataset answer to hold it against" };
  }
  if (e.kind === "none") return { ok: null, verdict: "no key to hold it against" };
  if ((a.unit ?? "") !== e.unit) return { ok: false, verdict: `in ${a.unit}, the key is in ${e.unit}` };
  const off = Number(a.answer) - e.value;
  const ok = e.unit === "count" ? off === 0 : Math.abs(off) <= 0.02 * Math.abs(e.value);
  const by = e.unit === "count" ? `${off > 0 ? "+" : ""}${off}` : `${(100 * off / e.value).toFixed(2)}%`;
  return { ok, verdict: off === 0 ? `equals ${shown(e.value, e.unit)}` : `${by} from ${shown(e.value, e.unit)}`, off };
}

// ------------------------------------------------------------------ the run

let latest = null;  // the app's last full reply to /api/ask
page.on("response", async (res) => {
  if (!res.url().endsWith("/api/ask") || res.status() !== 200) return;
  try { const body = await res.json(); if (!body.pending) latest = body; } catch { /* a reply cut short is not an answer */ }
});

const started = new Date();
const rows = [];
await page.goto(`${BASE}/#d=${encodeURIComponent(cases[0].drawing)}&v=${cases[0].view ?? "plan"}`, { waitUntil: "domcontentloaded" });
await page.waitForSelector("form.composer input", { timeout: 60000 });
await sleep(1500);
await band("", "", "", "");
await card(`<h1>Kaava · the hard cases, asked live</h1>
  <p>${cases.length} questions of the kinds Stoa said it may ask: one size given and the rest worked out, baseboards, slab areas by type with their holes taken out, door counts, piping plans.</p>
  <p><b>Every answer in this video is worked out while it is filmed.</b> None is shown again from a kept answer. Waiting is shown faster and never cut: the band at the bottom shows the real time.</p>
  <p>${started.toISOString().slice(0, 10)} · commit ${commit}</p>`);
await film(5200);
await card("");

let open = cases[0].drawing;
for (const [i, c] of cases.entries()) {
  const tag = `HARD CASE ${i + 1} / ${cases.length}`;
  await band(tag, `Stoa asked for: ${c.asked}`, c.label, "");
  if (c.drawing !== open) {
    const opened = page.waitForResponse((r) => r.url().includes("/api/drawing?") && decodeURIComponent(r.url()).includes(c.drawing), { timeout: 60000 }).catch(() => null);
    await page.evaluate((h) => { window.location.hash = h; }, `d=${encodeURIComponent(c.drawing)}&v=${c.view ?? "plan"}`);
    await opened;
    open = c.drawing;
    await film(2200);
  } else {
    await film(900);
  }
  // The question, typed.
  latest = null;
  await page.locator("form.composer input").click();
  for (let k = 0; k < c.question.length; k += 9) {
    await page.keyboard.type(c.question.slice(k, k + 9));
    await shoot();
  }
  await film(500);
  const t0 = Date.now();
  await page.keyboard.press("Enter");
  // The wait: real time on the band, pictures taken sparsely (they are shown faster).
  let state = { arrived: false, failed: "" };
  while (Date.now() - t0 < PER_CASE_LIMIT_S * 1000) {
    state = await page.evaluate((q) => {
      const asked = (document.querySelector(".answer .asked")?.textContent ?? "").trim();
      const lead = document.querySelector(".answer .lead h2")?.textContent ?? "";
      const failed = /went wrong|can't be opened/i.test(lead) ? (document.querySelector(".answer .lead p")?.textContent ?? lead) : "";
      return { arrived: asked === q && !document.querySelector(".answer.stale") && Boolean(document.querySelector(".result-card")), failed };
    }, c.question.trim());
    if (state.arrived || state.failed) break;
    await band(tag, `Stoa asked for: ${c.asked}`, c.label, `working it out live · ${clock(Date.now() - t0)} real time, shown faster`);
    await shoot("wait");
    await sleep(380);
  }
  const wall = (Date.now() - t0) / 1000;
  if (!state.arrived && !state.failed) {
    // The app takes one question at a time and is still waiting for this one: the page is opened anew,
    // or the next question could not be asked at all.
    await page.goto(`${BASE}/?after=${i}#d=${encodeURIComponent(c.drawing)}&v=${c.view ?? "plan"}`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("form.composer input", { timeout: 60000 });
    await sleep(1500);
  }
  for (let k = 0; k < 20 && !latest && state.arrived; k++) await sleep(100);
  const a = state.arrived ? latest : null;
  const verdict = judge(c, a);
  const row = {
    id: c.id, asked: c.asked, label: c.label, drawing: c.drawing, question: c.question, expect: c.expect,
    answer: a?.answer ?? null, unit: a?.unit ?? null, state: a?.state ?? null, engine: a?.engine ?? null, method: a?.method ?? null,
    cost_eur: a?.cost_eur ?? null, spent_now_eur: a?.spent_now_eur ?? null, replayed: a?.replayed ?? null, failed: a?.failed ?? null,
    latency_ms: a?.latency_ms ?? null, wall_s: Math.round(wall * 10) / 10, evidence_items: a?.evidence?.length ?? 0,
    reason: a ? (a.answer === null ? a.reason || a.explanation : a.explanation) : (state.failed || "no answer within the time allowed"),
    ok: verdict.ok, verdict: verdict.verdict,
  };
  rows.push(row);
  console.log(`${String(i + 1).padStart(2)}. ${c.id.padEnd(22)} ${shown(row.answer, row.unit).padEnd(12)} ${String(row.state).padEnd(9)} ${euro(row.cost_eur).padEnd(9)} ${String(row.wall_s).padStart(6)} s  ${row.replayed ? "SHOWN AGAIN " : ""}${row.ok === true ? "OK" : row.ok === false ? "NOT OK" : "no key"} · ${row.verdict}`);
  const said = a ? `${a.answer === null ? "refused" : "answered"} in ${wall < 10 ? wall.toFixed(1) : Math.round(wall)} s · ${a.replayed ? "shown again" : `inference ${euro(a.cost_eur)}`}` : "no answer";
  await band(tag, `Stoa asked for: ${c.asked}`, c.label, said, true);
  // The answer: its evidence is marked one item at a time; then the row that says what it cost.
  const until = Date.now() + 9000;
  while (Date.now() < until) {
    await shoot();
    if (await page.evaluate(() => Boolean(document.querySelector(".result-card.settled")))) break;
  }
  await film(2600);
  await page.evaluate(() => document.querySelector(".compare")?.scrollIntoView({ behavior: "smooth", block: "center" }));
  await film(2600);
  await page.evaluate(() => document.querySelector(".reading .scroll")?.scrollTo({ top: 0 }));
}

// ------------------------------------------------------------------ the log

const total = rows.reduce((sum, r) => sum + (r.cost_eur ?? 0), 0);
const summary = {
  cases: rows.length,
  with_a_key: rows.filter((r) => r.ok !== null).length,
  as_the_key_says: rows.filter((r) => r.ok === true).length,
  not_as_the_key_says: rows.filter((r) => r.ok === false).length,
  without_a_key: rows.filter((r) => r.ok === null).length,
  shown_again: rows.filter((r) => r.replayed).length,
  inference_eur: Math.round(total * 10000) / 10000,
  seconds: Math.round(rows.reduce((sum, r) => sum + r.wall_s, 0)),
};
const stamp = started.toISOString().replace(/[-:]/g, "").replace("T", "-").slice(0, 15);
const logDir = `${ROOT}/results/hard_cases`;
mkdirSync(logDir, { recursive: true });
const logFile = `${logDir}/${stamp}.json`;
writeFileSync(logFile, JSON.stringify({
  what: "The hard cases (examples/hard_cases.json), asked in the app one after another (scripts/hard_cases.mjs)",
  when: started.toISOString(), commit, server: BASE, fresh: Boolean(stats.fresh_mode),
  rule: "a count must equal the key, a measure must be within 2% of it, a refusal is right where one is due; 'no key' means nobody worked the answer out by hand",
  video: filming ? path.relative(ROOT, OUT) : null, summary, rows,
}, null, 1));
console.log(`\n${summary.as_the_key_says} of ${summary.with_a_key} with a key as the key says, ${summary.without_a_key} without a key, ${summary.shown_again} shown again; inference ${euro(total)}; ${summary.seconds} s of answering`);
console.log(`log: ${path.relative(ROOT, logFile)}`);

// ------------------------------------------------------------------ the closing table, and the video

if (filming) {
  const esc = (s) => String(s).replace(/[&<>]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[ch]));
  const body = rows.map((r, i) => `<tr><td class="n">${i + 1}</td><td>${esc(r.label)}</td><td class="n">${esc(shown(r.answer, r.unit))}</td>
    <td class="${r.ok === true ? "ok" : r.ok === false ? "no" : "open"}">${esc(r.verdict)}</td>
    <td class="n">${esc(r.state === "verified" ? "Verified" : r.state === "check" ? "Check this" : r.state === "declined" ? "Refused" : "—")}</td>
    <td class="n">${esc(euro(r.cost_eur))}</td><td class="n">${r.wall_s < 10 ? r.wall_s.toFixed(1) : Math.round(r.wall_s)} s</td></tr>`).join("");
  await band("", "", "", "");
  await card(`<h2>What came back, all ${rows.length}, as they came</h2>
    <table><thead><tr><th></th><th>Case</th><th>Answer</th><th>Against the key</th><th>State</th><th>Inference</th><th>Time</th></tr></thead><tbody>${body}</tbody></table>
    <p class="foot">${summary.as_the_key_says} of the ${summary.with_a_key} cases that have something to be held against came out as it says${summary.not_as_the_key_says ? `, ${summary.not_as_the_key_says} did not` : ""}. ${summary.without_a_key} have no key: nobody counted or measured them by hand, so nothing is claimed for them (docs/BLIND-TRIAL.md). All ${rows.length} together: ${euro(total)} of model calls, ${clock(summary.seconds * 1000)} of answering. Log: results/hard_cases/${stamp}.json</p>`);
  await film(11000);
}
await browser.close();

if (filming) {
  // Each picture is shown for as long as it stood on the page, except the waits, which are shown faster.
  const lines = [];
  let i = 0;
  while (i < frames.length) {
    let j = i;
    while (j + 1 < frames.length && frames[j + 1].mode === frames[i].mode) j++;
    const real = frames.slice(i, j + 1).map((f, k) => ((frames[i + k + 1]?.t ?? f.t + 400) - f.t) / 1000);
    const sum = real.reduce((x, y) => x + y, 0);
    const speed = frames[i].mode === "wait" ? Math.max(1, sum / WAIT_SHOWN_S) : 1;
    real.forEach((d, k) => lines.push(`file '${FRAMES}/${frames[i + k].file}'\nduration ${Math.max(0.03, Math.min(d / speed, 0.7)).toFixed(4)}`));
    i = j + 1;
  }
  lines.push(`file '${FRAMES}/${frames.at(-1).file}'`);
  writeFileSync(`${FRAMES}/frames.txt`, lines.join("\n") + "\n");
  mkdirSync(path.dirname(OUT), { recursive: true });
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", `${FRAMES}/frames.txt`,
    "-vf", "fps=25,format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "24", "-movflags", "+faststart", OUT], { stdio: "inherit" });
  const seconds = execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", OUT]).toString().trim();
  console.log(`video: ${path.relative(ROOT, OUT)} (${Math.round(Number(seconds))} s, ${frames.length} pictures)`);
  if (existsSync(OUT) && !args["keep-frames"]) rmSync(FRAMES, { recursive: true, force: true });
}
