import { useEffect, useMemo, useRef, useState } from "react";
import { api, type AskResult, type DrawingDetail, type Evidence, formatValue, headlineRun, type Run, type Stats } from "./api";
import { type Stop, TOUR } from "./tour";
import { type Direction, type Highlight, Viewer3D } from "./Viewer3D";

// The landing page is one story told by scrolling. A real drawing lies flat, stands up, opens
// its doors and adds up its rooms as the visitor scrolls: the page's scroll position directs the
// model. Every figure on screen is the engine's own answer to a real question, asked on load.

const STOP = TOUR[0];
// A scan, for the chapter where there is no geometry to read: the first example answered by a vision model.
const SCAN = TOUR.find((stop) => stop.drawing.includes("cubicasa") && /window/i.test(stop.question)) ?? TOUR[7];
const NONE = new Set<number>();
const CHAPTERS = 7;
// Where each chapter begins, as a share of the whole scroll:
// opening, it stands up, doors, areas, a scan, how far to trust it, results.
const AT = [0, 0.09, 0.26, 0.45, 0.6, 0.74, 0.87, 1.0001];

const clamp = (x: number) => Math.min(1, Math.max(0, x));
const smooth = (from: number, to: number, x: number) => { const t = clamp((x - from) / (to - from)); return t * t * (3 - 2 * t); };
const linear = (from: number, to: number, x: number) => clamp((x - from) / (to - from));
const overall = (run?: Run) => run?.rows.find((r) => r.scope === "overall");
const pct = (v: number | null | undefined) => (v == null ? "—" : `${(100 * v).toFixed(1)}%`);
const euros = (v: number | null | undefined) => (v == null ? "—" : `€${(1000 * v).toFixed(2)}`);
const counted = (result: AskResult | null): Evidence[] =>
  result ? result.evidence.filter((e) => e.kind !== "formula" && e.kind !== "context" && e.n != null) : [];

interface Props {
  stats: Stats | null;
  onOpen(stop: Stop): void;
  onBrowse(): void;
  onBring(file: File): void;
  onTalk(stop: Stop): void;
  canTalk: boolean;
}

