import "@fontsource-variable/bricolage-grotesque/opsz.css";
import "@fontsource-variable/geist";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";
import { TOUR } from "./tour";

// The examples name drawings of the full dataset. A server that holds only part of it (the preview)
// says which it has, and the others are left out before anything is shown.
async function keepKnownExamples() {
  try {
    const res = await fetch("/api/known", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ drawings: [...new Set(TOUR.map((stop) => stop.drawing))] }) });
    if (!res.ok) return;
    const known = new Set(((await res.json()) as { known: string[] }).known);
    for (let i = TOUR.length - 1; i > 0; i--) if (!known.has(TOUR[i].drawing)) TOUR.splice(i, 1);
  } catch { /* shown as written */ }
}

void keepKnownExamples().then(() => createRoot(document.getElementById("root")!).render(<App />));
