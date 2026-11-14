import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnswerPanel } from "./AnswerPanel";
import { api, type AskResult, type DrawingDetail, type Evidence, euro, formatValue, type Source, SOURCE_NAMES } from "./api";
import { Numbers } from "./Numbers";
import { Picker } from "./Picker";
import * as sound from "./sound";
import { Stamp } from "./Stamp";
import { TOUR } from "./tour";
import { anchorOf, type Theme, Viewer2D } from "./Viewer2D";
import { type Highlight, Viewer3D } from "./Viewer3D";

type Mode = "plan" | "3d" | "both";
type Page = "ask" | "numbers";

interface Route {
  drawing: string | null;
  question: string;
  page: Page;
  view?: Mode | null;
}

function readRoute(): Route {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  return {
    drawing: params.get("d"),
    question: params.get("q") ?? "",
    page: params.get("p") === "numbers" ? "numbers" : "ask",
    view: (["plan", "3d", "both"] as const).find((m) => m === params.get("v")) ?? null,
  };
}

function writeRoute(route: Route) {
  const params = new URLSearchParams();
  if (route.page === "numbers") params.set("p", "numbers");
  if (route.drawing) params.set("d", route.drawing);
  if (route.question) params.set("q", route.question);
  if (route.view && route.view !== "plan") params.set("v", route.view);
  const next = `#${params.toString()}`;
  if (next !== window.location.hash) window.history.replaceState(null, "", next);
}

function remembered<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = window.localStorage.getItem(key);
    return allowed.includes(value as T) ? (value as T) : fallback;
  } catch {
    return fallback;
  }
}

function remember(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // a private window: the choice simply lasts for this visit
  }
}

// Evidence that stands for one unit of the answer (a counted item or a measured part).
function countable(evidence: Evidence[]): Evidence[] {
  return evidence.filter((e) => e.kind !== "formula" && e.kind !== "context");
}

function measureOf(item: Evidence): number | null {
  return item.area_m2 ?? item.volume_m3 ?? item.length_m ?? null;
}

const ZONES = ["A", "B", "C", "D", "E", "F", "G", "H"];

