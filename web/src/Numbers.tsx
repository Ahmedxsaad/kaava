import { useEffect, useState } from "react";
import { api, countsAsDataset, headlineRun, type Run, SOURCE_NAMES, type StatRow, type Stats } from "./api";

// Every figure on this page is read from results/leaderboard.csv through /api/stats.

const overall = (run?: Run) => run?.rows.find((r) => r.scope === "overall");
const weighted = (run?: Run) => run?.rows.find((r) => r.scope === "weighted_to_dataset_mix");
const families = (run?: Run) => run?.rows.filter((r) => r.scope === "family") ?? [];
const pct = (v: number | null | undefined, digits = 1) => (v == null ? "—" : `${(100 * v).toFixed(digits)}%`);
const int = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString("en"));
const perThousand = (v: number | null | undefined) => (v == null ? "—" : `€${(1000 * v).toFixed(2)}`);
const TYPE_NAMES: Record<string, string> = { count: "counts", area: "areas", volume: "volumes", length: "lengths", text: "free text" };
const familyName = (source: string, type: string) => `${SOURCE_NAMES[source] ?? source} · ${TYPE_NAMES[type] ?? type}`;

interface Tip {
  x: number;
  y: number;
  title: string;
  lines: { key?: string; value: string; label: string }[];
}

function Tooltip({ tip }: { tip: Tip | null }) {
  if (!tip) return null;
  return (
    <div className="n-tip" style={{ left: tip.x + 14, top: tip.y + 14 }}>
      <strong>{tip.title}</strong>
      {tip.lines.map((line) => (
        <span key={line.label}>
          {line.key && <i style={{ background: line.key }} />}
          <b>{line.value}</b> {line.label}
        </span>
      ))}
    </div>
  );
}

function Dumbbell({ before, after, mix, onTip }: {
  before: StatRow[];
  after: StatRow[];
  mix: Stats["full_set_mix"];
  onTip(tip: Tip | null): void;
}) {
  const total = mix.reduce((s, m) => s + m.questions, 0) || 1;
  const share = (r: StatRow) => (mix.find((m) => m.source === r.source && m.type === r.type)?.questions ?? 0) / total;
  const rows = after
    .filter((r) => r.type !== "text" && r.accuracy != null)
    .map((a) => ({ a, b: before.find((r) => r.source === a.source && r.type === a.type) }))
    .filter((row) => row.b?.accuracy != null)
    .sort((x, y) => share(y.a) - share(x.a));
  return (
    <div className="n-dumbbell">
      <div className="n-axis" aria-hidden>
        <span />
        <div>{[0, 25, 50, 75, 100].map((tick) => <i key={tick} style={{ left: `${tick}%` }}>{tick}%</i>)}</div>
      </div>
      {rows.map(({ a, b }) => {
        const from = 100 * (b!.accuracy ?? 0), to = 100 * (a.accuracy ?? 0);
        const name = familyName(a.source, a.type);
        return (
          <div
            className="n-row"
            key={name}
            tabIndex={0}
            onPointerMove={(e) => onTip({
              x: e.clientX, y: e.clientY, title: name,
              lines: [
                { key: "#ffffff", value: pct(a.accuracy), label: `engines first (${a.correct} of ${a.n})` },
                { key: "var(--series-muted)", value: pct(b!.accuracy), label: `vision model alone (${b!.correct} of ${b!.n})` },
                { value: pct(share(a)), label: "of all questions in the dataset" },
              ],
            })}
            onPointerLeave={() => onTip(null)}
          >
            <span className="n-name">{name}<em>{a.n} questions</em></span>
            <div className="n-track">
              {[25, 50, 75].map((tick) => <i key={tick} className="n-grid" style={{ left: `${tick}%` }} />)}
              <i className="n-link" style={{ left: `${Math.min(from, to)}%`, width: `${Math.abs(to - from)}%` }} />
              <i className="n-dot before" style={{ left: `${from}%` }} />
              <i className="n-dot after" style={{ left: `${to}%` }} />
              <b className="n-value after" style={{ left: `${to}%` }}>{pct(a.accuracy, 0)}</b>
              {Math.abs(to - from) > 9
                ? <b className="n-value before" style={{ left: `${from}%` }}>{pct(b!.accuracy, 0)}</b>
                : Math.abs(to - from) < 0.5 && <b className="n-value before" style={{ left: `${from}%` }}>no engine yet: same as the model</b>}
            </div>
          </div>
        );
      })}
      <div className="n-legend">
        <span><i className="before" />Vision model alone</span>
        <span><i className="after" />Engines first, vision model for the rest</span>
      </div>
    </div>
  );
}

