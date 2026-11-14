import { useState } from "react";
import { type AskResult, type DrawingDetail, type Evidence, euro, formatValue, unitLabel } from "./api";
import { boxOf } from "./Viewer2D";

interface Props {
  result: AskResult | null;
  asking: boolean;
  error: string | null;
  shown: number | string | null;  // the value while evidence is still being marked
  settled: boolean;
  adjusted: number | null;  // the value after the reader removed items, if they did
  marked: number;  // how many evidence items have been marked on the sheet so far
  excluded: Set<number>;
  hover: number | null;
  focus: number | null;
  view: DrawingDetail["view"] | null;
  starters: string[];  // questions to offer before anything has been asked
  onHover(n: number | null): void;
  onToggle(n: number): void;
  onFocus(n: number | null): void;
  onRestore(): void;
  onUseModel(): void;
  onAsk(question: string): void;
  onShare: (() => void) | null;  // an evidence sheet of this answer, when the drawing has a 2D sheet
  sharing: boolean;
}

const STATE = {
  verified: { label: "Verified", line: "No need to double-check." },
  check: { label: "Check this", line: "The evidence is marked on the drawing." },
  declined: { label: "Can't answer reliably", line: "No answer is better than a wrong one." },
} as const;

const pct = (value: number) => `${(100 * value).toFixed(value < 0.01 ? 2 : 1)}%`;
const time = (ms: number) => (ms < 1000 ? `${Math.max(1, Math.round(ms))} ms` : `${(ms / 1000).toFixed(1)} s`);

// Why a measured kind of answer is still not Verified: say what was measured.
function notVerified(result: AskResult): string {
  const levels = [...(result.family?.levels ?? [])].sort((a, b) => b.score - a.score);
  const level = levels.filter((l) => result.confidence == null || l.score <= result.confidence + 1e-9)[0] ?? levels[levels.length - 1];
  if (!level) return "This kind of answer has not been measured on held-back drawings yet.";
  const rate = level.answers ? level.errors / level.answers : 0;
  return `On held-back drawings, answers of this kind with these checks were wrong ${level.errors.toLocaleString("en")} times in ${level.answers.toLocaleString("en")}. ` +
    (rate <= 0.01
      ? "That is too few answers to prove fewer than 1% are wrong, so it is not marked Verified yet."
      : `That is ${(100 * rate).toFixed(1)}%, above our 1% bar, so it is never marked Verified.`);
}

