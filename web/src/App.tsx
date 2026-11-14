import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnswerPanel } from "./AnswerPanel";
import { api, type AskResult, type DrawingDetail, type Evidence, euro, formatValue, headlineRun, places, type Source, SOURCE_NAMES, spoken } from "./api";
import { Home } from "./Home";
import { Numbers } from "./Numbers";
import { Picker } from "./Picker";
import { How } from "./How";
import { type Stop, TOUR } from "./tour";
import { anchorOf, Viewer2D } from "./Viewer2D";
import { type Highlight, Viewer3D } from "./Viewer3D";

type Mode = "plan" | "3d" | "both";
type Page = "home" | "ask" | "numbers" | "how";

interface Route {
  drawing: string | null;
  question: string;
  page: Page;
  view?: Mode | null;
}

function readRoute(): Route {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const drawing = params.get("d");
  const p = params.get("p");
  return {
    drawing,
    question: params.get("q") ?? "",
    page: p === "numbers" ? "numbers" : p === "how" ? "how" : drawing ? "ask" : "home",
    view: (["plan", "3d", "both"] as const).find((m) => m === params.get("v")) ?? null,
  };
}

function writeRoute(route: Route) {
  const params = new URLSearchParams();
  if (route.page === "numbers") params.set("p", "numbers");
  if (route.page === "how") params.set("p", "how");
  if (route.page !== "home" && route.drawing) params.set("d", route.drawing);
  if (route.page === "ask" && route.question) params.set("q", route.question);
  if (route.page === "ask" && route.view && route.view !== "plan") params.set("v", route.view);
  const next = `#${params.toString()}`;
  if (next !== window.location.hash) window.history.replaceState(null, "", next === "#" ? window.location.pathname : next);
}

