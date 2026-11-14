import { useEffect, useMemo, useRef, useState } from "react";
import { api, type AskResult, type DrawingDetail, type Evidence, formatValue, headlineRun, type Run, type Stats } from "./api";
import { type Stop, TOUR } from "./tour";
import { type Direction, type Highlight, Viewer3D } from "./Viewer3D";

// The landing page is one stage. It opens by itself: a real drawing stands up into a model and
// answers a question, before anyone clicks. Scrolling then tells the story: the page's scroll
// position directs the model. Every figure on screen is the engine's own answer to a real
// question, asked when the page loads.

const STOP = TOUR[0];
// A scan, for the chapter where there is no geometry to read: the first example answered by a vision model.
const SCAN = TOUR.find((stop) => stop.drawing.includes("cubicasa") && /window/i.test(stop.question)) ?? TOUR[7];
const WINDOWS = "How many windows are drawn on this floor plan?";
const NONE = new Set<number>();
const CHAPTERS = 7;
// Where each chapter begins, as a share of the whole scroll:
// the opening, a picture, counting, measuring, a scan, how far to trust it, results.
const AT = [0, 0.1, 0.27, 0.46, 0.61, 0.75, 0.88, 1.0001];
const OPENING = 0.03;  // below this the opening plays by itself

const clamp = (x: number) => Math.min(1, Math.max(0, x));
const smooth = (from: number, to: number, x: number) => { const t = clamp((x - from) / (to - from)); return t * t * (3 - 2 * t); };
const linear = (from: number, to: number, x: number) => clamp((x - from) / (to - from));
const overall = (run?: Run) => run?.rows.find((r) => r.scope === "overall");
const pct = (v: number | null | undefined) => (v == null ? "—" : `${(100 * v).toFixed(1)}%`);
const euros = (v: number | null | undefined) => (v == null ? "—" : `€${(1000 * v).toFixed(2)}`);
const counted = (result: AskResult | null): Evidence[] =>
  result ? result.evidence.filter((e) => e.kind !== "formula" && e.kind !== "context" && e.n != null) : [];
const VERDICT = { verified: "Verified", check: "Check this", declined: "Can't answer" } as const;

// The opening's own clock: how long a question takes to type, to be answered, and to stay.
const TYPE_MS = 30, COUNT_MS = 2100, HOLD_MS = 3400, STAND_MS = 1700;

interface Props {
  stats: Stats | null;
  onOpen(stop: Stop): void;
  onAsk(drawing: string, question: string): void;
  onBrowse(): void;
  onBring(file: File): void;
  onTalk(stop: Stop): void;
  canTalk: boolean;
}