function why(result: AskResult): { title: string; text: React.ReactNode } {
  const v = result.verified_because;
  const two = result.readings;  // a scan read twice: the printed labels, and a vision model's own count
  if (result.state === "verified" && v) {
    return {
      title: "Why you can skip the check",
      text: (
        <>
          {two ? "Two readings give the same count: the room names printed on the plan, read by local OCR, and the rooms the vision model marked on its own. " : null}
          On drawings held back for measuring, answers of this kind that passed the same checks were wrong{" "}
          <b>{v.calibration_errors.toLocaleString("en")}</b> times in <b>{v.calibration_answers.toLocaleString("en")}</b>.
          The error rate is below <b>{pct(v.error_upper_bound)}</b> with 95% confidence. Our bar is 1%.
        </>
      ),
    };
  }
  if (result.state === "check") {
    const bar = result.family?.threshold;
    if (two && two.labels !== two.model) {
      return {
        title: "Why it needs a look",
        text: `Two readings of this plan differ: the printed labels give ${two.labels}, the vision model marked ${two.model}. Both are drawn on the sheet, so you can see which is right.`,
      };
    }
    if (two) {
      return { title: "Why it needs a look", text: `The printed labels and the vision model give the same count. ${notVerified(result)}` };
    }
    if (result.coinciding) {
      const { extra, distinct, outlines, counted } = result.coinciding;
      const what = /door/i.test(result.interpreted ?? result.question) ? "door" : /window/i.test(result.interpreted ?? result.question) ? "window" : "item";
      return {
        title: "Why it needs a look",
        text: counted === "distinct"
          ? `The drawing's file holds ${outlines} ${what} outlines, and ${extra} of them lie exactly on top of another: a ${what} between two units is listed once for each. That makes ${distinct} ${what}s, and the answer counts each one once. The dataset's own answer counts outlines (${outlines}), so no measured error rate covers this count and it is not marked Verified.`
          : `The drawing's file holds ${extra} of these outlines twice, exactly on top of another (a ${what} between two units is listed for each). This answer counts outlines, as the dataset does: ${outlines}. Counted once per place it is ${distinct}. Which one you need depends on what you are ordering, so it is not marked Verified.`,
      };
    }
    if (result.restated?.question) {
      return {
        title: "Why it needs a look",
        text: `The question was restated by a language model (${result.restated.model}) before the engine could read it. The number is computed from the drawing as usual, but an answer that rests on a model's reading of the question is never marked Verified.`,
      };
    }
    return {
      title: "Why it needs a look",
      text: bar != null && result.confidence != null
        ? `Its checks score ${result.confidence.toFixed(2)}; answers of this kind are Verified from ${bar.toFixed(2)}.`
        : result.family
          ? notVerified(result)
          : result.method.startsWith("vlm")
            ? `A vision model read this from pixels. Its items are marked so you can check each one; it is never marked Verified.${
                result.confidence != null ? ` The model's own estimate that its list is exactly right: ${Math.round(100 * result.confidence)}%.` : ""}`
            : "This kind of answer has not been measured on held-back drawings yet.",
    };
  }
  return { title: "Why there is no answer", text: result.reason || "The drawing does not support a reliable answer." };
}

// One counted item, cut out of the drawing itself: a reviewer checks the whole count at a glance.
function Crop({ item, view }: { item: Evidence; view: DrawingDetail["view"] }) {
  const box = boxOf(item)!;
  const side = Math.max(box[2] - box[0], box[3] - box[1]) * 1.9 + Math.max(view.width, view.height) * 0.018 + 16;
  const cx = (box[0] + box[2]) / 2, cy = (box[1] + box[3]) / 2;
  const k = 64 / side;
  return (
    <i
      className="crop"
      style={{
        backgroundImage: `url(${view.url})`,
        backgroundSize: `${view.width * k}px ${view.height * k}px`,
        backgroundPosition: `${-(cx - side / 2) * k}px ${-(cy - side / 2) * k}px`,
      }}
    />
  );
}