function Routing({ mix, labels, onTip }: { mix: Stats["full_set_mix"]; labels: Stats["labels_on_scans"]; onTip(tip: Tip | null): void }) {
  const sum = (test: (m: { source: string; type: string }) => boolean) => mix.filter(test).reduce((s, m) => s + m.questions, 0);
  // Scans: the shares were measured on the development split. The label engine's strictest answers stand
  // alone; its other strong answers are read a second time by the vision model.
  const scans = sum((m) => m.source === "cubicasa5k");
  const read = Math.round(scans * (labels?.share_alone ?? 0));
  const twice = Math.round(scans * ((labels?.share ?? 0) - (labels?.share_alone ?? 0)));
  const parts = [
    { name: "DXF geometry", note: "Swiss floors · €0", colour: "var(--series-1)", n: sum((m) => m.source === "swiss_dwellings"), ink: "#fff" },
    { name: "IFC model query", note: "building models · €0", colour: "var(--series-2)", n: sum((m) => m.source === "ifc_models"), ink: "#fff" },
    { name: "Printed labels, local OCR", note: "room counts on scans where the labels and the type line agree · €0", colour: "var(--series-3)", n: read, ink: "#0b0b0b" },
    { name: "Vision model, each item marked", note: `the other scans and schedules · paid (${int(twice)} of them room counts read twice: labels and model)`, colour: "var(--series-4)", n: scans - read + sum((m) => m.source === "opening_design"), ink: "#0b0b0b" },
    { name: "No engine yet", note: "free-text model questions", colour: "var(--series-other)", n: sum((m) => m.source === "ifc_bench"), ink: "#0b0b0b" },
  ];
  const total = parts.reduce((s, p) => s + p.n, 0) || 1;
  return (
    <div className="n-routing">
      <div className="n-segments" role="img" aria-label={parts.map((p) => `${p.name} ${pct(p.n / total)}`).join(", ")}>
        {parts.filter((p) => p.n > 0).map((p) => (
          <div
            key={p.name}
            style={{ flexGrow: p.n, background: p.colour, color: p.ink }}
            tabIndex={0}
            onPointerMove={(e) => onTip({ x: e.clientX, y: e.clientY, title: p.name, lines: [
              { key: p.colour, value: int(p.n), label: "questions" },
              { value: pct(p.n / total), label: "of the dataset" },
            ] })}
            onPointerLeave={() => onTip(null)}
          >
            {p.n / total > 0.08 && <b>{pct(p.n / total, 0)}</b>}
          </div>
        ))}
      </div>
      <div className="n-legend">
        {parts.map((p) => <span key={p.name}><i style={{ background: p.colour }} />{p.name}<em>{p.note}</em></span>)}
      </div>
    </div>
  );
}