// Evidence that stands for one unit of the answer (a counted item or a measured part).
function countable(evidence: Evidence[]): Evidence[] {
  return evidence.filter((e) => e.kind !== "formula" && e.kind !== "context");
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
  const [asked, setAsked] = useState("");  // the question the shown answer belongs to
  const [result, setResult] = useState<AskResult | null>(null);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);  // what a long answer is doing right now
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("plan");
  const [progress, setProgress] = useState(1);  // how far the reading line has crossed the evidence, 0 to 1
  const [excluded, setExcluded] = useState<Set<number>>(new Set());
  const [hover, setHover] = useState<number | null>(null);
  const [focus, setFocus] = useState<number | null>(null);
  const [picking, setPicking] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [focusKey, setFocusKey] = useState("");
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [playing, setPlaying] = useState(false);  // the examples play on their own, one after another
  const [bringing, setBringing] = useState(false);
  const [shared, setShared] = useState<string | null>(null);
  // What this session's answers cost, and what the vision model alone costs for the same questions.
  const [meter, setMeter] = useState({ answers: 0, spent: 0, alone: 0 });
  const ticket = useRef(0);
  const aloneRef = useRef<number | null>(null);
  const pending = useRef<string | null>(route.question || null);
  const wanted = useRef<Mode | null>(route.view ?? null);  // a view asked for in the link wins once
  const voiced = useRef(false);  // the server can read answers aloud with a natural voice

  const ask = useCallback(async (drawing: DrawingDetail, text: string, useModel = false) => {
    const clean = text.trim();
    if (!clean) return;
    const mine = ++ticket.current;
    setAsking(true);
    setBusy(null);
    setError(null);
    setSuggesting(false);
    // A long answer says where it is (reading the sheet, tile 3 of 8): shown while waiting.
    const watch = window.setInterval(() => {
      void api.progress(drawing.id, clean).then((text) => { if (mine === ticket.current) setBusy(text); }).catch(() => undefined);
    }, 900);
    setQuestion("");
    setAsked(clean);
    writeRoute({ drawing: drawing.id, question: clean, page: "ask" });
    try {
      const answer = await api.ask(drawing.id, clean, useModel);
      if (mine !== ticket.current) return;
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
      setFocusKey(`${drawing.id}|${clean}|${mine}`);
      const has2d = drawing.view.kind !== "none";
      const onPlan = answer.evidence.some((e) => e.bbox || e.points || e.x !== undefined);
      const in3d = answer.evidence.some((e) => e.kind === "element");
      const forced = wanted.current;
      wanted.current = null;
      const scan3d = drawing.mesh_kind === "scan";  // a scan's marks stand in its 3D view too: stay there
      setMode((current) => forced ?? (in3d && !onPlan ? (has2d && current === "both" ? "both" : "3d") : onPlan && current === "3d" && !scan3d ? "plan" : current));
    } catch (e) {
      if (mine === ticket.current) {
        setResult(null);
        setError((e as Error).message);
      }
    } finally {
      window.clearInterval(watch);
      if (mine === ticket.current) { setAsking(false); setBusy(null); }
    }
  }, []);

  const open = useCallback(async (id: string, then?: string, view?: Mode) => {
    if (view) wanted.current = view;
    setPicking(false);
    setError(null);
    setPage("ask");
    ticket.current++;
    try {
      const next = await api.drawing(id);
      setDetail(next);
      setResult(null);
      setProgress(1);
      setExcluded(new Set());
      setFocus(null);
      setFocusKey("");
      setQuestion("");
      setAsked("");
      // A drawing that comes with a model opens with the model standing beside it.
      setMode(next.view.kind === "none" ? "3d" : wanted.current ?? (next.mesh && window.innerWidth >= 1180 ? "both" : "plan"));
      writeRoute({ drawing: id, question: then ?? "", page: "ask" });
      if (then) void ask(next, then);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [ask]);
  const openStop = useCallback((stop: Stop) => open(stop.drawing, stop.question, stop.view), [open]);
  const bring = useCallback(async (file: File) => {
    setError(null);
    setBringing(true);
    try {
      const made = await api.upload(file);
      setSources(await api.sources());
      await open(made.id);
    } catch (e) {
      setPage("ask");
      setError((e as Error).message);
    } finally {
      setBringing(false);
    }
  }, [open]);
  const share = useCallback(async () => {
    if (!detail || !asked) return;
    setShared("working");
    try {
      const made = await api.share(detail.id, asked);
      const url = new URL(made.url, window.location.origin).toString();
      try { await navigator.clipboard.writeText(url); } catch { /* the sheet still opens */ }
      window.open(url, "_blank", "noopener");
      setShared(made.hosted === "cloudinary" ? "Link copied · hosted by Cloudinary" : "Link copied");
    } catch (e) {
      setShared((e as Error).message);
    }
    window.setTimeout(() => setShared(null), 4000);
  }, [detail, asked]);

  useEffect(() => {
    api.stats().then((stats) => {
      const run = headlineRun(stats.runs, "alone");
      aloneRef.current = run?.rows.find((r) => r.scope === "overall")?.eur_per_question ?? null;
    }).catch(() => undefined);
    api.partners().then((p) => { voiced.current = p.elevenlabs; }).catch(() => undefined);
    api.sources().then(async (list) => {
      setSources(list);
      if (route.drawing) await open(route.drawing, pending.current ?? undefined);
      pending.current = null;
    }).catch((e: Error) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A link pasted into an open tab, or back and forward: follow it without reloading the app.
  useEffect(() => {
    const follow = () => {
      const next = readRoute();
      if (next.page === "numbers") return setPage("numbers");
      if (next.page === "how") return setPage("how");
      if (next.page === "home" || !next.drawing) return setPage("home");
      void open(next.drawing, next.question || undefined, next.view ?? undefined);
    };
    window.addEventListener("hashchange", follow);
    return () => window.removeEventListener("hashchange", follow);
  }, [open]);

  // Evidence is marked one item at a time, in the order a line crossing the sheet from the
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
    const duration = Math.min(4200, Math.max(1300, units.length * 120));
    const looking = result.evidence.some((e) => e.kind === "context" && e.points) ? 1500 : 450;  // the searched rooms first
    const began = performance.now() + looking;
    setProgress(0);
    const timer = window.setInterval(() => {
      const p = Math.min(1, Math.max(0, (performance.now() - began) / duration));
      setProgress(p);
      if (p >= 1) window.clearInterval(timer);
    }, 16);
    return () => window.clearInterval(timer);
  }, [result, units.length]);
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
  // What is happening on the drawing right now, in words: the search, then the count, then what it came to.
  const caption = useMemo(() => {
    if (!result || result.answer === null) return null;
    const total = units.length;
    if (!total) return null;
    const area = units.some((e) => e.points);
    const what = area ? `room${total === 1 ? "" : "s"}` : `item${total === 1 ? "" : "s"}`;
    if (settled) return area ? `${total} ${what} measured and drawn. Click one to leave it out.` : `${total} ${what} marked and counted. Click one to leave it out.`;
    const looked = result.evidence.filter((e) => e.kind === "context" && e.points).length;  // rooms that were searched
    if (progress <= 0) return looked ? `Searching the ${looked} room${looked === 1 ? "" : "s"} the question names…` : "Reading the drawing…";
    return area ? `Measuring: ${revealed} of ${total} ${what}` : `Counting: ${revealed} of ${total}`;
  }, [result, units, settled, progress, revealed]);

  // "Make blueprints talk", literally: the drawing answers aloud, in the first person, once the evidence is in.
  useEffect(() => {
    if (!speaking || !result || !settled || !("speechSynthesis" in window)) return;
    const sentence = spoken(result);
    const browserVoice = () => {
      const utterance = new SpeechSynthesisUtterance(sentence);
      utterance.lang = "en-GB";
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(utterance);
    };
    if (!voiced.current) return browserVoice();
    // A natural voice from ElevenLabs when the server has a key; the browser's own voice otherwise.
    fetch("/api/speak", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text: sentence }) })
      .then((res) => (res.ok ? res.blob() : Promise.reject(new Error("no voice"))))
      .then((blob) => new Audio(URL.createObjectURL(blob)).play())
      .catch(browserVoice);
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
      // A place where the file has the same outline several times is left out, or put back, as a whole.
      const here = [n, ...(result?.evidence ?? []).filter((e) => e.same_as === n && e.n != null).map((e) => e.n!)];
      const off = next.has(n);
      for (const each of here) { if (off) next.delete(each); else next.add(each); }
      return next;
    });
  }, [result]);

  // One mark per place: an outline that is in the file twice is drawn once, and says so.
  const drawable = useMemo(() => (result ? places(result.evidence.filter((e) => e.kind !== "context" && (e.bbox || e.points || e.x !== undefined))) : []), [result]);
  const context = useMemo(() => (result ? result.evidence.filter((e) => e.kind === "context" && (e.bbox || e.points)) : []), [result]);
  // The rooms an answer searched are shown from the first moment: the search is seen before the count.
  const searched = useMemo(() => context.filter((e) => e.points), [context]);
  const highlights: Highlight[] = useMemo(() => {
    if (!result) return [];
    const scan = detail?.mesh_kind === "scan";
    return places(units).flatMap((e): Highlight[] => {
      const id = e.kind === "element" ? e.id : e.ref;
      const tone = e.kind === "polygon" || e.cls === "IfcSpace" ? "area" as const : "count" as const;
      if (id) return [{ id, n: e.n!, tone, times: e.times }];
      // On a scan the evidence is a box or a point in the picture: it stands in the 3D view at that place.
      const box = !scan ? null : e.bbox ? e.bbox : e.x !== undefined && e.y !== undefined ? [e.x, e.y, e.x, e.y] : null;
      return box ? [{ id: "", n: e.n!, tone: "count" as const, box: box as [number, number, number, number] }] : [];
    });
  }, [result, units, detail]);
  // Outdoor rooms a sum left out, shown hatched: only when the answer is a sum over rooms.
  const leftOut = useMemo(() => {
    if (!detail || !result || result.unit !== "m2") return [];
    const used = new Set(result.evidence.filter((e) => e.kind === "polygon").map((e) => e.ref));
    if (used.size < 2 || !/indoor|without|ohne|excluding/i.test(result.interpreted ?? "")) return [];
    const flats = new Set(detail.rooms.filter((r) => used.has(r.ref)).map((r) => r.unit));
    return detail.rooms.filter((r) => r.outdoor && !used.has(r.ref) && flats.has(r.unit));
  }, [detail, result]);

  const at = TOUR.findIndex((stop) => stop.drawing === detail?.id && stop.question === asked);

  // Playing: once an answer has been on show for a while, move to the next example.
  useEffect(() => {
    if (!playing || page !== "ask" || asking || (result && !settled)) return;
    const next = TOUR[(at + 1) % TOUR.length];
    const dwell = !result || at === -1 ? 500 : TOUR[at].view ? 9500 : 6500;  // a 3D scene needs time to build
    const timer = window.setTimeout(() => void openStop(next), dwell);
    return () => window.clearTimeout(timer);
  }, [playing, page, asking, result, settled, at, openStop]);

  // Keys. While inspecting: arrows step through the items, X leaves one out, Escape ends.
  // Otherwise the arrows step through the examples, for presenting.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (page !== "ask" || picking || e.altKey || e.ctrlKey || e.metaKey) return;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA")) return;
      if (e.key === "Escape" && playing) setPlaying(false);
      if (focus !== null) {
        const i = units.findIndex((u) => u.n === focus);
        if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
          e.preventDefault();
          setFocus(units[(i + (e.key === "ArrowRight" ? 1 : -1) + units.length) % units.length].n!);
        } else if (e.key === "x" || e.key === "X") toggle(focus);
        else if (e.key === "Escape") setFocus(null);
        return;
      }
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      if (at === -1 && e.key === "ArrowLeft") return;
      e.preventDefault();
      void openStop(TOUR[(at + (e.key === "ArrowRight" ? 1 : -1) + TOUR.length) % TOUR.length]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [page, picking, at, openStop, focus, units, toggle, playing]);

  // A few of the dataset's questions about this drawing to start from: one count, one area, one in another language.
  const starters = useMemo(() => {
    if (!detail) return [];
    const english = (text: string) => /^(How|What|Which|On sheet)\b/.test(text);
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

  // Asked aloud, answered aloud: the words appear as they are heard, and the drawing replies by voice.
  const listen = (target: DrawingDetail | null = detail) => {
    const Recognition = (window as unknown as Record<string, any>).SpeechRecognition ?? (window as unknown as Record<string, any>).webkitSpeechRecognition;
    if (!Recognition || !target) return;
    const recognition = new Recognition();
    recognition.lang = navigator.language || "en-US";
    recognition.interimResults = true;
    recognition.onresult = (event: any) => {
      const heard = event.results[event.results.length - 1];
      const text = heard[0].transcript as string;
      setQuestion(text);
      if (heard.isFinal) {
        setSpeaking(true);
        void ask(target, text);
      }
    };
    recognition.onend = () => setListening(false);
    recognition.onerror = () => setListening(false);
    setListening(true);
    try { recognition.start(); } catch { setListening(false); }
  };
  // "Ask it aloud" on the landing page: open the drawing, then listen.
  const talkNext = useRef(false);
  useEffect(() => {
    if (!detail || !talkNext.current) return;
    talkNext.current = false;
    listen(detail);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail]);
  const talk = (stop: Stop) => {
    talkNext.current = true;
    setSpeaking(true);
    void open(stop.drawing, undefined, "both");
  };
  const canListen = typeof window !== "undefined" && ("SpeechRecognition" in window || "webkitSpeechRecognition" in window);
  const go = (next: Page) => {
    setPage(next);
    setPlaying(false);
    writeRoute({ drawing: detail?.id ?? null, question: asked, page: next });
  };
  const groundUrl = detail && has3d && (detail.view.kind === "plan" || detail.view.kind === "page" || detail.mesh_kind === "scan")
    ? `/api/ground?drawing=${encodeURIComponent(detail.id)}` : null;

  return (
    <div className={`app page-${page}`}>
      <header className="top">
        <button className="brand" onClick={() => go("home")} aria-label="Home">
          <svg viewBox="0 0 28 22" aria-hidden><path className="kv1" d="M9 1h18l-4.5 7H4.500z" /><path className="kv2" d="M5.500 14h18L19 21H1z" /></svg>
          <span>Kaava</span>
        </button>
        <nav>
          <button className={page === "home" ? "on" : ""} onClick={() => go("home")}>Home</button>
          <button className={page === "ask" ? "on" : ""} onClick={() => (detail ? go("ask") : void openStop(TOUR[0]))}>Ask</button>
          <button className={page === "how" ? "on" : ""} onClick={() => go("how")}>How it works</button>
          <button className={page === "numbers" ? "on" : ""} onClick={() => go("numbers")}>Numbers</button>
        </nav>
        <div className="right">
          {page === "ask" && meter.answers > 0 && (
            <span className="meter" title="What this session's answers cost here, and what a vision model alone costs for the same questions">
              {meter.answers} answer{meter.answers === 1 ? "" : "s"} · {euro(meter.spent)}
              {meter.alone > 0 && <em> · model alone {euro(meter.alone)}</em>}
            </span>
          )}
          {page === "ask" && (
            <button className={`ghost${playing ? " on" : ""}`} onClick={() => setPlaying(!playing)} aria-pressed={playing} title="Play the examples one after another (arrow keys step through them)">
              {playing ? "Stop" : "Play examples"}
            </button>
          )}
          <button className="primary" onClick={() => setPicking(true)}>Choose a drawing</button>
        </div>
      </header>

      {page === "home" && <Home onOpen={(stop) => void openStop(stop)} onAsk={(drawing, words) => void open(drawing, words)} onBrowse={() => setPicking(true)} onNumbers={() => go("numbers")} onBring={(file) => void bring(file)} onTalk={talk} canTalk={canListen} />}
      {bringing && <div className="toast">Reading your drawing…</div>}
      {shared && shared !== "working" && <div className="toast">{shared}</div>}
      {page === "numbers" && <Numbers />}
      {page === "how" && <How onOpen={(stop) => void openStop(stop)} />}
      {page === "ask" && (
        <main className="work">
          <section className="reading">
            <div className="scroll">
              <AnswerPanel
                result={result}
                asking={asking}
                error={error}
                shown={shown}
                settled={settled}
                adjusted={adjusted}
                busy={busy}
                marked={revealed}
                excluded={excluded}
                hover={hover}
                focus={focus}
                view={detail && has2d ? detail.view : null}
                starters={starters}
                onHover={setHover}
                onToggle={toggle}
                onFocus={setFocus}
                onRestore={() => setExcluded(new Set())}
                onUseModel={() => detail && void ask(detail, asked, true)}
                onShare={has2d ? () => void share() : null}
                sharing={shared === "working"}
                onAsk={(text) => detail && void ask(detail, text)}
              />
            </div>

            <form className="composer" onSubmit={(e) => { e.preventDefault(); if (detail) void ask(detail, question); }}>
              {suggesting && detail && matches.length > 0 && (
                <ul className="suggest">
                  {matches.slice(0, 60).map((s) => (
                    <li key={s.question}>
                      <button type="button" onMouseDown={(e) => { e.preventDefault(); void ask(detail, s.question); }}>
                        <i>{s.type}</i>
                        <span>{s.question}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <input
                value={question}
                placeholder={listening ? "Listening…" : detail?.suggested.length ? "Ask about this drawing, in any language" : "Ask anything about this drawing"}
                onChange={(e) => { setQuestion(e.target.value); setSuggesting(true); }}
                onFocus={() => setSuggesting(true)}
                onBlur={() => window.setTimeout(() => setSuggesting(false), 160)}
                spellCheck={false}
              />
              <div className="row">
                {canListen && (
                  <button type="button" className={`tool${listening ? " on hearing" : ""}`} onClick={() => listen()} aria-label="Ask by voice" title="Ask by voice">
                    <svg viewBox="0 0 24 24" aria-hidden><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" /></svg>
                  </button>
                )}
                {"speechSynthesis" in window && (
                  <button type="button" className={`tool${speaking ? " on" : ""}`} onClick={() => { setSpeaking(!speaking); window.speechSynthesis.cancel(); }} aria-label="Read answers aloud" aria-pressed={speaking} title="Read answers aloud">
                    <svg viewBox="0 0 24 24" aria-hidden><path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z" /><path d="M15.5 9a4.2 4.2 0 0 1 0 6M18 6.5a7.6 7.6 0 0 1 0 11" /></svg>
                  </button>
                )}
                <span className="grow" />
                <button type="submit" className="send" disabled={!detail || asking || !question.trim()} aria-label="Ask">
                  <svg viewBox="0 0 24 24" aria-hidden><path d="M12 19V5M5.5 11.5L12 5l6.5 6.5" /></svg>
                </button>
              </div>
            </form>
          </section>

          <section className="board">
            <header>
              {at >= 0 ? (
                // Presenting the examples: what this one shows, and where we are among them.
                <div className={`caption ${TOUR[at].state}`} key={at}>
                  <button type="button" onClick={() => void openStop(TOUR[(at - 1 + TOUR.length) % TOUR.length])} aria-label="Previous example">←</button>
                  <i>{String(at + 1).padStart(2, "0")}<small> / {TOUR.length}</small></i>
                  <div>
                    <strong>{TOUR[at].label}</strong>
                    <span>{TOUR[at].note}</span>
                  </div>
                  <button type="button" onClick={() => void openStop(TOUR[(at + 1) % TOUR.length])} aria-label="Next example">→</button>
                </div>
              ) : (
                <div className="what">
                  {detail && (
                    <>
                      <strong>{detail.title}</strong>
                      <span>
                        {detail.example ? "Stoa's sample sheet" : SOURCE_NAMES[detail.source] ?? detail.source} · {detail.formats.join(", ").toUpperCase()}{detail.paper_scale ? ` · 1:${detail.paper_scale}` : ""}
                      </span>
                    </>
                  )}
                </div>
              )}
              {has2d && has3d && (
                <div className="segmented" role="tablist">
                  {(["plan", "both", "3d"] as Mode[]).map((m) => (
                    <button type="button" key={m} role="tab" aria-selected={effective === m} className={effective === m ? "on" : ""} onClick={() => setMode(m)}>
                      {m === "plan" ? "Plan" : m === "3d" ? "3D" : "Both"}
                    </button>
                  ))}
                </div>
              )}
            </header>
            <div className={`stage mode-${effective}`}>
              {result && caption && <p className={`doing${settled ? " done" : ""}`} key={`${focusKey}|${settled}`}><i />{caption}</p>}
              {detail && has2d && effective !== "3d" && (
                <Viewer2D
                  detail={detail}
                  evidence={drawable}
                  context={settled ? context : searched}
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
                  highlights={highlights}
                  visible={visible}
                  hover={hover}
                  focus={focus}
                  excluded={excluded}
                  focusKey={focusKey}
                />
              )}
              {!detail && !error && <div className="viewer-wait">Opening the drawing…</div>}
            </div>
            <footer>{detail?.credit}{detail?.model_file ? ` · Model: ${detail.model_file}` : ""}</footer>
          </section>
        </main>
      )}

      {picking && <Picker sources={sources} current={detail?.id ?? null} onPick={(id) => void open(id)} onClose={() => setPicking(false)} />}
    </div>
  );
}