export function Story({ stats, onOpen, onBrowse, onBring, onTalk, canTalk }: Props) {
  const section = useRef<HTMLElement>(null);
  const [p, setP] = useState(0);  // how far through the story, 0 to 1
  const [detail, setDetail] = useState<DrawingDetail | null>(null);
  const [doors, setDoors] = useState<AskResult | null>(null);
  const [area, setArea] = useState<AskResult | null>(null);
  const [flat, setFlat] = useState<string | null>(null);  // which apartment the area question is about
  const [scan, setScan] = useState<{ drawing: DrawingDetail; answer: AskResult } | null>(null);

  // The drawing, and two real questions put to the engine.
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
  const direction: Direction = useMemo(() => ({
    rise: smooth(0.06, 0.23, p),
    tilt: 1.5 - 0.88 * smooth(0.04, 0.26, p) + 0.14 * smooth(0.7, 0.92, p),
    // From square-on over the long side round to its far corner: the long side stays in view throughout.
    turn: Math.PI / 2 + 0.5 * smooth(0.07, 0.29, p) + 0.72 * linear(0.26, 1, p),
    back: 1.2 + 0.1 * smooth(0.2, 0.32, p),
    sway: 0.1 * (1 - smooth(0.01, 0.07, p)),  // before any scroll the walls breathe: it is a model, not a picture
  }), [p]);

  // Playing: the page scrolls itself through the story at a steady pace (for showing it, or filming it).
  const [playing, setPlaying] = useState(false);
  useEffect(() => {
    if (!playing) return;
    const el = section.current;
    if (!el) return;
    const span = el.getBoundingClientRect().height - window.innerHeight;
    const from = window.scrollY, to = el.offsetTop + span;
    const duration = Math.max(4000, 42000 * ((to - from) / span));
    const began = performance.now();
    let frame = 0;
    const step = () => {
      const t = (performance.now() - began) / duration;
      window.scrollTo(0, from + (to - from) * Math.min(1, t));
      if (t >= 1) return setPlaying(false);
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    const stop = () => setPlaying(false);
    window.addEventListener("wheel", stop, { passive: true });
    window.addEventListener("touchstart", stop, { passive: true });
    window.addEventListener("keydown", stop);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("wheel", stop);
      window.removeEventListener("touchstart", stop);
      window.removeEventListener("keydown", stop);
    };
  }, [playing]);

  // Doors open one by one through the third chapter; the apartment's rooms light up through the fourth.
  const doorItems = useMemo(() => counted(doors), [doors]);
  const roomItems = useMemo(() => counted(area), [area]);
  const act = p < AT[3] ? "doors" : p < AT[4] ? "area" : "rest";
  const items = act === "doors" ? doorItems : act === "area" ? roomItems : [];
  const shown = act === "doors" ? Math.round(linear(0.29, 0.41, p) * doorItems.length)
    : act === "area" ? Math.round(linear(0.48, 0.565, p) * roomItems.length) : 0;
  // On the scan, the model's boxes are drawn one by one.
  const boxes = useMemo(() => (scan ? scan.answer.evidence.filter((e) => e.kind === "box" && e.bbox && e.n != null) : []), [scan]);
  const boxed = Math.round(linear(0.63, 0.705, p) * boxes.length);
  const highlights: Highlight[] = useMemo(() => items.flatMap((e) => {
    const id = e.kind === "element" ? e.id : e.ref;
    return id ? [{ id, n: e.n!, tone: e.kind === "polygon" ? "area" as const : "count" as const }] : [];
  }), [items]);
  const visible = useMemo(() => (shown ? new Set(items.slice(0, shown).map((e) => e.n!)) : NONE), [items, shown]);
  const doorsDone = shown >= doorItems.length && doorItems.length > 0;
  const sum = roomItems.slice(0, act === "area" ? shown : roomItems.length).reduce((total, e) => total + (e.area_m2 ?? 0), 0);
  const areaDone = act !== "area" || shown >= roomItems.length;
  const terms = roomItems.slice(0, act === "area" ? shown : roomItems.length).map((e) => (e.area_m2 ?? 0).toFixed(2));

  const runs = stats?.runs ?? [];
  const alone = overall(headlineRun(runs, "alone"));
  const ours = overall(headlineRun(runs, "system"));
  const engines = overall([...runs].reverse().find((r) => r.method === "router:v1" && r.split === "dev" && r.dataset === "junction-dataset")
    ?? [...runs].reverse().find((r) => r.method === "router:v1" && r.split === "dev"));
  const aloneSaid = doors?.baseline?.answer ?? null;
  const proof = doors?.verified_because;

  const go = (to: number) => {
    const el = section.current;
    if (!el) return;
    const span = el.getBoundingClientRect().height - window.innerHeight;
    window.scrollTo({ top: el.offsetTop + span * (AT[to] + 0.012), behavior: "smooth" });
  };

  return (
    <section className="story" ref={section} style={{ height: `${CHAPTERS * 105}vh` }}>
      <div className={`story-pin tone-${chapter}`}>
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
              focusKey={`${act}|${highlights.length}`}
              theme="night"
              bare
              shift={0.2}
              direct={direction}
            />
          )}
        </div>
        <div className="story-shade" />
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

        <div className="story-copy">
          <article className={chapter === 0 ? "on" : ""}>
            <p className="eyebrow">Junction 2026 · Stoa · Make Blueprints Talk</p>
            <h1>Ask a drawing.<br />See where the answer came from.</h1>
            <p className="sub">Counts, areas and volumes computed from the drawing's own geometry, and marked where you can check them.</p>
            <div className="actions">
              <button className="primary" onClick={() => onOpen(STOP)}>Ask this drawing</button>
              {canTalk && (
                <button className="talk" onClick={() => onTalk(STOP)}>
                  <svg viewBox="0 0 24 24" aria-hidden><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" /></svg>
                  Ask it aloud
                </button>
              )}
            </div>
            <div className="cues">
              <button className="cue" onClick={() => go(1)}><i />Scroll: watch this floor plan answer</button>
              <button className="play" onClick={() => setPlaying(true)}>
                <svg viewBox="0 0 24 24" aria-hidden><path d="M8 5.5v13l10.5-6.500z" /></svg>Play the story
              </button>
            </div>
          </article>

          <article className={chapter === 1 ? "on" : ""}>
            <p className="step">01</p>
            <h2>A drawing is not a picture.</h2>
            <p className="sub">
              A vision model squints at the pixels and guesses{aloneSaid != null ? <>: on this floor it says <b>{String(aloneSaid)} doors</b></> : ""}.
              We read the drawing's own geometry instead: its walls, rooms and doors. Keep scrolling and it stands up.
            </p>
          </article>

          <article className={chapter === 2 ? "on" : ""}>
            <p className="step">02</p>
            <h2>Count. Don't guess.</h2>
            <div className={`live asked${doorsDone ? " done" : ""}`}>
              <p className="said"><span>How many doors are on this floor?</span></p>
              <div className="reply">
                <strong>{doors ? shown : " "}</strong>
                <span className="unit">doors</span>
                <span className={`verdict ${doors?.state ?? "check"}`}><i />{doors?.state === "verified" ? "Verified" : "Check this"}</span>
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
                <span className={`verdict ${area?.state ?? "check"}`}><i />{area?.state === "verified" ? "Verified" : "Check this"}</span>
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
                <strong>{scan ? boxed : "\u00a0"}</strong>
                <span className="unit">windows</span>
                <span className={`verdict ${scan?.answer.state ?? "check"}`}><i />{scan?.answer.state === "verified" ? "Verified" : "Check this"}</span>
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