function Proof({ stats }: { stats: Stats }) {
  const alpha = stats.thresholds.alpha ?? 0.01;
  const engines: Record<string, string> = { "dxf-engine:v1": "Swiss floors", "ifc-engine:v1": "IFC models", "raster-labels:v1": "Scans, printed labels" };
  const rows = Object.entries(stats.thresholds.families).map(([key, f]) => {
    const [method, kind] = key.split("/");
    const passed = f.threshold != null;
    const best = passed
      ? { answers: f.answers_at_threshold ?? 0, errors: f.errors_at_threshold ?? 0, bound: f.upper_bound ?? 0 }
      : [...f.levels_tested].sort((a, b) => a.upper_bound - b.upper_bound).map((l) => ({ answers: l.answers, errors: l.errors, bound: l.upper_bound }))[0];
    return { key, name: `${engines[method] ?? method} · ${TYPE_NAMES[kind] ?? kind}`, passed, best, threshold: f.threshold, total: f.calibration_answers };
  });
  return (
    <div className="n-meters">
      {rows.map((row) => (
        <div className={`n-meter ${row.passed ? "pass" : "fail"}`} key={row.key}>
          <span className="n-name">
            {row.name}
            <em>{row.passed ? `✓ Verified from score ${row.threshold!.toFixed(2)}` : "✕ Not Verified yet"}</em>
          </span>
          <div className="n-bar" title={`Upper bound ${pct(row.best?.bound, 2)} against a ${pct(alpha, 0)} limit`}>
            <i style={{ width: `${Math.min(100, (100 * (row.best?.bound ?? 1)) / alpha)}%` }} />
          </div>
          <span className="n-figure">
            {row.best ? (
              <>
                <span><b>{int(row.best.errors)}</b> wrong in <b>{int(row.best.answers)}</b></span>
                <em>error rate below {pct(row.best.bound, 2)}</em>
              </>
            ) : (
              <em>no held-out answers yet</em>
            )}
          </span>
        </div>
      ))}
      <p className="n-foot">
        The bar is the 95% upper bound on the error rate, against the {pct(alpha, 0)} limit at the right edge. A family is
        marked Verified only when the bound stays under the limit on drawings that were never used to build the engines.
      </p>
    </div>
  );
}

