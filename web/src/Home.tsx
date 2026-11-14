import { useEffect, useState } from "react";
import { api, headlineRun, type Run, type Stats } from "./api";
import { type Stop, TOUR } from "./tour";

interface Props {
  onOpen(stop: Stop): void;
  onBrowse(): void;
  onNumbers(): void;
  onBring(file: File): void;
}

const overall = (run?: Run) => run?.rows.find((r) => r.scope === "overall");
const pct = (v: number | null | undefined) => (v == null ? "—" : `${(100 * v).toFixed(1)}%`);

const STATE_WORD = { verified: "Verified", check: "Check this", declined: "Can't answer" } as const;

export function Home({ onOpen, onBrowse, onNumbers, onBring }: Props) {
  const [stats, setStats] = useState<Stats | null>(null);
  useEffect(() => { api.stats().then(setStats).catch(() => undefined); }, []);
  const runs = stats?.runs ?? [];
  const alone = overall(headlineRun(runs, "alone"));
  const ours = overall(headlineRun(runs, "system"));
  const engines = overall([...runs].reverse().find((r) => r.method === "router:v1" && r.split === "dev" && r.dataset === "junction-dataset")
    ?? [...runs].reverse().find((r) => r.method === "router:v1" && r.split === "dev"));
  const shown = TOUR.filter((stop) => stop.card);

  return (
    <main className="home">
      <section className="hero">
        <p className="eyebrow">Junction 2026 · Stoa · Make Blueprints Talk</p>
        <h1>Ask a drawing. See where the answer came from.</h1>
        <p className="sub">
          Counts, areas and volumes computed from the drawing's own geometry and marked on the sheet, so a project
          engineer can check an answer in seconds. Every answer is Verified, Check this, or Can't answer reliably.
        </p>
        <div className="actions">
          <button className="primary" onClick={() => onOpen(TOUR[0])}>Open an example</button>
          <button className="quiet" onClick={onBrowse}>Browse all drawings →</button>
          <label className="quiet bring">
            Bring your own drawing →
            <input type="file" accept="image/*,application/pdf" onChange={(e) => { const file = e.target.files?.[0]; if (file) onBring(file); e.target.value = ""; }} />
          </label>
        </div>
      </section>

      <section className="examples">
        {shown.map((stop) => (
          <button key={stop.label} className="example" onClick={() => onOpen(stop)}>
            <span className="thumb"><img src={api.thumb(stop.drawing)} alt="" loading="lazy" /></span>
            <span className={`state ${stop.state}`}><i />{STATE_WORD[stop.state]}</span>
            <strong>{stop.label}</strong>
            <span className="note">{stop.note}</span>
          </button>
        ))}
      </section>

      <section className="band">
        <header>
          <h2>Measured, not claimed</h2>
          <button onClick={onNumbers}>All the numbers →</button>
        </header>
        <div className="figures">
          <div>
            <strong>{pct(ours?.accuracy)}</strong>
            <p>correct on {ours?.n ?? 200} development questions. A vision model alone: {pct(alone?.accuracy)}.</p>
          </div>
          <div>
            <strong>{ours?.eur_per_question != null ? `€${(1000 * ours.eur_per_question).toFixed(2)}` : "—"}</strong>
            <p>per 1,000 questions. A vision model alone: {alone?.eur_per_question != null ? `€${(1000 * alone.eur_per_question).toFixed(2)}` : "—"}.</p>
          </div>
          <div>
            <strong>{engines?.verified_wrong ?? "—"}</strong>
            <p>wrong among {engines?.verified?.toLocaleString("en") ?? "—"} Verified answers on the development drawings.</p>
          </div>
        </div>
        <p className="source">Read from the evaluation log in the repository. The test split has not been opened.</p>
      </section>

      <section className="how">
        <h2>How an answer is made</h2>
        <ol>
          <li><i>01</i><div><strong>The cheapest exact method answers</strong><p>A building model is queried, a CAD plan is measured, the labels on a scan are read. A vision model is the last resort, and its cost is shown.</p></div></li>
          <li><i>02</i><div><strong>Numbers come from geometry</strong><p>Every count is a list of objects and every area an outline. A language model never writes a number.</p></div></li>
          <li><i>03</i><div><strong>The evidence is on the sheet</strong><p>Each counted item and measured room is marked on the plan and in 3D. Leave one out and the answer follows.</p></div></li>
          <li><i>04</i><div><strong>It knows when not to answer</strong><p>Verified means under 1% wrong on drawings held back for measuring. Otherwise it asks for a look, or declines.</p></div></li>
        </ol>
      </section>
    </main>
  );
}
