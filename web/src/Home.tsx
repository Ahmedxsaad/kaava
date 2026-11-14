import { useEffect, useState } from "react";
import { api, headlineRun, type Run, type Stats } from "./api";
import { Story } from "./Story";
import { type Stop, TOUR } from "./tour";

interface Props {
  onOpen(stop: Stop): void;
  onAsk(drawing: string, question: string): void;
  onBrowse(): void;
  onNumbers(): void;
  onBring(file: File): void;
  onTalk(stop: Stop): void;
  canTalk: boolean;
}

const overall = (run?: Run) => run?.rows.find((r) => r.scope === "overall");
const pct = (v: number | null | undefined) => (v == null ? "—" : `${(100 * v).toFixed(1)}%`);

const STATE_WORD = { verified: "Verified", check: "Check this", declined: "Can't answer" } as const;

export function Home({ onOpen, onAsk, onBrowse, onNumbers, onBring, onTalk, canTalk }: Props) {
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
      <Story stats={stats} onOpen={onOpen} onAsk={onAsk} onBrowse={onBrowse} onBring={onBring} onTalk={onTalk} canTalk={canTalk} />
      <div className="home-rest">
      <header className="try-head">
        <h2>Try it on real drawings</h2>
        <button onClick={onNumbers}>All the numbers →</button>
      </header>
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

      </div>
    </main>
  );
}