export function Numbers() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tip, setTip] = useState<Tip | null>(null);
  useEffect(() => {
    api.stats().then(setStats).catch((e: Error) => setError(e.message));
  }, []);
  if (error) return <main className="numbers"><p className="n-wait">{error}</p></main>;
  if (!stats) return <main className="numbers"><p className="n-wait">Reading the run log…</p></main>;

  const runs = stats.runs;
  const baseline = headlineRun(runs, "alone");
  const ours = headlineRun(runs, "system");
  const enginesDev = [...runs].reverse().find((r) => r.method === "router:v1" && r.split === "dev" && r.dataset === "junction-dataset")
    ?? [...runs].reverse().find((r) => r.method === "router:v1" && r.split === "dev");
  const b = overall(baseline), o = overall(ours), e = overall(enginesDev);
  // The same system counting each door once (what the app answers), scored against the dataset's answers.
  const once = ours && countsAsDataset(ours) ? overall(headlineRun(runs, "app")) : undefined;
  // Scans measured again on fresh development drawings (the prompt for scans was tuned on the 200's scans).
  const freshRun = [...runs].reverse().find((r) => r.method.startsWith("router:v1+fallback") && r.sample === "dev_scans_fresh_100");
  const freshScans = overall(freshRun);
  // The model marking items on its own, in the same setting the system used (other settings were also measured).
  const setting = /\[([^\]]+?)(?: then [^\]]*)?\]/.exec(freshRun?.method ?? "")?.[1];
  const freshAlone = overall([...runs].reverse().find((r) => r.sample === "dev_scans_fresh_100"
    && (setting ? r.method === setting : r.method.startsWith("vlm-pointing"))));
  const scanShare = (() => {
    const all = stats.full_set_mix.reduce((sum, m) => sum + m.questions, 0) || 1;
    return stats.full_set_mix.filter((m) => m.source === "cubicasa5k").reduce((sum, m) => sum + m.questions, 0) / all;
  })();
  const scansHere = ours?.rows.find((r) => r.scope === "family" && r.source === "cubicasa5k");
  const reweighted = freshScans?.accuracy != null && scansHere?.accuracy != null && weighted(ours)?.accuracy != null
    ? weighted(ours)!.accuracy! - scanShare * (scansHere.accuracy - freshScans.accuracy) : null;
  const model = baseline?.method.split(":")[1] ?? "vision model";
  // Sheets with no model or DXF: the engine that shows its working, on every graded question of its family.
  const sheets = overall([...runs].reverse().find((r) => r.method.startsWith("sheet-working") && r.sample === "dev_us_sheets_graded_31"));
  const aloneOnSheets = (baseline?.rows ?? []).filter((r) => r.scope === "family" && r.source === "opening_design" && r.type !== "text")
    .reduce((sum, r) => ({ n: sum.n + (r.n ?? 0), correct: sum.correct + (r.correct ?? 0) }), { n: 0, correct: 0 });
  const worstBound = Math.max(0, ...Object.values(stats.thresholds.families).filter((f) => f.threshold != null).map((f) => f.upper_bound ?? 0));

  return (
    <main className="numbers" onScroll={() => setTip(null)}>
      <div className="n-page">
        <header className="n-head">
          <h1>The numbers behind the answers</h1>
          <p>
            Read live from the evaluation log in <code>results/</code>. Engines were built on the development drawings;
            the Verified thresholds were set on a separate calibration split; the test split has not been opened.
          </p>
        </header>

        <section className="n-tiles">
          <div className="n-tile">
            <span>Correct answers, same {int(o?.n)} questions</span>
            <strong>{pct(o?.accuracy)}</strong>
            <em>Vision model alone ({model}): <b>{pct(b?.accuracy)}</b></em>
          </div>
          <div className="n-tile">
            <span>Cost per 1,000 questions</span>
            <strong>{perThousand(o?.eur_per_question)}</strong>
            <em>Vision model alone: <b>{perThousand(b?.eur_per_question)}</b></em>
          </div>
          <div className="n-tile">
            <span>Wrong among Verified answers, dev</span>
            <strong>{int(e?.verified_wrong)}</strong>
            <em>of <b>{int(e?.verified)}</b> Verified answers on {int(e?.n)} questions</em>
          </div>
          <div className="n-tile">
            <span>Verified error rate, held-out</span>
            <strong>{worstBound ? `< ${pct(worstBound, 2)}` : "—"}</strong>
            <em>95% upper bound, worst Verified family</em>
          </div>
        </section>

        {stats.live && stats.live.asked > 0 && (
          <section className="n-card">
            <h2>Asked on this demo, live</h2>
            <p>
              Every question put to this app is logged in {stats.live.kept_in}: what was asked and how it ended, never who asked.
              These figures are read from that log{stats.live.since ? `, kept since ${new Date(stats.live.since).toLocaleString()}` : ""}.
            </p>
            <div className="n-tiles">
              <div className="n-tile">
                <span>Questions asked</span>
                <strong>{int(stats.live.asked)}</strong>
                <em>on <b>{int(stats.live.drawings)}</b> drawings</em>
              </div>
              <div className="n-tile">
                <span>How they ended</span>
                <strong>{int(stats.live.verified)}</strong>
                <em>Verified · <b>{int(stats.live.check)}</b> Check this · <b>{int(stats.live.declined)}</b> no number</em>
              </div>
              <div className="n-tile">
                <span>Answered at no cost</span>
                <strong>{pct(stats.live.free / stats.live.asked)}</strong>
                <em><b>{int(stats.live.free)}</b> of {int(stats.live.asked)}: exact engines, or an answer already paid for</em>
              </div>
              <div className="n-tile">
                <span>Spent on model calls</span>
                <strong>€{stats.live.spent_eur.toFixed(2)}</strong>
                <em>{stats.live.compared > 0
                  ? <>on the {int(stats.live.compared)} also put to the model alone: <b>€{stats.live.spent_compared_eur.toFixed(3)}</b> against €{stats.live.alone_eur.toFixed(3)}</>
                  : "in all, since the log began"}</em>
              </div>
            </div>
          </section>
        )}

        <section className="n-card">
          <h2>Accuracy by kind of question</h2>
          <p>
            The same {int(o?.n)} development questions answered twice. Rows are ordered by how common each kind is in the
            full dataset. Re-weighted to that mix: <b>{pct(weighted(ours)?.accuracy)}</b> against{" "}
            <b>{pct(weighted(baseline)?.accuracy)}</b> for the vision model alone.
          </p>
          <Dumbbell before={families(baseline)} after={families(ours)} mix={stats.full_set_mix} onTip={setTip} />
          {once?.accuracy != null && o?.accuracy != null && (
            <p className="n-aside">
              A door between two flats is in a drawing's file once for each flat, and the dataset's answers count it
              twice. The figures here count as the dataset does. The app counts each door once and says so: scored
              against the dataset's answers that is <b>{pct(once.accuracy)}</b> on the same questions
              ({int(once.correct)} of {int(once.n)} instead of {int(o.correct)}). Those answers are never marked Verified.
            </p>
          )}
          {freshScans && (
            <p className="n-aside">
              The scan prompt was tuned on this sample's scans, so they were measured again on {int(freshScans.n)} scan
              questions from drawings no sample had used: <b>{pct(freshScans.accuracy)}</b> correct
              {freshAlone ? <> ({pct(freshAlone.accuracy)} for the model marking items on its own)</> : null}. With that
              figure for scans the re-weighted total is near <b>{pct(reweighted)}</b>.
            </p>
          )}
        </section>

        {sheets?.n != null && (
          <section className="n-card">
            <h2>Harder sheets: the working is shown</h2>
            <p>
              Schedules, structural plans and piping plans have no model or DXF behind them. There the vision model may
              not state the number: it names the printed figures and rows that matter, and the code counts, computes
              and reads each figure again where it is marked. On every count and area question about the US drawing
              sets in the development split: <b>{int(sheets.correct)} of {int(sheets.n)}</b> right, at{" "}
              <b>€{(sheets.eur_per_question ?? 0).toFixed(3)}</b> per question.
              {aloneOnSheets.n > 0 && <> The vision model alone, on the {aloneOnSheets.n} of them in the sample above: <b>{aloneOnSheets.correct} of {aloneOnSheets.n}</b>.</>}
              {" "}Such an answer is never Verified: which figures matter is the model's judgement.
            </p>
          </section>
        )}

        <div className="n-two">
          <section className="n-card">
            <h2>Where the questions go</h2>
            <p>Share of the dataset's {int(stats.full_set_mix.reduce((s, m) => s + m.questions, 0))} questions by the cheapest method that can answer them.</p>
            <Routing mix={stats.full_set_mix} labels={stats.labels_on_scans} onTip={setTip} />
          </section>
          <section className="n-card">
            <h2>The proof behind “Verified”</h2>
            <p>Measured on calibration drawings, one row per engine and kind of answer.</p>
            <Proof stats={stats} />
          </section>
        </div>

        <section className="n-card">
          <h2>Run log</h2>
          <p>The latest run of each method and sample. Free-text answers are not graded automatically.</p>
          <div className="n-table">
            <table>
              <thead>
                <tr><th>Method</th><th>Split</th><th>Sample</th><th>Questions</th><th>Correct</th><th>Answered</th><th>Verified</th><th>Verified wrong</th><th>€ / question</th><th>Commit</th></tr>
              </thead>
              <tbody>
                {[...runs].reverse().map((run) => {
                  const row = overall(run);
                  return (
                    <tr key={run.run_id}>
                      <td className="method">{run.method}</td>
                      <td>{run.split}</td>
                      <td>{run.sample}{run.dataset.endsWith("preview") ? " · preview" : ""}</td>
                      <td>{int(row?.n)}</td>
                      <td>{pct(row?.accuracy)}</td>
                      <td>{pct(row?.coverage)}</td>
                      <td>{int(row?.verified)}</td>
                      <td>{int(row?.verified_wrong)}</td>
                      <td>{row?.eur_per_question ? `€${row.eur_per_question.toFixed(4)}` : "€0"}</td>
                      <td>{run.commit}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      </div>
      <Tooltip tip={tip} />
    </main>
  );
}