// How the answer was worked out: the question as read, the method and why, the evidence, the checks.
function Steps({ result, marked }: { result: AskResult; marked: number }) {
  const method = result.method;
  const route = method.startsWith("dxf") ? "This plan comes with a DXF, so its geometry answers the question exactly, at no cost."
    : method.startsWith("ifc") ? "This drawing is a building model, or was rendered from one, so a query on the model answers exactly, at no cost."
    : method.startsWith("raster-labels") ? "A scan with no vector data: the room labels printed on it are read by a local OCR model, at no cost."
    : method.startsWith("labels+model") ? "A scan with no vector data, read twice: the room labels printed on it by a local OCR model, and the rooms themselves by a vision model."
    : method.startsWith("vlm") ? "No exact engine covers this, so a vision model points at each item. This is the paid path."
    : "No exact engine covers this kind of drawing.";
  const total = result.evidence.filter((e) => e.kind !== "formula" && e.kind !== "context").length;
  const formula = result.evidence.find((e) => e.kind === "formula");
  const steps: { title: string; body?: React.ReactNode }[] = [
    {
      title: "Read the question",
      body: result.restated?.question
        ? <>Restated by {result.restated.model} as “{result.restated.question}”{result.interpreted ? <>, then read as: {result.interpreted}</> : null}</>
        : result.restated
          ? <>The rules could not read it, and {result.restated.model} found no engine question for it: {result.restated.reason}</>
          : result.interpreted ?? (method.startsWith("vlm") ? "Passed on as written, with the counting rules it states." : "Taken as written."),
    },
    { title: `Chose the method: ${result.engine}`, body: route },
  ];
  if (result.answer !== null) {
    steps.push({
      title: total ? `Marked the evidence: ${Math.min(marked, total)} of ${total}` : "Found nothing of this kind",
      body: formula?.text ?? (total ? "Each item is drawn on the sheet and listed below. Any of them can be left out." : undefined),
    });
    if (result.checks.length) {
      steps.push({
        title: "Ran the checks",
        body: (
          <ul className="checks">
            {result.checks.map((c) => (
              <li key={c.name}><span>{c.name}</span><b>{typeof c.value === "number" ? c.value.toLocaleString("en", { maximumFractionDigits: 2 }) : c.value}</b></li>
            ))}
          </ul>
        ),
      });
    }
  }
  return (
    <section className="block steps">
      <h3>How it was worked out</h3>
      <ol>
        {steps.map((step, i) => (
          <li key={step.title.split(":")[0]}>
            <i>{String(i + 1).padStart(2, "0")}</i>
            <div>
              <strong>{step.title}</strong>
              {step.body && <div className="body">{step.body}</div>}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

// This system beside a vision model answering alone: the answer, what it cost, how long it took.
function Compare({ result }: { result: AskResult }) {
  const base = result.baseline;
  const reference = result.reference;
  const unit = result.unit ?? reference?.unit ?? null;
  const mark = (status: string | undefined) => (status === "correct" ? "right" : status === "wrong" ? "wrong" : "");
  // Each place counted once, where the dataset counts every outline: the two numbers answer different questions.
  const twice = result.coinciding?.counted === "distinct" && reference?.answer === result.coinciding.outlines ? result.coinciding : null;
  return (
    <section className="block compare">
      <table>
        <thead>
          <tr><th /><th>Answer</th><th>Cost</th><th>Time</th></tr>
        </thead>
        <tbody>
          <tr className="ours">
            <td>{result.engine}{result.replayed_from ? ", logged" : ""}</td>
            <td className={twice ? "" : mark(reference?.status)}>{result.answer === null ? "—" : formatValue(result.answer, unit)}</td>
            <td>{euro(result.cost_eur)}</td>
            <td>{time(result.latency_ms)}</td>
          </tr>
          {base && (
            <tr>
              <td>{base.model ?? "Vision model"} alone</td>
              <td className={mark(base.status)}>{base.answer === null ? "—" : formatValue(base.answer as number, unit)}</td>
              <td>{euro(base.cost_eur)}</td>
              <td>{time(base.latency_ms)}</td>
            </tr>
          )}
          {reference && (
            <tr className="reference">
              <td>Dataset's answer</td>
              <td>{formatValue(reference.answer as number, reference.unit)}</td>
              <td colSpan={2}>{twice ? `counts outlines: ${twice.extra} lie on another` : reference.status === "correct" ? "matches ours" : reference.status === "wrong" ? "differs from ours" : reference.status === "abstained" ? "we did not answer" : "not graded"}</td>
            </tr>
          )}
        </tbody>
      </table>
    </section>
  );
}

function EvidenceBlock(props: Pick<Props, "excluded" | "hover" | "focus" | "view" | "onHover" | "onToggle" | "onFocus"> & { items: Evidence[] }) {
  const { items, excluded, hover, focus, view, onHover, onToggle, onFocus } = props;
  const [all, setAll] = useState(false);
  const countable = items.filter((e) => e.kind !== "formula" && e.kind !== "context");
  const context = items.filter((e) => e.kind === "context");
  const measured = countable.some((e) => e.area_m2 != null || e.volume_m3 != null || e.length_m != null);
  if (!countable.length && !context.length) return null;
  const onSheet = !!view?.url && countable.length > 0 && countable.every((e) => boxOf(e));
  const limit = measured ? 12 : onSheet ? 48 : 80;
  const visible = all ? countable : countable.slice(0, limit);
  const at = focus !== null ? countable.findIndex((e) => e.n === focus) : -1;
  return (
    <section className="block evidence">
      <header>
        <h3>Evidence</h3>
        {countable.length > 0 && <span>{countable.length} item{countable.length === 1 ? "" : "s"} · select one to leave it out</span>}
      </header>
      {onSheet && countable.length > 1 && (
        <div className="inspect">
          {at === -1 ? (
            <button onClick={() => onFocus(countable[0].n!)}>Inspect one by one →</button>
          ) : (
            <>
              <button onClick={() => onFocus(countable[(at - 1 + countable.length) % countable.length].n!)} aria-label="Previous item">←</button>
              <span>{at + 1} of {countable.length}</span>
              <button onClick={() => onFocus(countable[(at + 1) % countable.length].n!)} aria-label="Next item">→</button>
              <button onClick={() => onToggle(focus!)}>{excluded.has(focus!) ? "Put back" : "Leave out"}</button>
              <button onClick={() => onFocus(null)}>Done</button>
            </>
          )}
        </div>
      )}
      {measured ? (
        <ul className="rows">
          {visible.map((e) => (
            <li
              key={e.n}
              className={`${hover === e.n || focus === e.n ? "hot" : ""}${excluded.has(e.n!) ? " off" : ""}`}
              onPointerEnter={() => onHover(e.n!)}
              onPointerLeave={() => onHover(null)}
              onClick={() => onToggle(e.n!)}
            >
              <i>{e.n}</i>
              <span>{e.label || e.cls}</span>
              <b>{e.area_m2 != null ? `${e.area_m2.toFixed(2)} m²` : e.volume_m3 != null ? `${e.volume_m3.toFixed(2)} m³` : e.length_m != null ? `${e.length_m.toFixed(2)} m` : ""}</b>
            </li>
          ))}
        </ul>
      ) : onSheet ? (
        <div className="contact">
          {visible.map((e) => (
            <button
              key={e.n}
              title={e.label || e.cls || ""}
              className={`${hover === e.n || focus === e.n ? "hot" : ""}${excluded.has(e.n!) ? " off" : ""}`}
              onPointerEnter={() => onHover(e.n!)}
              onPointerLeave={() => onHover(null)}
              onClick={() => onToggle(e.n!)}
              onDoubleClick={() => onFocus(e.n!)}
            >
              <Crop item={e} view={view!} />
              <em>{e.n}</em>
            </button>
          ))}
        </div>
      ) : (
        <div className="chips">
          {visible.map((e) => (
            <button
              key={e.n}
              title={e.label || e.cls || ""}
              className={`${hover === e.n ? "hot" : ""}${excluded.has(e.n!) ? " off" : ""}`}
              onPointerEnter={() => onHover(e.n!)}
              onPointerLeave={() => onHover(null)}
              onClick={() => onToggle(e.n!)}
            >
              {e.n}
            </button>
          ))}
        </div>
      )}
      {countable.length > limit && (
        <button className="more" onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show all ${countable.length}`}</button>
      )}
      {context.length > 0 && (
        <ul className="context">
          {context.map((e, i) => (
            <li key={i}>
              <b>{e.label}</b>
              <span>{e.role === "not counted" ? `read but not counted${e.why ? `: ${e.why}` : ""}` : e.role}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function AnswerPanel(props: Props) {
  const { result, asking, error, shown, settled, adjusted, excluded } = props;
  if (error) {
    return (
      <div className="answer">
        <div className="lead"><h2>Something went wrong</h2><p>{error}</p></div>
      </div>
    );
  }
  if (!result) {
    return (
      <div className="answer">
        <div className={`lead${asking ? " busy" : ""}`}>
          <h2>{asking ? "Reading the drawing" : "Ask this drawing anything."}</h2>
          <p>
            Counts, areas and volumes are computed from the drawing's own geometry and marked on the sheet, so you can
            check an answer in seconds. Each one comes back Verified, Check this, or Can't answer reliably.
          </p>
          {!asking && props.starters.length > 0 && (
            <ul className="starters">
              {props.starters.map((text) => (
                <li key={text}><button onClick={() => props.onAsk(text)}><span>{text}</span><i>→</i></button></li>
              ))}
            </ul>
          )}
        </div>
      </div>
    );
  }
  const state = STATE[result.state];
  const unit = unitLabel(result.unit);
  const reason = why(result);
  return (
    <article className={`answer state-${result.state}${asking ? " stale" : ""}`}>
      <p className="asked">{result.question}</p>

      <div className={`result-card ${result.state}${settled ? " settled" : ""}`}>
      <div className="verdict">
        <span className="pill"><i />{state.label}
          {settled && result.state === "verified" && (
            <span className="burst" aria-hidden>{Array.from({ length: 14 }, (_, i) => <b key={i} style={{ "--a": `${i * 25.7 + 8}deg`, "--d": `${46 + (i % 4) * 16}px`, "--c": ["#ffffff", "#8be28f", "#ffd45e", "#ff9a5c"][i % 4] } as React.CSSProperties} />)}</span>
          )}
        </span>
        <span className="line">{state.line}</span>
        {props.onShare && result.answer !== null && (
          <button className="share" onClick={props.onShare} disabled={props.sharing} title="A single image of this answer with its evidence marked, to send or attach">
            {props.sharing ? "Making the sheet…" : "Share ↗"}
          </button>
        )}
      </div>

      <div className="figure" aria-live="polite">
        {result.answer === null ? <span className="none">No number</span> : (
          <>
            <span className={settled ? "settled" : ""}>{formatValue(shown, result.unit)}</span>
            {unit && <em>{unit}</em>}
          </>
        )}
      </div>
      {result.explanation && <p className="explain">{result.explanation}</p>}
      {result.coinciding && settled && (result.coinciding.counted === "distinct" ? (
        <p className="twice">The file holds <b>{result.coinciding.outlines}</b> outlines, {result.coinciding.extra} exactly on another. The dataset's answer counts outlines: {result.coinciding.outlines}.</p>
      ) : (
        <p className="twice"><b>{result.coinciding.distinct}</b> distinct places: {result.coinciding.extra} outline{result.coinciding.extra === 1 ? " is" : "s are"} in the file twice.</p>
      ))}
      {adjusted !== null && (
        <p className="adjusted">
          Without the {excluded.size} item{excluded.size === 1 ? "" : "s"} you left out:{" "}
          <b>{formatValue(adjusted, result.unit)}{unit ? ` ${unit}` : ""}</b>
          <button onClick={props.onRestore}>Put back</button>
        </p>
      )}
      </div>

      {settled && (
        <section className="proof">
          <h3>{reason.title}</h3>
          <p>{reason.text}</p>
          {result.can_use_model && (
            <div className="fallback">
              {result.can_confirm ? (
                <p>
                  This count comes from the labels printed on the plan. A vision model can count the rooms on its own
                  for about a cent: two readings that agree are a stronger answer than either alone.
                </p>
              ) : (
                <p>
                  {result.answer === null ? "No exact engine covers this question on a scan." : "The printed labels alone are a weak source here."}{" "}
                  A vision model can mark each item for about a cent. Its answer is always marked Check this.
                </p>
              )}
              <button onClick={props.onUseModel} disabled={asking}>{result.can_confirm ? "Count again with the vision model" : "Ask the vision model"}</button>
            </div>
          )}
        </section>
      )}

      <Compare result={result} />
      <Steps result={result} marked={props.marked} />
      <EvidenceBlock
        items={result.evidence}
        excluded={excluded}
        hover={props.hover}
        focus={props.focus}
        view={props.view}
        onHover={props.onHover}
        onToggle={props.onToggle}
        onFocus={props.onFocus}
      />
    </article>
  );
}
