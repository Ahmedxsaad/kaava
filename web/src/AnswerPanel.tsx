import { useState } from "react";
import { type AskResult, type Evidence, euro, formatValue, unitLabel } from "./api";

interface Props {
  result: AskResult | null;
  asking: boolean;
  error: string | null;
  shown: number | string | null;  // the value while evidence is still landing
  settled: boolean;
  adjusted: number | null;  // the value after the reader removed items, if they did
  excluded: Set<number>;
  hover: number | null;
  onHover(n: number | null): void;
  onToggle(n: number): void;
  onRestore(): void;
  onUseModel(): void;
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

function pct(value: number): string {
  return `${(100 * value).toFixed(value < 0.01 ? 2 : 1)}%`;
}

// Why a measured kind of answer is still not Verified: say what was measured.
function notVerified(result: AskResult): string {
  const levels = [...(result.family?.levels ?? [])].sort((a, b) => b.score - a.score);
  const level = levels.filter((l) => result.confidence == null || l.score <= result.confidence + 1e-9)[0] ?? levels[levels.length - 1];
  if (!level) return "This kind of answer has not been measured on held-back drawings yet.";
  const rate = level.answers ? level.errors / level.answers : 0;
  const few = rate <= 0.01;
  return `On held-back drawings, answers of this kind with these checks were wrong ${level.errors.toLocaleString("en")} times in ${level.answers.toLocaleString("en")}. ` +
    (few ? "That is too few answers to prove fewer than 1% are wrong, so it is not marked Verified yet."
         : `That is ${(100 * rate).toFixed(1)}%, above our 1% bar, so it is never marked Verified.`);
}

function Why({ result }: { result: AskResult }) {
  const v = result.verified_because;
  if (result.state === "verified" && v) {
    return (
      <section className="why verified">
        <h3>Why you can skip the check</h3>
        <p>
          On drawings held back for measuring, answers of this kind that passed the same checks were wrong{" "}
          <b>{v.calibration_errors.toLocaleString("en")}</b> times in <b>{v.calibration_answers.toLocaleString("en")}</b>.
          The error rate is below <b>{pct(v.error_upper_bound)}</b> with 95% confidence. Our bar is 1%.
        </p>
      </section>
    );
  }
  if (result.state === "check") {
    const family = result.family;
    const bar = family?.threshold;
    return (
      <section className="why check">
        <h3>Why it needs a look</h3>
        <p>
          {bar != null && result.confidence != null
            ? `Its checks score ${result.confidence.toFixed(2)}; answers of this kind are Verified from ${bar.toFixed(2)}.`
            : family
              ? notVerified(result)
              : result.method.startsWith("vlm")
                ? `A vision model read this from pixels. Its items are drawn so you can check each one; it is never marked Verified.${
                    result.confidence != null ? ` The model's own estimate that its list is exactly right: ${Math.round(100 * result.confidence)}%.` : ""}`
                : "This kind of answer has not been measured on held-back drawings yet."}
        </p>
      </section>
    );
  }
  return (
    <section className="why declined">
      <h3>Why there is no answer</h3>
      <p>{result.reason || "The drawing does not support a reliable answer."}</p>
    </section>
  );
}

function EvidenceList({ items, excluded, hover, onHover, onToggle }: {
  items: Evidence[];
  excluded: Set<number>;
  hover: number | null;
  onHover(n: number | null): void;
  onToggle(n: number): void;
}) {
  const [all, setAll] = useState(false);
  const countable = items.filter((e) => e.kind !== "formula" && e.kind !== "context");
  const context = items.filter((e) => e.kind === "context");
  const formula = items.find((e) => e.kind === "formula");
  const measured = countable.some((e) => e.area_m2 != null || e.volume_m3 != null || e.length_m != null);
  if (!countable.length && !formula && !context.length) return null;
  const limit = measured ? 14 : 90;
  const visible = all ? countable : countable.slice(0, limit);
  return (
    <section className="evidence">
      <h3>
        Evidence <span>{countable.length ? `${countable.length} item${countable.length === 1 ? "" : "s"} · click one to leave it out` : ""}</span>
      </h3>
      {measured ? (
        <ul className="rows">
          {visible.map((e) => (
            <li
              key={e.n}
              className={`${hover === e.n ? "hot" : ""}${excluded.has(e.n!) ? " off" : ""}`}
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
        <button className="more" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show all ${countable.length}`}
        </button>
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

export function AnswerPanel(props: Props) {
  const { result, asking, error, shown, settled, adjusted, excluded, hover } = props;
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
          <h2>{asking ? "Working it out…" : "Ask the drawing"}</h2>
          <p>
            Every answer comes back as <b className="s-verified">Verified</b>, <b className="s-check">Check this</b> or{" "}
            <b className="s-declined">Can't answer reliably</b>, with the items it counted or measured drawn on the plan.
            Numbers are computed from the drawing's geometry and objects, never written by a language model.
          </p>
        </div>
      </aside>
    );
  }
  const state = STATE[result.state];
  const unit = unitLabel(result.unit);
  const base = result.baseline;
  const reference = result.reference;
  return (
    <aside className={`panel state-${result.state}${asking ? " stale" : ""}`}>
      <header className="verdict">
        <div className="badge"><StateIcon state={result.state} /></div>
        <div>
          <strong>{state.label}</strong>
          <span>{state.line}</span>
        </div>
      </header>

      <p className="asked">{result.question}</p>

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

      <Why result={result} />

      {result.can_use_model && (
        <section className="fallback">
          <p>
            {result.answer === null
              ? "No exact engine covers this question on a scan."
              : "The printed labels alone are a weak source here."}{" "}
            A vision model can point at each item for about a cent; its answer is always marked Check this.
          </p>
          <button onClick={props.onUseModel} disabled={asking}>Ask the vision model</button>
        </section>
      )}

      {result.checks.length > 0 && (
        <section className="checks">
          <h3>Checks run</h3>
          <ul>
            {result.checks.map((c) => (
              <li key={c.name}><span>{c.name}</span><b>{typeof c.value === "number" ? c.value.toLocaleString("en", { maximumFractionDigits: 2 }) : c.value}</b></li>
            ))}
          </ul>
        </section>
      )}

      <EvidenceList items={result.evidence} excluded={excluded} hover={hover} onHover={props.onHover} onToggle={props.onToggle} />

      {result.interpreted && (
        <section className="read-as">
          <h3>Question read as</h3>
          <p>{result.interpreted}</p>
        </section>
      )}

      <section className="ledger">
        <div className="line ours">
          <span>{result.engine}{result.replayed_from ? " (logged answer)" : ""}</span>
          <b>{euro(result.cost_eur)}</b>
          <b>{result.latency_ms < 1000 ? `${Math.max(1, Math.round(result.latency_ms))} ms` : `${(result.latency_ms / 1000).toFixed(1)} s`}</b>
        </div>
        {base && (
          <div className={`line theirs ${base.status}`}>
            <span>
              Vision model alone said{" "}
              <em>{base.answer === null ? "nothing" : formatValue(base.answer as number, result.unit ?? reference?.unit ?? null)}</em>
              {base.status === "wrong" ? " ✗" : base.status === "correct" ? " ✓" : ""}
            </span>
            <b>{euro(base.cost_eur)}</b>
            <b>{(base.latency_ms / 1000).toFixed(1)} s</b>
          </div>
        )}
        {reference && (
          <div className={`line reference ${reference.status}`}>
            <span>
              Dataset's answer: <em>{formatValue(reference.answer as number, reference.unit)}{unitLabel(reference.unit) ? ` ${unitLabel(reference.unit)}` : ""}</em>
            </span>
            <b className="match">
              {reference.status === "correct" ? "matches" : reference.status === "wrong" ? "differs" : reference.status === "abstained" ? "not answered" : "not graded"}
            </b>
          </div>
        )}
      </section>
    </aside>
  );
}