export function App() {
  const [route] = useState(readRoute);
  const [page, setPage] = useState<Page>(route.page);
  const [theme, setTheme] = useState<Theme>(() => {
    const asked = new URLSearchParams(window.location.hash.replace(/^#/, "")).get("t");  // a link can ask for one
    return asked === "paper" || asked === "blueprint" ? asked : remembered("blueprints.theme", ["blueprint", "paper"] as const, "blueprint");
  });
  const [audible, setAudible] = useState(() => remembered("blueprints.sound", ["on", "off"] as const, "on") === "on");
  const [sources, setSources] = useState<Source[]>([]);
  const [detail, setDetail] = useState<DrawingDetail | null>(null);
  const [question, setQuestion] = useState(route.question);
  const [result, setResult] = useState<AskResult | null>(null);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("plan");
  const [progress, setProgress] = useState(1);  // how far the reading line has crossed the evidence, 0 to 1
  const [excluded, setExcluded] = useState<Set<number>>(new Set());
  const [hover, setHover] = useState<number | null>(null);
  const [focus, setFocus] = useState<number | null>(null);
  const [picking, setPicking] = useState(false);
  const [touring, setTouring] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [focusKey, setFocusKey] = useState("");
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  // What this session's answers cost, and what the vision model alone costs for the same questions.
  const [meter, setMeter] = useState({ answers: 0, spent: 0, alone: 0 });
  const [aloneAverage, setAloneAverage] = useState<number | null>(null);
  const asked = useRef(0);
  const aloneRef = useRef<number | null>(null);
  const pending = useRef<string | null>(route.question || null);
  const wanted = useRef<Mode | null>(route.view ?? null);  // a view asked for in the link wins once
  const stamped = useRef("");

  const ask = useCallback(async (drawing: DrawingDetail, text: string, useModel = false) => {
    const clean = text.trim();
    if (!clean) return;
    const ticket = ++asked.current;
    setAsking(true);
    setError(null);
    setSuggesting(false);
    setQuestion(clean);
    writeRoute({ drawing: drawing.id, question: clean, page: "ask" });
    try {
      const answer = await api.ask(drawing.id, clean, useModel);
      if (ticket !== asked.current) return;
      setExcluded(new Set());
      setHover(null);
      setFocus(null);
      setProgress(0);
      setResult(answer);
      setMeter((m) => ({
        answers: m.answers + 1,
        spent: m.spent + (answer.replayed_from ? 0 : answer.cost_eur),
        alone: m.alone + (answer.baseline?.cost_eur ?? aloneRef.current ?? 0),
      }));
      setFocusKey(`${drawing.id}|${clean}|${ticket}`);
      const has2d = drawing.view.kind !== "none";
      const onPlan = answer.evidence.some((e) => e.bbox || e.points || e.x !== undefined);
      const in3d = answer.evidence.some((e) => e.kind === "element");
      const forced = wanted.current;
      wanted.current = null;
      setMode((current) => forced ?? (in3d && !onPlan ? (has2d && current === "both" ? "both" : "3d") : onPlan && current === "3d" ? "plan" : current));
    } catch (e) {
      if (ticket === asked.current) {
        setResult(null);
        setError((e as Error).message);
      }
    } finally {
      if (ticket === asked.current) setAsking(false);
    }
  }, []);

  const open = useCallback(async (id: string, then?: string, view?: Mode) => {
    if (view) wanted.current = view;
    setPicking(false);
    setError(null);
    asked.current++;
    try {
      const next = await api.drawing(id);
      setDetail(next);
      setResult(null);
      setProgress(1);
      setExcluded(new Set());
      setFocus(null);
      setFocusKey("");
      setQuestion(then ?? "");
      setMode(next.view.kind === "none" ? "3d" : wanted.current ?? "plan");
      writeRoute({ drawing: id, question: then ?? "", page: "ask" });
      if (then) void ask(next, then);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [ask]);

  useEffect(() => {
    api.stats().then((stats) => {
      const run = stats.runs.find((r) => r.method.startsWith("vlm-image-only") && r.sample === "dev_baseline_200");
      const average = run?.rows.find((r) => r.scope === "overall")?.eur_per_question ?? null;
      aloneRef.current = average;
      setAloneAverage(average);
    }).catch(() => undefined);
    api.sources().then(async (list) => {
      setSources(list);
      const first = route.drawing ?? TOUR[0]?.drawing;
      if (first) await open(first, pending.current ?? undefined);
      pending.current = null;
    }).catch((e: Error) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Evidence is marked one item at a time, in the order a line sweeping the sheet from the
  // left meets them; the counter follows.
  const units = useMemo(() => (result ? countable(result.evidence) : []), [result]);
  const sequence = useMemo(() => {
    const placed = units.map((e) => ({ n: e.n!, x: anchorOf(e)?.[0] ?? null }));
    const onSheet = placed.length > 0 && placed.every((p) => p.x !== null);
    return { order: (onSheet ? [...placed].sort((a, b) => a.x! - b.x!) : placed), onSheet };
  }, [units]);
  useEffect(() => {
    if (!result || !units.length) {
      setProgress(1);
      return;
    }
    // By the clock, not by counting frames: a busy page must not slow the reading down.
    const duration = Math.min(2300, Math.max(900, units.length * 70));
    const began = performance.now() + 450;
    setProgress(0);
    const timer = window.setInterval(() => {
      const p = Math.min(1, Math.max(0, (performance.now() - began) / duration));
      setProgress(p);
      if (p >= 1) window.clearInterval(timer);
    }, 16);
    return () => window.clearInterval(timer);
  }, [result, units.length]);
  // The line moves across the evidence at a steady pace; an item is marked as the line passes it.
  const reading = useMemo(() => {
    const n = sequence.order.length;
    if (!n || progress >= 1) return { revealed: n, at: null as number | null };
    if (!sequence.onSheet) return { revealed: Math.floor(progress * n), at: null };
    const lo = sequence.order[0].x!, hi = sequence.order[n - 1].x!;
    const pad = (hi - lo) * 0.05 + 2;
    const at = lo - pad + progress * (hi - lo + 2 * pad);
    let revealed = 0;
    while (revealed < n && sequence.order[revealed].x! <= at) revealed++;
    return { revealed, at };
  }, [sequence, progress]);
  const revealed = reading.revealed;
  const visible = useMemo(() => new Set(sequence.order.slice(0, revealed).map((p) => p.n)), [sequence, revealed]);
  const settled = !result || progress >= 1;
  const sweepAt = settled ? null : reading.at;

  // The pen ticks as items are marked; the stamp lands when the last one is in.
  useEffect(() => {
    if (audible && result && revealed > 0 && revealed <= units.length) sound.tick(revealed / units.length);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revealed]);
  useEffect(() => {
    if (!result || !settled || stamped.current === focusKey) return;
    stamped.current = focusKey;
    if (!audible) return;
    const timer = window.setTimeout(() => sound.stamp(result.state), 230);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result, settled, focusKey]);

  // "Make blueprints talk", literally: read the verdict and the number aloud once the evidence has landed.
  useEffect(() => {
    if (!speaking || !result || !settled || !("speechSynthesis" in window)) return;
    const verdict = result.state === "verified" ? "Verified." : result.state === "check" ? "Check this." : "I can't answer that reliably.";
    const unit = result.unit === "m2" ? "square metres" : result.unit === "m3" ? "cubic metres" : result.unit === "m" ? "metres" : "";
    const value = result.answer === null ? result.reason : `${formatValue(result.answer, result.unit)} ${unit}`;
    const utterance = new SpeechSynthesisUtterance(`${verdict} ${value}`);
    utterance.lang = "en-GB";
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result, settled, speaking]);

  const tracksCount = !!result && result.unit === "count" && typeof result.answer === "number" && units.length === result.answer;
  const parts = units.map(measureOf);
  const tracksSum = !!result && typeof result.answer === "number" && result.unit !== "count" && units.length > 0 &&
    parts.every((p) => p !== null) && Math.abs(parts.reduce((s: number, p) => s + (p ?? 0), 0) - result.answer) < 0.02 * Math.max(1, result.answer);
  const shown = useMemo(() => {
    if (!result || settled) return result?.answer ?? null;
    if (tracksCount) return revealed;
    if (tracksSum) return units.filter((e) => visible.has(e.n!)).reduce((s, e) => s + (measureOf(e) ?? 0), 0);
    return result.answer;
  }, [result, settled, tracksCount, tracksSum, revealed, units, visible]);
  const adjusted = useMemo(() => {
    if (!result || !excluded.size || typeof result.answer !== "number") return null;
    if (tracksCount) return result.answer - excluded.size;
    if (tracksSum) return result.answer - units.filter((e) => excluded.has(e.n!)).reduce((s, e) => s + (measureOf(e) ?? 0), 0);
    return null;
  }, [result, excluded, tracksCount, tracksSum, units]);

  const toggle = useCallback((n: number) => {
    setExcluded((prev) => {
      const next = new Set(prev);
      if (next.has(n)) next.delete(n); else next.add(n);
      return next;
    });
  }, []);

  const drawable = useMemo(() => (result ? result.evidence.filter((e) => e.kind !== "context" && (e.bbox || e.points || e.x !== undefined)) : []), [result]);
  const context = useMemo(() => (result ? result.evidence.filter((e) => e.kind === "context" && e.bbox) : []), [result]);
  const highlights: Highlight[] = useMemo(() => {
    if (!result) return [];
    return units.flatMap((e) => {
      const id = e.kind === "element" ? e.id : e.ref;
      const tone = e.kind === "polygon" || e.cls === "IfcSpace" ? "area" as const : "count" as const;
      return id ? [{ id, n: e.n!, tone }] : [];
    });
  }, [result, units]);
  // Outdoor rooms a sum left out, shown hatched: only when the answer is a sum over rooms.
  const leftOut = useMemo(() => {
    if (!detail || !result || result.unit !== "m2") return [];
    const used = new Set(result.evidence.filter((e) => e.kind === "polygon").map((e) => e.ref));
    if (used.size < 2 || !/indoor|without|ohne|excluding/i.test(result.interpreted ?? "")) return [];
    const flats = new Set(detail.rooms.filter((r) => used.has(r.ref)).map((r) => r.unit));
    return detail.rooms.filter((r) => r.outdoor && !used.has(r.ref) && flats.has(r.unit));
  }, [detail, result]);

  // Keys. While inspecting: arrows step through the items, X leaves one out, Escape ends.
  // Otherwise the arrows step through the examples, for presenting.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (page !== "ask" || picking || e.altKey || e.ctrlKey || e.metaKey) return;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA")) return;
      if (focus !== null) {
        const at = units.findIndex((u) => u.n === focus);
        if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
          e.preventDefault();
          setFocus(units[(at + (e.key === "ArrowRight" ? 1 : -1) + units.length) % units.length].n!);
        } else if (e.key === "x" || e.key === "X") toggle(focus);
        else if (e.key === "Escape") setFocus(null);
        return;
      }
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const at = TOUR.findIndex((stop) => stop.drawing === detail?.id && stop.question === question);
      if (at === -1 && e.key === "ArrowLeft") return;
      const next = TOUR[(at + (e.key === "ArrowRight" ? 1 : -1) + TOUR.length) % TOUR.length];
      e.preventDefault();
      void open(next.drawing, next.question, next.view);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [page, picking, detail, question, open, focus, units, toggle]);

  // A few of the dataset's questions about this drawing to start from: one count, one area, one in another language.
  const starters = useMemo(() => {
    if (!detail) return [];
    const english = (t: string) => /^(How|What|Which|On sheet)\b/.test(t);
    const list = detail.suggested;
    const picks = [
      list.find((q) => q.type === "count" && english(q.question)),
      list.find((q) => q.type === "area" && english(q.question)),
      list.find((q) => !english(q.question)),
      list.find((q) => q.type === "text"),
    ].filter((q): q is NonNullable<typeof q> => !!q).map((q) => q.question);
    return [...new Set(picks)].slice(0, 3);
  }, [detail]);

  const has2d = !!detail && detail.view.kind !== "none";
  const has3d = !!detail?.mesh;
  const effective: Mode = !has2d ? "3d" : !has3d ? "plan" : mode;
  const matches = useMemo(() => {
    if (!detail) return [];
    const needle = question.trim().toLowerCase();
    const list = detail.suggested.filter((s) => !needle || s.question.toLowerCase().includes(needle));
    return list.length ? list : detail.suggested;
  }, [detail, question]);

  const listen = () => {
    const Recognition = (window as unknown as Record<string, any>).SpeechRecognition ?? (window as unknown as Record<string, any>).webkitSpeechRecognition;
    if (!Recognition || !detail) return;
    const recognition = new Recognition();
    recognition.lang = navigator.language || "en-US";
    recognition.interimResults = false;
    recognition.onresult = (event: any) => {
      const text = event.results[0][0].transcript as string;
      setQuestion(text);
      void ask(detail, text);
    };
    recognition.onend = () => setListening(false);
    recognition.onerror = () => setListening(false);
    setListening(true);
    recognition.start();
  };
  const canListen = typeof window !== "undefined" && ("SpeechRecognition" in window || "webkitSpeechRecognition" in window);
  const go = (next: Page) => { setPage(next); writeRoute({ drawing: detail?.id ?? null, question, page: next }); };
  const groundUrl = detail && has3d && (detail.view.kind === "plan" || detail.view.kind === "page")
    ? `/api/ground?drawing=${encodeURIComponent(detail.id)}` : null;

  return (
    <div className={`app theme-${theme}`}>
      <header className="top">
        <div className="brand">
          <svg viewBox="0 0 32 32" aria-hidden>
            <rect width="32" height="32" rx="6" />
            <path d="M8 23V9h9l7 7v7z" />
            <circle cx="16" cy="17" r="3" />
          </svg>
          <span>Blueprints Talk</span>
        </div>
        <nav className="pages">
          <button className={page === "ask" ? "on" : ""} onClick={() => go("ask")}>Ask</button>
          <button className={page === "numbers" ? "on" : ""} onClick={() => go("numbers")}>Numbers</button>
        </nav>
        <div className="spacer" />
        {page === "ask" && meter.answers > 0 && (
          <div className="meter" title={aloneAverage != null ? `Where the vision model was not logged for a question, its measured average of ${euro(aloneAverage)} is used` : undefined}>
            <span><b>{meter.answers}</b> answer{meter.answers === 1 ? "" : "s"}</span>
            <span>cost <b>{euro(meter.spent)}</b></span>
            {meter.alone > 0 && <span>vision model alone <b>{euro(meter.alone)}</b></span>}
          </div>
        )}
        {page === "ask" && (
          <>
            <div className="switch" role="group" aria-label="How the drawing is shown">
              <button className={theme === "blueprint" ? "on" : ""} onClick={() => { setTheme("blueprint"); remember("blueprints.theme", "blueprint"); }}>Blueprint</button>
              <button className={theme === "paper" ? "on" : ""} onClick={() => { setTheme("paper"); remember("blueprints.theme", "paper"); }}>Paper</button>
            </div>
            <button
              className={`icon${audible ? " on" : ""}`}
              onClick={() => { setAudible(!audible); remember("blueprints.sound", audible ? "off" : "on"); }}
              aria-pressed={audible}
              title={audible ? "Sounds on" : "Sounds off"}
            >
              <svg viewBox="0 0 24 24" aria-hidden>
                <path d="M9 18V6l10-2v12" fill="none" strokeWidth="2" strokeLinejoin="round" />
                <circle cx="7" cy="18" r="2.4" /><circle cx="17" cy="16" r="2.4" />
                {!audible && <path d="M4 4l16 16" fill="none" strokeWidth="2.2" strokeLinecap="round" />}
              </svg>
            </button>
            <div className="tour">
              <button onClick={() => setTouring(!touring)} aria-expanded={touring}>Examples</button>
              {touring && (
                <ul onMouseLeave={() => setTouring(false)}>
                  {TOUR.map((stop) => (
                    <li key={stop.label}>
                      <button onClick={() => { setTouring(false); void open(stop.drawing, stop.question, stop.view); }}>
                        <i className={`dot ${stop.state}`} />
                        <span>{stop.label}</span>
                        <em>{stop.note}</em>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <button className="drawing" onClick={() => setPicking(true)}>
              {detail ? (
                <>
                  <em>{SOURCE_NAMES[detail.source] ?? detail.source}</em>
                  <span>{detail.title}</span>
                  <i>{detail.formats.join(" · ").toUpperCase()}</i>
                </>
              ) : "Choose a drawing"}
            </button>
          </>
        )}
      </header>

      {page === "numbers" ? (
        <Numbers />
      ) : (
        <main className="work">
          <section className="left">
            <form className="ask" onSubmit={(e) => { e.preventDefault(); if (detail) void ask(detail, question); }}>
              <div className="field">
                <input
                  value={question}
                  placeholder={detail?.suggested.length ? "Ask about this drawing, in any language" : "Ask anything about this drawing"}
                  onChange={(e) => { setQuestion(e.target.value); setSuggesting(true); }}
                  onFocus={() => setSuggesting(true)}
                  onBlur={() => window.setTimeout(() => setSuggesting(false), 160)}
                  spellCheck={false}
                />
                {canListen && (
                  <button type="button" className={`mic${listening ? " on" : ""}`} onClick={listen} aria-label="Ask by voice" title="Ask by voice">
                    <svg viewBox="0 0 24 24" aria-hidden><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" fill="none" strokeWidth="2" strokeLinecap="round" /></svg>
                  </button>
                )}
                {"speechSynthesis" in window && (
                  <button type="button" className={`mic speak${speaking ? " on" : ""}`} onClick={() => { setSpeaking(!speaking); window.speechSynthesis.cancel(); }} aria-label="Read answers aloud" aria-pressed={speaking} title="Read answers aloud">
                    <svg viewBox="0 0 24 24" aria-hidden><path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z" /><path d="M15.5 9a4.2 4.2 0 0 1 0 6M18 6.5a7.6 7.6 0 0 1 0 11" fill="none" strokeWidth="2" strokeLinecap="round" /></svg>
                  </button>
                )}
                <button type="submit" className="go" disabled={!detail || asking || !question.trim()}>{asking ? "Asking…" : "Ask"}</button>
                {suggesting && detail && matches.length > 0 && (
                  <ul className="suggest">
                    {matches.slice(0, 60).map((s) => (
                      <li key={s.question}>
                        <button type="button" onMouseDown={(e) => { e.preventDefault(); void ask(detail, s.question); }}>
                          <i className={s.type}>{s.type}</i>
                          <span>{s.question}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              {has2d && has3d && (
                <div className="modes" role="tablist">
                  {(["plan", "both", "3d"] as Mode[]).map((m) => (
                    <button type="button" key={m} role="tab" aria-selected={effective === m} className={effective === m ? "on" : ""} onClick={() => setMode(m)}>
                      {m === "plan" ? "Plan" : m === "3d" ? "Raise to 3D" : "Both"}
                    </button>
                  ))}
                </div>
              )}
            </form>

            <div className={`stage mode-${effective}`}>
              {detail && has2d && effective !== "3d" && (
                <Viewer2D
                  detail={detail}
                  theme={theme}
                  evidence={drawable}
                  context={settled ? context : []}
                  visible={visible}
                  sweepAt={sweepAt}
                  excluded={excluded}
                  hover={hover}
                  focus={focus}
                  leftOut={leftOut}
                  focusKey={focusKey}
                  onHover={setHover}
                  onToggle={toggle}
                />
              )}
              {detail && has3d && effective !== "plan" && (
                <Viewer3D
                  key={detail.mesh!}
                  meshUrl={detail.mesh!}
                  groundUrl={groundUrl}
                  imageUrl={detail.view.url}
                  theme={theme}
                  highlights={highlights}
                  visible={visible}
                  hover={hover}
                  focus={focus}
                  excluded={excluded}
                  focusKey={focusKey}
                />
              )}
              {!detail && !error && <div className="viewer-wait">Unrolling the drawing…</div>}

              {/* The sheet's frame: zone letters and numbers, as on a real drawing. */}
              <div className="frame" aria-hidden>
                <div className="zones top">{ZONES.map((z) => <span key={z}>{z}</span>)}</div>
                <div className="zones side">{[1, 2, 3, 4, 5].map((z) => <span key={z}>{z}</span>)}</div>
              </div>
              {result && settled && <Stamp key={focusKey} result={result} />}
            </div>

            {/* The sheet's title block: what the drawing is, where it came from, whose it is. */}
            <div className="titleblock" aria-label="About this drawing">
              {detail && (
                <>
                  <div><span>Drawing</span><b>{detail.title}</b></div>
                  <div><span>Source</span><b>{SOURCE_NAMES[detail.source] ?? detail.source}</b></div>
                  <div><span>Formats</span><b>{detail.formats.join(" · ").toUpperCase()}</b></div>
                  {detail.paper_scale && <div><span>Scale, measured from the sheet</span><b>1:{detail.paper_scale}</b></div>}
                  {detail.model_file && <div><span>Model</span><b>{detail.model_file}</b></div>}
                  <div className="credit"><span>Credit</span><b>{detail.credit}</b></div>
                </>
              )}
            </div>
          </section>

          <AnswerPanel
            result={result}
            asking={asking}
            error={error}
            shown={shown}
            settled={settled}
            adjusted={adjusted}
            excluded={excluded}
            hover={hover}
            focus={focus}
            view={detail && has2d ? detail.view : null}
            theme={theme}
            starters={starters}
            onHover={setHover}
            onToggle={toggle}
            onFocus={setFocus}
            onRestore={() => setExcluded(new Set())}
            onUseModel={() => detail && void ask(detail, question, true)}
            onAsk={(text) => detail && void ask(detail, text)}
          />
        </main>
      )}

      {picking && <Picker sources={sources} current={detail?.id ?? null} onPick={(id) => void open(id)} onClose={() => setPicking(false)} />}
    </div>
  );
}
