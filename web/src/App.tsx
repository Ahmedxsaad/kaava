import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnswerPanel } from "./AnswerPanel";
import { api, type AskResult, type DrawingDetail, type Evidence, type Source, SOURCE_NAMES } from "./api";
import { Numbers } from "./Numbers";
import { Picker } from "./Picker";
import { TOUR } from "./tour";
import { Viewer2D } from "./Viewer2D";
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

// Evidence that stands for one unit of the answer (a counted item or a measured part).
function countable(evidence: Evidence[]): Evidence[] {
  return evidence.filter((e) => e.kind !== "formula");
}

function measureOf(item: Evidence): number | null {
  return item.area_m2 ?? item.volume_m3 ?? item.length_m ?? null;
}

export function App() {
  const [route] = useState(readRoute);
  const [page, setPage] = useState<Page>(route.page);
  const [sources, setSources] = useState<Source[]>([]);
  const [detail, setDetail] = useState<DrawingDetail | null>(null);
  const [question, setQuestion] = useState(route.question);
  const [result, setResult] = useState<AskResult | null>(null);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("plan");
  const [revealed, setRevealed] = useState(0);
  const [excluded, setExcluded] = useState<Set<number>>(new Set());
  const [hover, setHover] = useState<number | null>(null);
  const [picking, setPicking] = useState(false);
  const [touring, setTouring] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [focusKey, setFocusKey] = useState("");
  const [listening, setListening] = useState(false);
  const asked = useRef(0);
  const pending = useRef<string | null>(route.question || null);
  const wanted = useRef<Mode | null>(route.view ?? null);  // a view asked for in the link wins once

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
      setRevealed(0);
      setResult(answer);
      setFocusKey(`${drawing.id}|${clean}|${ticket}`);
      const has2d = drawing.view.kind !== "none";
      const onPlan = answer.evidence.some((e) => e.bbox || e.points || e.x !== undefined);
      const in3d = answer.evidence.some((e) => e.kind === "element");
      const forced = wanted.current;
      wanted.current = null;
      setMode((current) => forced ?? (in3d ? (has2d && current === "both" ? "both" : "3d") : onPlan && current === "3d" ? "plan" : current));
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
      setRevealed(0);
      setExcluded(new Set());
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
    api.sources().then(async (list) => {
      setSources(list);
      const first = route.drawing ?? TOUR[0]?.drawing;
      if (first) await open(first, pending.current ?? undefined);
      pending.current = null;
    }).catch((e: Error) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Evidence lands one item at a time; the counter follows.
  const units = useMemo(() => (result ? countable(result.evidence) : []), [result]);
  useEffect(() => {
    if (!result || !units.length) return;
    // By the clock, not by counting ticks: a busy page must not slow the count down.
    const step = Math.min(170, Math.max(16, 1500 / units.length));
    const began = performance.now() + 420;
    const timer = window.setInterval(() => {
      const n = Math.min(units.length, Math.floor((performance.now() - began) / step));
      if (n > 0) setRevealed(n);
      if (n >= units.length) window.clearInterval(timer);
    }, 16);
    return () => window.clearInterval(timer);
  }, [result, units.length]);

  const settled = !result || revealed >= units.length;
  const tracksCount = !!result && result.unit === "count" && typeof result.answer === "number" && units.length === result.answer;
  const parts = units.map(measureOf);
  const tracksSum = !!result && typeof result.answer === "number" && result.unit !== "count" && units.length > 0 &&
    parts.every((p) => p !== null) && Math.abs(parts.reduce((s: number, p) => s + (p ?? 0), 0) - result.answer) < 0.02 * Math.max(1, result.answer);
  const shown = useMemo(() => {
    if (!result || settled) return result?.answer ?? null;
    if (tracksCount) return revealed;
    if (tracksSum) return units.slice(0, revealed).reduce((s, e) => s + (measureOf(e) ?? 0), 0);
    return result.answer;
  }, [result, settled, tracksCount, tracksSum, revealed, units]);
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

  const drawable = useMemo(() => (result ? result.evidence.filter((e) => e.bbox || e.points || e.x !== undefined) : []), [result]);
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
    const units = new Set(detail.rooms.filter((r) => used.has(r.ref)).map((r) => r.unit));
    return detail.rooms.filter((r) => r.outdoor && !used.has(r.ref) && units.has(r.unit));
  }, [detail, result]);

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

  return (
    <div className="app">
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
          <button className={page === "ask" ? "on" : ""} onClick={() => { setPage("ask"); writeRoute({ drawing: detail?.id ?? null, question, page: "ask" }); }}>Ask</button>
          <button className={page === "numbers" ? "on" : ""} onClick={() => { setPage("numbers"); writeRoute({ drawing: detail?.id ?? null, question, page: "numbers" }); }}>Numbers</button>
        </nav>
        <div className="spacer" />
        {page === "ask" && (
          <>
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
            <form
              className="ask"
              onSubmit={(e) => { e.preventDefault(); if (detail) void ask(detail, question); }}
            >
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
                  <button type="button" className={`mic${listening ? " on" : ""}`} onClick={listen} aria-label="Ask by voice">
                    <svg viewBox="0 0 24 24" aria-hidden><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" fill="none" strokeWidth="2" strokeLinecap="round" /></svg>
                  </button>
                )}
                <button type="submit" className="go" disabled={!detail || asking || !question.trim()}>
                  {asking ? "Asking…" : "Ask"}
                </button>
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
                      {m === "plan" ? "Plan" : m === "3d" ? "3D" : "Both"}
                    </button>
                  ))}
                </div>
              )}
            </form>

            <div className={`stage mode-${effective}`}>
              {detail && has2d && effective !== "3d" && (
                <Viewer2D
                  detail={detail}
                  evidence={drawable}
                  revealed={revealed}
                  excluded={excluded}
                  hover={hover}
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
                  highlights={highlights}
                  revealed={revealed}
                  hover={hover}
                  excluded={excluded}
                  focusKey={focusKey}
                />
              )}
              {!detail && !error && <div className="viewer-wait">Loading the drawing…</div>}
            </div>

            <footer className="credit">
              {detail && (
                <>
                  <span>{detail.credit}</span>
                  {detail.paper_scale && <span>Sheet scale 1:{detail.paper_scale}, measured from the drawing</span>}
                  {detail.model_file && <span>Model: {detail.model_file}</span>}
                </>
              )}
            </footer>
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
            onHover={setHover}
            onToggle={toggle}
            onRestore={() => setExcluded(new Set())}
            onUseModel={() => detail && void ask(detail, question, true)}
          />
        </main>
      )}

      {picking && <Picker sources={sources} current={detail?.id ?? null} onPick={(id) => void open(id)} onClose={() => setPicking(false)} />}
    </div>
  );
}
