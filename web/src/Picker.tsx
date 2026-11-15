import { useEffect, useState } from "react";
import { api, BRING_ACCEPT, BRING_NOTE, type DrawingSummary, type Source, SOURCE_NAMES } from "./api";

interface Props {
  sources: Source[];
  current: string | null;
  onPick(id: string): void;
  onClose(): void;
  onBring(file: File): void;
}

const ENGINE_NOTE: Record<string, string> = {
  ifc: "exact · model query",
  dxf: "exact · geometry",
};

export function Picker({ sources, current, onPick, onClose, onBring }: Props) {
  const initial = sources.find((s) => current?.includes(`/${s.source}/`))?.source ?? sources[0]?.source ?? "";
  const [source, setSource] = useState(initial);
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<DrawingSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!source) return;
    let live = true;
    setLoading(true);
    const timer = window.setTimeout(() => {
      api.drawings(source, query, 0).then((page) => {
        if (!live) return;
        setItems(page.items);
        setTotal(page.total);
        setLoading(false);
      });
    }, query ? 200 : 0);
    return () => { live = false; window.clearTimeout(timer); };
  }, [source, query]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const more = () => {
    setLoading(true);
    api.drawings(source, query, items.length).then((page) => {
      setItems((prev) => [...prev, ...page.items]);
      setLoading(false);
    });
  };
  const meta = sources.find((s) => s.source === source);

  return (
    <div className="scrim" onClick={onClose}>
      <div className="picker" role="dialog" aria-label="Choose a drawing" onClick={(e) => e.stopPropagation()}>
        <header>
          <h2>Choose a drawing</h2>
          <input autoFocus placeholder="Filter by name" value={query} onChange={(e) => setQuery(e.target.value)} />
          <label className="bring bring-here" title={BRING_NOTE}>
            Bring your own
            <input type="file" accept={BRING_ACCEPT} onChange={(e) => { const file = e.target.files?.[0]; if (file) onBring(file); e.target.value = ""; }} />
          </label>
          <button className="close" onClick={onClose} aria-label="Close">×</button>
        </header>
        <nav>
          {sources.map((s) => (
            <button key={s.source} className={s.source === source ? "on" : ""} onClick={() => setSource(s.source)}>
              {SOURCE_NAMES[s.source] ?? s.source}
              <span>{s.drawings.toLocaleString("en")}</span>
            </button>
          ))}
        </nav>
        <p className="picker-note">
          {source === "uploads"
            ? "Stoa's sample sheets, and what was brought to this demo. A sheet is read with its working shown; a model is answered by queries on the model."
            : meta?.archive
            ? "Archive drawings nobody wrote questions for. Ask anything; a vision model answers and its items are drawn."
            : "Drawings from the development split. The calibration and test drawings stay unseen."}
          {meta?.credit && source !== "uploads" ? ` ${meta.credit.replace(/\.$/, "")}.` : ""}
          <span className="bring-line">Bring your own: {BRING_NOTE.charAt(0).toLowerCase() + BRING_NOTE.slice(1)}</span>
        </p>
        <div className="grid">
          {items.map((d) => (
            <button key={d.id} className={`card${d.id === current ? " on" : ""}`} onClick={() => onPick(d.id)}>
              <span className="thumb"><img src={api.thumb(d.id)} loading="lazy" alt="" /></span>
              <span className="name">{d.title}</span>
              <span className="meta">
                {d.formats.join(" · ").toUpperCase()}
                {d.engine ? <em>{ENGINE_NOTE[d.engine]}</em> : d.questions ? <em className="model">vision model</em> : null}
              </span>
            </button>
          ))}
          {!items.length && !loading && <p className="none">No drawing matches.</p>}
        </div>
        {items.length < total && (
          <button className="load" onClick={more} disabled={loading}>
            {loading ? "Loading…" : `Show more (${(total - items.length).toLocaleString("en")} left)`}
          </button>
        )}
      </div>
    </div>
  );
}
