import { useState } from "react";
import { type AskResult, type DrawingDetail, type Evidence, euro, formatValue, unitLabel } from "./api";
import { boxOf, type Theme } from "./Viewer2D";

interface Props {
  result: AskResult | null;
  asking: boolean;
  error: string | null;
  shown: number | string | null;  // the value while evidence is still landing
  settled: boolean;
  adjusted: number | null;  // the value after the reader removed items, if they did
  excluded: Set<number>;
  hover: number | null;
  focus: number | null;
  view: DrawingDetail["view"] | null;
  theme: Theme;
  starters: string[];  // questions to offer before anything has been asked
  onHover(n: number | null): void;
  onToggle(n: number): void;
  onFocus(n: number | null): void;
  onRestore(): void;
  onUseModel(): void;
  onAsk(question: string): void;
}

const STATE = {
  verified: { label: "Verified", line: "No need to double-check." },
  check: { label: "Check this", line: "The evidence is on the drawing." },
  declined: { label: "Can't answer reliably", line: "No answer is better than a wrong one." },
} as const;

function StateIcon({ state }: { state: keyof typeof STATE }) {
  if (state === "verified")
    return <svg viewBox="0 0 24 24" aria-hidden><path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" /></svg>;
  if (state === "check")
    return <svg viewBox="0 0 24 24" aria-hidden><circle cx="11" cy="11" r="6.2" fill="none" stroke="currentColor" strokeWidth="2.4" /><path d="M16 16l4.5 4.5" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" /></svg>;
  return <svg viewBox="0 0 24 24" aria-hidden><circle cx="12" cy="12" r="8.2" fill="none" stroke="currentColor" strokeWidth="2.4" /><path d="M6.5 17.5l11-11" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" /></svg>;
}

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
  if (result.state === "verified" && v) {
    return {
      title: "Why you can skip the check",
      text: (
        <>
          On drawings held back for measuring, answers of this kind that passed the same checks were wrong{" "}
          <b>{v.calibration_errors.toLocaleString("en")}</b> times in <b>{v.calibration_answers.toLocaleString("en")}</b>.
          The error rate is below <b>{pct(v.error_upper_bound)}</b> with 95% confidence. Our bar is 1%.
        </>
      ),
    };
  }
  if (result.state === "check") {
    const bar = result.family?.threshold;
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
function Crop({ item, view, theme }: { item: Evidence; view: DrawingDetail["view"]; theme: Theme }) {
  const box = boxOf(item)!;
  const side = Math.max(box[2] - box[0], box[3] - box[1]) * 1.9 + Math.max(view.width, view.height) * 0.018 + 16;
  const cx = (box[0] + box[2]) / 2, cy = (box[1] + box[3]) / 2;
  const k = 58 / side;
  return (
    <span className={`crop theme-${theme}`}>
      <i style={{
        backgroundImage: `url(${view.url})`,
        backgroundSize: `${view.width * k}px ${view.height * k}px`,
        backgroundPosition: `${-(cx - side / 2) * k}px ${-(cy - side / 2) * k}px`,
      }} />
    </span>
  );
}

function EvidenceBlock(props: Pick<Props, "excluded" | "hover" | "focus" | "view" | "theme" | "onHover" | "onToggle" | "onFocus"> & { items: Evidence[] }) {
  const { items, excluded, hover, focus, view, theme, onHover, onToggle, onFocus } = props;
  const [all, setAll] = useState(false);
  const countable = items.filter((e) => e.kind !== "formula" && e.kind !== "context");
  const context = items.filter((e) => e.kind === "context");
  const formula = items.find((e) => e.kind === "formula");
  const measured = countable.some((e) => e.area_m2 != null || e.volume_m3 != null || e.length_m != null);
  if (!countable.length && !formula && !context.length) return null;
  const onSheet = !!view?.url && countable.length > 0 && countable.every((e) => boxOf(e));
  const limit = measured ? 14 : onSheet ? 60 : 90;
  const visible = all ? countable : countable.slice(0, limit);
  const at = focus !== null ? countable.findIndex((e) => e.n === focus) : -1;
  return (
    <section className="tb-cell evidence">
      <h3>
        Evidence
        <span>{countable.length ? `${countable.length} item${countable.length === 1 ? "" : "s"} · click one to leave it out` : ""}</span>
      </h3>
      {onSheet && countable.length > 1 && (
        <div className="inspect">
          {at === -1 ? (
            <button onClick={() => onFocus(countable[0].n!)}>Inspect one by one</button>
          ) : (
            <>
              <button onClick={() => onFocus(countable[(at - 1 + countable.length) % countable.length].n!)} aria-label="Previous item">←</button>
              <span><b>{at + 1}</b> of {countable.length}</span>
              <button onClick={() => onFocus(countable[(at + 1) % countable.length].n!)} aria-label="Next item">→</button>
              <button className={excluded.has(focus!) ? "on" : ""} onClick={() => onToggle(focus!)}>{excluded.has(focus!) ? "Put back" : "Not one"}</button>
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
              <b>
                {e.area_m2 != null ? `${e.area_m2.toFixed(2)} m²` : e.volume_m3 != null ? `${e.volume_m3.toFixed(2)} m³` : e.length_m != null ? `${e.length_m.toFixed(2)} m` : ""}
              </b>
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
              <Crop item={e} view={view!} theme={theme} />
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
      {formula && <p className="formula">{formula.text}</p>}
      {context.length > 0 && (
        <ul className="context">
          {context.map((e, i) => (
            <li key={i} className={e.role === "not counted" ? "doubt" : "support"}>
              <b>{e.label}</b>
              <span>{e.role === "not counted" ? `read but not counted${e.why ? `: ${e.why}` : ""}` : e.role}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// This system against a vision model answering alone: the answer, what it cost, how long it took.
function Duel({ result }: { result: AskResult }) {
  const base = result.baseline;
  const reference = result.reference;
  const unit = result.unit ?? reference?.unit ?? null;
  const longest = Math.max(result.latency_ms, base?.latency_ms ?? 0, 1);
  const verdict = (status: string | undefined) => (status === "correct" ? "right" : status === "wrong" ? "wrong" : "");
  return (
    <section className="tb-cell duel">
      <div className="duel-row ours">
        <span className="who">{result.engine}{result.replayed_from ? " · logged" : ""}</span>
        <b className={`said ${verdict(reference?.status)}`}>{result.answer === null ? "—" : formatValue(result.answer, unit)}</b>
        <b>{euro(result.cost_eur)}</b>
        <b>{time(result.latency_ms)}</b>
        <i className="bar"><i style={{ width: `${Math.max(1.5, (100 * result.latency_ms) / longest)}%` }} /></i>
      </div>
      {base && (
        <div className="duel-row theirs">
          <span className="who">{base.model ?? "Vision model"} alone</span>
          <b className={`said ${verdict(base.status)}`}>{base.answer === null ? "—" : formatValue(base.answer as number, unit)}</b>
          <b>{euro(base.cost_eur)}</b>
          <b>{time(base.latency_ms)}</b>
          <i className="bar"><i style={{ width: `${Math.max(1.5, (100 * base.latency_ms) / longest)}%` }} /></i>
        </div>
      )}
      {reference && (
        <div className={`duel-ref ${reference.status}`}>
          <span>Dataset's answer</span>
          <b>{formatValue(reference.answer as number, reference.unit)}{unitLabel(reference.unit) ? ` ${unitLabel(reference.unit)}` : ""}</b>
          <em>{reference.status === "correct" ? "matches" : reference.status === "wrong" ? "differs" : reference.status === "abstained" ? "not answered" : "not graded"}</em>
        </div>
      )}
    </section>
  );
}

export function AnswerPanel(props: Props) {
  const { result, asking, error, shown, settled, adjusted, excluded } = props;
  if (error) {
    return (
      <aside className="panel">
        <div className="empty"><h2>Something went wrong</h2><p>{error}</p></div>
      </aside>
    );
  }
  if (!result) {
    return (
      <aside className="panel">
        <div className={`empty${asking ? " busy" : ""}`}>
          <span className="eyebrow">Make blueprints talk</span>
          <h2>{asking ? "Reading the drawing…" : "Ask the drawing."}</h2>
          <p>
            Every answer comes back <b className="s-verified">Verified</b>, <b className="s-check">Check this</b> or{" "}
            <b className="s-declined">Can't answer reliably</b>, and every counted or measured item is marked on the
            sheet like a reviewer's red pen. Numbers are computed from geometry and objects, never written by a language model.
          </p>
          {!asking && props.starters.length > 0 && (
            <div className="starters">
              <h3>Try one</h3>
              {props.starters.map((text) => (
                <button key={text} onClick={() => props.onAsk(text)}>{text}</button>
              ))}
            </div>
          )}
        </div>
      </aside>
    );
  }
  const state = STATE[result.state];
  const unit = unitLabel(result.unit);
  const reason = why(result);
  return (
    <aside className={`panel state-${result.state}${asking ? " stale" : ""}`}>
      <header className="verdict">
        <div className="badge"><StateIcon state={result.state} /></div>
        <div>
          <strong>{state.label}</strong>
          <span>{state.line}</span>
        </div>
      </header>

      <section className="tb-cell asked">
        <h3>Question</h3>
        <p>{result.question}</p>
      </section>

      <section className="tb-cell answer">
        <h3>Answer</h3>
        <div className="value" aria-live="polite">
          {result.answer === null ? (
            <span className="none">—</span>
          ) : (
            <>
              <span className={`figure${settled ? " settled" : ""}`}>{formatValue(shown, result.unit)}</span>
              {unit && <span className="unit">{unit}</span>}
            </>
          )}
        </div>
        {adjusted !== null && (
          <div className="adjusted">
            Without the {excluded.size} item{excluded.size === 1 ? "" : "s"} you left out:{" "}
            <b>{formatValue(adjusted, result.unit)}{unit ? ` ${unit}` : ""}</b>
            <button onClick={props.onRestore}>Put back</button>
          </div>
        )}
        {result.explanation && <p className="explain">{result.explanation}</p>}
      </section>

      <section className={`tb-cell why ${result.state}`}>
        <h3>{reason.title}</h3>
        <p>{reason.text}</p>
        {result.can_use_model && (
          <div className="fallback">
            <p>
              {result.answer === null ? "No exact engine covers this question on a scan." : "The printed labels alone are a weak source here."}{" "}
              A vision model can point at each item for about a cent; its answer is always marked Check this.
            </p>
            <button onClick={props.onUseModel} disabled={asking}>Ask the vision model</button>
          </div>
        )}
      </section>

      <Duel result={result} />

      {result.checks.length > 0 && (
        <section className="tb-cell checks">
          <h3>Checks run</h3>
          <ul>
            {result.checks.map((c) => (
              <li key={c.name}><span>{c.name}</span><b>{typeof c.value === "number" ? c.value.toLocaleString("en", { maximumFractionDigits: 2 }) : c.value}</b></li>
            ))}
          </ul>
        </section>
      )}

      <EvidenceBlock
        items={result.evidence}
        excluded={excluded}
        hover={props.hover}
        focus={props.focus}
        view={props.view}
        theme={props.theme}
        onHover={props.onHover}
        onToggle={props.onToggle}
        onFocus={props.onFocus}
      />

      {result.interpreted && (
        <section className="tb-cell read-as">
          <h3>Question read as</h3>
          <p>{result.interpreted}</p>
        </section>
      )}
    </aside>
  );
}