export function Story({ stats, onOpen, onAsk, onBrowse, onBring, onTalk, canTalk }: Props) {
  const section = useRef<HTMLElement>(null);
  const [p, setP] = useState(0);  // how far through the story, 0 to 1
  const [detail, setDetail] = useState<DrawingDetail | null>(null);
  const [doors, setDoors] = useState<AskResult | null>(null);
  const [area, setArea] = useState<AskResult | null>(null);
  const [windows, setWindows] = useState<AskResult | null>(null);
  const [flat, setFlat] = useState<string | null>(null);  // which apartment the area question is about
  const [scan, setScan] = useState<{ drawing: DrawingDetail; answer: AskResult } | null>(null);
  const [built, setBuilt] = useState<number | null>(null);  // when the model came on stage

  // The drawing, and real questions put to the engine.
  useEffect(() => {
    let alive = true;
    api.drawing(STOP.drawing).then((drawing) => {
      if (!alive) return;
      setDetail(drawing);
      const sum = drawing.suggested.find((q) => q.unit === "m2" && /net floor area of apartment [A-Z] /.test(q.question));
      if (sum) {
        setFlat(/apartment ([A-Z]) /.exec(sum.question)?.[1] ?? null);
        api.ask(STOP.drawing, sum.question, false).then((answer) => alive && setArea(answer)).catch(() => undefined);
      }
    }).catch(() => undefined);
    api.ask(STOP.drawing, STOP.question, false).then((answer) => alive && setDoors(answer)).catch(() => undefined);
    api.ask(STOP.drawing, WINDOWS, false).then((answer) => alive && setWindows(answer)).catch(() => undefined);
    Promise.all([api.drawing(SCAN.drawing), api.ask(SCAN.drawing, SCAN.question, false)])
      .then(([drawing, answer]) => alive && setScan({ drawing, answer })).catch(() => undefined);
    return () => { alive = false; };
  }, []);

  // The scroll position, read once per frame.
  useEffect(() => {
    let frame = 0;
    const read = () => {
      frame = 0;
      const el = section.current;
      if (!el) return;
      const box = el.getBoundingClientRect();
      const span = box.height - window.innerHeight;
      setP(span > 0 ? clamp(-box.top / span) : 0);
      document.documentElement.toggleAttribute("data-on-stage", box.bottom > 72);  // the top bar floats over the stage
    };
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(read); };
    read();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      cancelAnimationFrame(frame);
      document.documentElement.removeAttribute("data-on-stage");
    };
  }, []);

  const chapter = Math.max(0, AT.findIndex((start, i) => p >= start && p < AT[i + 1]));
  const opening = p < OPENING;

  // ---- the opening plays by itself: the model stands up, then answers one question after another
  const doorItems = useMemo(() => counted(doors), [doors]);
  const roomItems = useMemo(() => counted(area), [area]);
  const windowItems = useMemo(() => counted(windows), [windows]);
  const scenes = useMemo(() => [
    { key: "doors", chip: "Count the doors", says: "How many doors are on this floor?", unit: "doors", result: doors, items: doorItems },
    { key: "area", chip: `Measure apartment ${flat ?? ""}`.trim(), says: `What is the net area of apartment ${flat ?? "B"}?`, unit: "m²", result: area, items: roomItems },
    { key: "windows", chip: "Count the windows", says: "How many windows are there?", unit: "windows", result: windows, items: windowItems },
  ].filter((scene) => scene.result), [doors, area, windows, doorItems, roomItems, windowItems, flat]);
  const [sceneAt, setSceneAt] = useState(0);  // which question the opening is on
  const [sceneBegan, setSceneBegan] = useState(0);  // and when it began
  const [now, setNow] = useState(0);
  const [draft, setDraft] = useState<string | null>(null);  // the visitor's own words, once they type
  useEffect(() => {
    if (!opening || built === null) return;
    const timer = window.setInterval(() => setNow(performance.now()), 34);
    return () => window.clearInterval(timer);
  }, [opening, built]);
  const scene = scenes.length ? scenes[sceneAt % scenes.length] : null;
  const standing = built === null ? 0 : (now - built) / STAND_MS;  // 0 to 1 while the walls rise, then on
  useEffect(() => {  // the first question begins once the model stands; each next one after the last has been shown
    if (!opening || built === null || !scene || draft !== null) return;
    if (!sceneBegan) { if (standing >= 1) setSceneBegan(now); return; }
    if (now - sceneBegan > scene.says.length * TYPE_MS + 400 + COUNT_MS + HOLD_MS) {
      setSceneAt((n) => n + 1);
      setSceneBegan(now);
    }
  }, [now, opening, built, scene, sceneBegan, standing, draft]);
  const sceneClock = sceneBegan ? now - sceneBegan : 0;
  const typing = scene ? scene.says.length * TYPE_MS + 400 : 0;
  const typed = scene && sceneBegan ? scene.says.slice(0, Math.floor(sceneClock / TYPE_MS)) : "";
  const sceneAsked = !!scene && !!sceneBegan && sceneClock >= typing;
  const sceneShown = scene && sceneAsked ? Math.round(clamp((sceneClock - typing) / COUNT_MS) * scene.items.length) : 0;
  const sceneDone = !!scene && sceneAsked && sceneShown >= scene.items.length;
  const sceneFigure = !scene?.result || !sceneAsked ? null
    : sceneDone ? scene.result.answer
    : scene.key === "area" ? scene.items.slice(0, sceneShown).reduce((total, e) => total + (e.area_m2 ?? 0), 0)
    : sceneShown;
  const pick = (index: number) => { setDraft(null); setSceneAt(index); setSceneBegan(performance.now()); };

  // ---- where the model stands and where the camera is
  const direction: Direction = useMemo(() => {
    if (opening) {
      const t = built === null ? 0 : (now - built) / 1000;
      return {
        rise: smooth(0.15, STAND_MS / 1000, t),
        tilt: 1.5 - 0.88 * smooth(0, 1.9, t),
        turn: Math.PI / 2 + 0.5 * smooth(0, 2.2, t) + 0.045 * Math.max(0, t - 2),
        back: 1.86, shift: 0, drop: 0.285,
      };
    }
    // Scrolling: it lies down flat (a picture), stands up again (geometry), then is walked around.
    const down = smooth(0.045, 0.125, p), up = smooth(0.25, 0.35, p);
    return {
      rise: clamp(1 - down + up),
      tilt: 0.62 + 0.88 * (down - up) + 0.14 * smooth(0.72, 0.92, p),
      turn: Math.PI / 2 + 0.5 * (1 - down) + 0.5 * up + 0.72 * linear(0.36, 1, p),
      back: 1.86 - 0.56 * smooth(OPENING, 0.1, p),
      shift: 0.2 * smooth(OPENING, 0.1, p),
      drop: 0.285 * (1 - smooth(OPENING, 0.1, p)),
    };
  }, [opening, p, now, built]);

  // ---- what is lit: the opening's own question, or the chapter the scroll is in
  const act = opening ? scene?.key ?? "rest" : chapter === 2 ? "doors" : chapter === 3 ? "area" : "rest";
  const items = opening ? (sceneAsked ? scene?.items ?? [] : []) : act === "doors" ? doorItems : act === "area" ? roomItems : [];
  const shown = opening ? sceneShown
    : act === "doors" ? Math.round(linear(AT[2] + 0.085, AT[3] - 0.03, p) * doorItems.length)
    : act === "area" ? Math.round(linear(AT[3] + 0.03, AT[4] - 0.035, p) * roomItems.length) : 0;
  const highlights: Highlight[] = useMemo(() => items.flatMap((e) => {
    const id = e.kind === "element" ? e.id : e.ref;
    return id ? [{ id, n: e.n!, tone: e.kind === "polygon" ? "area" as const : "count" as const }] : [];
  }), [items]);
  const visible = useMemo(() => (shown ? new Set(items.slice(0, shown).map((e) => e.n!)) : NONE), [items, shown]);
  const doorsDone = act === "doors" && shown >= doorItems.length && doorItems.length > 0;
  const sum = roomItems.slice(0, act === "area" ? shown : roomItems.length).reduce((total, e) => total + (e.area_m2 ?? 0), 0);
  const areaDone = act !== "area" || shown >= roomItems.length;
  const terms = roomItems.slice(0, act === "area" ? shown : roomItems.length).map((e) => (e.area_m2 ?? 0).toFixed(2));
  // On the scan, the model's boxes are drawn one by one.
  const boxes = useMemo(() => (scan ? scan.answer.evidence.filter((e) => e.kind === "box" && e.bbox && e.n != null) : []), [scan]);
  const boxed = Math.round(linear(AT[4] + 0.03, AT[5] - 0.035, p) * boxes.length);

  const runs = stats?.runs ?? [];
  const alone = overall(headlineRun(runs, "alone"));
  const ours = overall(headlineRun(runs, "system"));
  const engines = overall([...runs].reverse().find((r) => r.method === "router:v1" && r.split === "dev" && r.dataset === "junction-dataset")
    ?? [...runs].reverse().find((r) => r.method === "router:v1" && r.split === "dev"));
  const guess = doors?.baseline ?? null;  // what a vision model alone said for the doors
  const proof = doors?.verified_because;

  // ---- playing: the drawing tells its own story. Each chapter's line is spoken (ElevenLabs, when
  // the server has a key) while the page scrolls through that chapter in step with it.
  const [playing, setPlaying] = useState(false);
  const voiced = useRef(false);
  useEffect(() => { api.partners().then((partners) => { voiced.current = partners.elevenlabs; }).catch(() => undefined); }, []);
  const script = useRef<string[]>([]);
  const here = useRef(0);
  here.current = p;
  const cheaper = ours?.eur_per_question != null && alone?.eur_per_question != null && ours.eur_per_question < alone.eur_per_question / 2;
  script.current = [
    "I am a floor plan. Ask me anything.",
    `To a vision model I am only pixels${guess?.answer != null ? `. It looks, and guesses ${guess.answer} doors` : ""}.`,
    doors ? `But I am made of geometry. Watch me stand up. I have ${doorItems.length} doors: each one counted, none of them guessed.` : "",
    area && typeof area.answer === "number" ? `Apartment ${flat ?? ""} measures ${area.answer.toFixed(1)} square metres. Every room is measured, then added up.` : "",
    scan ? `This one is only a scan, with no geometry to read. So Gemini points, and the code counts: ${boxes.length} windows. Check that one.` : "",
    "And I tell you how far to trust me. Verified, check this, or no number at all.",
    ours?.accuracy != null ? `${(100 * ours.accuracy).toFixed(1)} percent of my answers are right${cheaper ? ", at less than half the cost of a vision model" : ""}. Now, ask me something.` : "",
  ];
  useEffect(() => {
    if (!playing) return;
    const el = section.current;
    if (!el) return;
    let stopped = false, frame = 0;
    let speaking: HTMLAudioElement | null = null;
    const span = el.getBoundingClientRect().height - window.innerHeight;
    const scrollOver = (from: number, to: number, ms: number) => new Promise<void>((done) => {
      const began = performance.now();
      const step = () => {
        if (stopped) return done();
        const t = Math.min(1, (performance.now() - began) / ms);
        window.scrollTo(0, el.offsetTop + span * (from + (to - from) * t));
        if (t >= 1) done(); else frame = requestAnimationFrame(step);
      };
      step();
    });
    const clip = async (text: string): Promise<HTMLAudioElement | null> => {
      if (!voiced.current || !text) return null;
      try {
        const res = await fetch("/api/speak", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text }) });
        if (!res.ok) return null;
        const audio = new Audio(URL.createObjectURL(await res.blob()));
        await new Promise<void>((ready) => { audio.onloadedmetadata = () => ready(); audio.onerror = () => ready(); });
        return audio;
      } catch { return null; }
    };
    void (async () => {
      const first = Math.max(0, AT.findIndex((from, i) => here.current >= from && here.current < AT[i + 1]));
      const clips = await Promise.all(script.current.slice(first).map(clip));
      for (let i = first; i < CHAPTERS && !stopped; i++) {
        speaking = clips[i - first];
        const said = speaking && Number.isFinite(speaking.duration) ? speaking.duration * 1000 + 900 : 0;
        void speaking?.play().catch(() => undefined);
        // The opening holds still while its line is said, then the page moves on.
        if (i === 0) await new Promise<void>((done) => window.setTimeout(done, Math.max(2600, said)));
        await scrollOver(i === first ? here.current : AT[i], Math.min(1, AT[i + 1]), i === 0 ? 2200 : Math.max(5600, said));
      }
      if (!stopped) setPlaying(false);
    })();
    const stop = () => setPlaying(false);
    window.addEventListener("wheel", stop, { passive: true });
    window.addEventListener("touchstart", stop, { passive: true });
    window.addEventListener("keydown", stop);
    return () => {
      stopped = true;
      cancelAnimationFrame(frame);
      speaking?.pause();
      window.removeEventListener("wheel", stop);
      window.removeEventListener("touchstart", stop);
      window.removeEventListener("keydown", stop);
    };
  }, [playing]);

  const go = (to: number) => {
    const el = section.current;
    if (!el) return;
    const span = el.getBoundingClientRect().height - window.innerHeight;
    window.scrollTo({ top: el.offsetTop + span * (AT[to] + 0.012), behavior: "smooth" });
  };
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const words = draft?.trim();
    if (words) onAsk(STOP.drawing, words); else onOpen(STOP);
  };

  return (
    <section className="story" ref={section} style={{ height: `${CHAPTERS * 105}vh` }}>
      <div className={`story-pin tone-${chapter}`}>
        <div className="aurora" aria-hidden><i /><i /><i /></div>
        <div className={`story-stage${chapter === 4 ? " away" : ""}`}>
          {detail?.mesh && (
            <Viewer3D
              key={detail.mesh}
              meshUrl={detail.mesh}
              groundUrl={`/api/ground?drawing=${encodeURIComponent(detail.id)}`}
              imageUrl={detail.view.url}
              highlights={highlights}
              visible={visible}
              hover={null}
              focus={null}
              excluded={NONE}
              focusKey={`${opening ? "o" : "s"}|${act}|${highlights.length}`}
              theme="night"
              bare
              direct={direction}
              onReady={() => { const t = performance.now(); setBuilt(t); setNow(t); }}
            />
          )}
        </div>
        <div className={`story-shade${p < 0.06 ? " opening" : ""}`} />
        {scan && (
          <div className={`story-scan${chapter === 4 ? " on" : ""}`}>
            <svg viewBox={`0 0 ${scan.drawing.view.width} ${scan.drawing.view.height}`} role="img" aria-label="A scanned plan with each window boxed">
              <image href={scan.drawing.view.url ?? undefined} width={scan.drawing.view.width} height={scan.drawing.view.height} />
              {boxes.slice(0, boxed).map((e) => {
                const [x0, y0, x1, y1] = e.bbox!;
                const u = scan.drawing.view.width / 430;
                return (
                  <g key={e.n} className="box">
                    <rect x={x0 - 2 * u} y={y0 - 2 * u} width={x1 - x0 + 4 * u} height={y1 - y0 + 4 * u} rx={3 * u} strokeWidth={2.2 * u} />
                    <circle cx={x0 - 2 * u} cy={y0 - 2 * u} r={7.5 * u} />
                    <text x={x0 - 2 * u} y={y0 - 2 * u + 2.9 * u} textAnchor="middle" fontSize={8.4 * u}>{e.n}</text>
                  </g>
                );
              })}
            </svg>
          </div>
        )}

        {/* The opening: one line, an ask bar that works, and the model answering below it. */}
        <div className={`hero${p < 0.045 ? " on" : ""}`}>
          <p className="tag"><i />Junction 2026 · Stoa · Make Blueprints Talk</p>
          <h1>Ask a drawing.<br /><em>Watch it answer.</em></h1>
          <form className="ask" onSubmit={submit}>
            <div className="bar">
              {canTalk && (
                <button type="button" className="mic" onClick={() => onTalk(STOP)} aria-label="Ask it aloud" title="Ask it aloud">
                  <svg viewBox="0 0 24 24" aria-hidden><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" /></svg>
                </button>
              )}
              <input
                value={draft ?? typed}
                onChange={(e) => setDraft(e.target.value)}
                onFocus={() => setDraft((words) => words ?? "")}
                onBlur={() => setDraft((words) => (words ? words : null))}
                placeholder="Ask this floor plan anything"
                spellCheck={false}
                aria-label="Ask this floor plan a question"
              />
              <button type="submit" className="go">Ask</button>
            </div>
            <div className={`reply${sceneAsked && draft === null ? " on" : ""}${sceneDone ? " done" : ""}`} aria-live="polite">
              <strong>{sceneFigure === null ? " " : formatValue(sceneFigure, scene?.key === "area" ? "m2" : "count")}</strong>
              <span className="unit">{scene?.unit}</span>
              <span className={`verdict ${scene?.result?.state ?? "check"}`}><i />{VERDICT[scene?.result?.state ?? "check"]}</span>
              <em>{scene?.result?.engine ?? ""} · €0 · computed, not generated</em>
            </div>
            <div className="chips">
              <span>Try</span>
              {scenes.map((option, index) => (
                <button type="button" key={option.key} className={draft === null && scene?.key === option.key ? "on" : ""} onClick={() => pick(index)}>{option.chip}</button>
              ))}
            </div>
          </form>
        </div>
        <div className={`hero-foot${p < 0.045 ? " on" : ""}`}>
          <button className="cue" onClick={() => go(1)}><i />Scroll to see how</button>
          <button className="play" onClick={() => setPlaying(true)}>
            <svg viewBox="0 0 24 24" aria-hidden><path d="M8 5.5v13l10.5-6.500z" /></svg>Let it tell its story
          </button>
        </div>

        <div className="story-copy">
          <article className={chapter === 1 ? "on" : ""}>
            <p className="step">01</p>
            <h2>To a vision model, a drawing is just pixels.</h2>
            {guess?.answer != null && (
              <div className="guess">
                <strong>{String(guess.answer)}</strong>
                <span><b>doors, it says.</b> There are {doorItems.length}. Nothing on the screen shows which ones it missed.</span>
              </div>
            )}
            <p className="sub small">It looks at the picture and guesses. We read the drawing's own geometry instead: its walls, rooms and doors.</p>
          </article>

          <article className={chapter === 2 ? "on" : ""}>
            <p className="step">02</p>
            <h2>Count. Don't guess.</h2>
            <div className={`live asked${doorsDone ? " done" : ""}`}>
              <p className="said"><span>How many doors are on this floor?</span></p>
              <div className="reply">
                <strong>{doors ? (act === "doors" ? shown : doorItems.length) : " "}</strong>
                <span className="unit">doors</span>
                <span className={`verdict ${doors?.state ?? "check"}`}><i />{VERDICT[doors?.state ?? "check"]}</span>
              </div>
              <p className="how">{doors?.engine ?? "Geometry"} · €0 · counted from the drawing, not generated</p>
            </div>
            <p className="sub small">Every door is an outline in the drawing. Each one opens as it is counted, numbered where you can check it.</p>
          </article>

          <article className={chapter === 3 ? "on" : ""}>
            <p className="step">03</p>
            <h2>Measure. Don't estimate.</h2>
            <div className={`live asked${areaDone && area ? " done" : ""}`}>
              <p className="said"><span>What is the net area of apartment {flat ?? "B"}?</span></p>
              <div className="reply">
                <strong>{area ? formatValue(areaDone && typeof area.answer === "number" ? area.answer : sum, "m2") : " "}</strong>
                <span className="unit">m²</span>
                <span className={`verdict ${area?.state ?? "check"}`}><i />{VERDICT[area?.state ?? "check"]}</span>
              </div>
              <p className="how sum">{terms.length ? terms.join(" + ") : " "}</p>
            </div>
            <p className="sub small">Every room is a polygon. Its area is computed and added up, balconies left out as the question asks.</p>
          </article>

          <article className={chapter === 4 ? "on" : ""}>
            <p className="step">04</p>
            <h2>On a scan, the model points. The code counts.</h2>
            <div className={`live asked${boxed >= boxes.length && boxes.length ? " done" : ""}`}>
              <p className="said"><span>How many windows are on this plan?</span></p>
              <div className="reply">
                <strong>{scan ? boxed : " "}</strong>
                <span className="unit">windows</span>
                <span className={`verdict ${scan?.answer.state ?? "check"}`}><i />{VERDICT[scan?.answer.state ?? "check"]}</span>
              </div>
              <p className="how">Gemini boxes each one · the code counts the boxes · about €0.007</p>
            </div>
            <p className="sub small">A scan has no geometry to read. Even then the number is not generated: it is the count of boxes you can see, and it is never marked Verified.</p>
          </article>

          <article className={chapter === 5 ? "on" : ""}>
            <p className="step">05</p>
            <h2>It says how far to trust it.</h2>
            <ul className="states">
              <li className="verified"><b><i />Verified</b><span>No need to double-check. {proof ? <>On drawings held back for testing, answers like this were wrong <em>{proof.calibration_errors} times in {proof.calibration_answers.toLocaleString("en")}</em>.</> : "Measured on drawings held back for testing."}</span></li>
              <li className="check"><b><i />Check this</b><span>The answer with its evidence marked, and the reason it needs a look.</span></li>
              <li className="declined"><b><i />Can't answer reliably</b><span>No number, and why. No answer is better than a wrong one.</span></li>
            </ul>
          </article>

          <article className={chapter === 6 ? "on" : ""}>
            <p className="step">06</p>
            <h2>Measured, not claimed.</h2>
            <div className="scores">
              <div><strong>{pct(ours?.accuracy)}</strong><span>correct on {ours?.n ?? 200} questions. A vision model alone: <b>{pct(alone?.accuracy)}</b></span></div>
              <div><strong>{euros(ours?.eur_per_question)}</strong><span>per 1,000 questions. A vision model alone: <b>{euros(alone?.eur_per_question)}</b></span></div>
              <div><strong>{engines?.verified_wrong ?? 0}</strong><span>wrong among <b>{engines?.verified?.toLocaleString("en") ?? "—"}</b> Verified answers</span></div>
            </div>
            <div className="actions">
              <button className="primary" onClick={() => onOpen(STOP)}>Try it on this drawing</button>
              <button className="quiet" onClick={onBrowse}>Browse all drawings →</button>
              <label className="quiet bring">
                Bring your own →
                <input type="file" accept="image/*,application/pdf" onChange={(e) => { const file = e.target.files?.[0]; if (file) onBring(file); e.target.value = ""; }} />
              </label>
            </div>
          </article>
        </div>

        {playing && <button className="story-stop" onClick={() => setPlaying(false)}>Playing · press any key to stop</button>}
        <nav className="story-rail" aria-label="Chapters">
          {Array.from({ length: CHAPTERS }, (_, i) => (
            <button key={i} className={i === chapter ? "on" : ""} onClick={() => go(i)} aria-label={`Chapter ${i + 1}`} />
          ))}
        </nav>
        <p className="story-credit">{(chapter === 4 ? scan?.drawing.title : detail?.title) ?? ""} · a real drawing from the dataset, {chapter === 4 ? "its logged answer shown" : "answered live"}</p>
      </div>
    </section>
  );
}
