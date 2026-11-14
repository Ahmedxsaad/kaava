// Types and calls for the API in src/blueprints/server.py.

export type State = "verified" | "check" | "declined";

export interface Source {
  source: string;
  drawings: number;
  credit: string;
  archive: boolean;
}

export interface DrawingSummary {
  id: string;
  source: string;
  title: string;
  formats: string[];
  questions: number;
  engine: "ifc" | "dxf" | null;
  has_3d: boolean;
  example?: boolean;  // a sample sheet that comes with the app
}

export interface Room {
  ref: string;
  points: [number, number][];
  name: string;
  number: string;
  area_m2: number;
  outdoor: boolean;
  unit: number | null;
}

export interface Suggested {
  question: string;
  type: string;
  unit: string | null;
}

export interface DrawingDetail extends DrawingSummary {
  credit: string;
  view: {
    kind: "plan" | "page" | "image" | "none";
    width: number;
    height: number;
    page: number;
    pages: number;
    aligned: boolean;
    note: string;
    px_per_m: number | null;
    url: string | null;
  };
  rooms: Room[];
  mesh: string | null;
  mesh_kind: "ifc" | "plan" | null;
  model_file: string | null;
  suggested: Suggested[];
  paper_scale: number | null;
  can_enhance: boolean;
}

export interface Evidence {
  kind: "box" | "polygon" | "point" | "element" | "formula" | "context";
  role?: string;
  why?: string;
  n?: number;
  ref?: string | null;
  label?: string;
  text?: string;
  bbox?: [number, number, number, number];
  points?: [number, number][];
  x?: number;
  y?: number;
  id?: string;
  cls?: string;
  area_m2?: number | null;
  volume_m3?: number;
  length_m?: number;
  score?: number | null;
  holes?: [number, number][][];  // rings cut out of a measured room (shafts, or rooms of their own inside it)
  same_as?: number;  // lies exactly on that earlier item: the same outline is in the file more than once
  times?: number;  // how many outlines lie here: the place is one mark
}

// One mark per place: an item that lies exactly on an earlier one is folded into it.
export function places(items: Evidence[]): Evidence[] {
  const extra = new Map<number, number>();
  for (const item of items) if (item.same_as != null) extra.set(item.same_as, (extra.get(item.same_as) ?? 0) + 1);
  return items.filter((item) => item.same_as == null).map((item) => (item.n != null && extra.has(item.n) ? { ...item, times: 1 + extra.get(item.n)! } : item));
}

export interface Level {
  score: number;
  answers: number;
  errors: number;
  upper_bound: number;
  passed: boolean;
}

export interface AskResult {
  question: string;
  state: State;
  answer: number | string | null;
  unit: string | null;
  explanation: string;
  reason: string;
  method: string;
  engine: string;
  confidence: number | null;
  cost_eur: number;
  latency_ms: number;
  replayed_from: string | null;
  interpreted: string | null;
  basis?: string | null;  // what the vision model said about how it got there
  trace?: { title: string; body: string }[];  // what the program and the model each did, in order
  restated: { question: string | null; reason: string; model: string; cost_eur: number; latency_ms: number } | null;
  checks: { name: string; value: number | string }[];
  verified_because: {
    family: string;
    score: number;
    threshold: number;
    calibration_answers: number;
    calibration_errors: number;
    error_upper_bound: number;
  } | null;
  family: {
    family: string;
    threshold: number | null;
    alpha: number;
    levels: Level[];
    calibration_answers: number;
  } | null;
  evidence: Evidence[];
  can_use_model: boolean;
  can_confirm: boolean;  // a strong label reading that a vision model could count as well
  // The file holds some counted outlines more than once: how many outlines, how many distinct places,
  // how many lie on another, and which of the two counts the answer is.
  coinciding: { outlines: number; distinct: number; extra: number; counted: "distinct" | "outlines" } | null;
  readings: { labels: number | null; model: number | null } | null;  // two readings of one scan
  reference: { answer: number | string; unit: string | null; type: string; status: string } | null;
  baseline: {
    answer: number | string | null;
    status: string;
    cost_eur: number;
    latency_ms: number;
    model: string | null;
    run_id: string;
  } | null;
}

export interface StatRow {
  scope: string;
  source: string;
  type: string;
  n: number;
  answered: number;
  coverage: number;
  correct: number;
  wrong: number;
  ungraded: number;
  accuracy: number | null;
  accuracy_when_answered: number | null;
  verified: number;
  verified_share: number | null;
  verified_wrong: number;
  verified_error_upper95: number | null;
  check: number;
  declined: number;
  eur_per_question: number | null;
  eur_total: number | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
}

export interface Run {
  run_id: string;
  method: string;
  dataset: string;
  split: string;
  sample: string;
  commit: string;
  timestamp: string;
  complete: boolean;
  notes: string;
  rows: StatRow[];
}

export interface Stats {
  runs: Run[];
  thresholds: {
    alpha?: number;
    families: Record<string, {
      threshold: number | null;
      answers_at_threshold?: number;
      errors_at_threshold?: number;
      upper_bound?: number;
      calibration_answers: number;
      levels_tested: Level[];
    }>;
  };
  full_set_mix: { source: string; type: string; questions: number }[];
  labels_on_scans: { run_id: string; scan_questions: number; answered: number; correct: number; share: number; share_alone: number } | null;
  // Stoa's sample sheets scored against our own answer key: this system, and the vision model alone.
  sheets_bench?: Record<string, { n: number; correct: number; wrong: number; declined: number; accuracy: number; eur_per_question: number; eur_total: number }>;
  model_budget_eur: number;
  model_spent_eur: number;
}

async function get<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(await message(res));
  return res.json();
}

async function message(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return typeof body.detail === "string" ? body.detail : res.statusText;
  } catch {
    return res.statusText;
  }
}

export const api = {
  sources: () => get<Source[]>("/api/sources"),
  drawings: (source: string, q: string, offset: number, limit = 48) =>
    get<{ total: number; items: DrawingSummary[] }>(
      `/api/drawings?source=${encodeURIComponent(source)}&q=${encodeURIComponent(q)}&offset=${offset}&limit=${limit}`,
    ),
  drawing: (id: string, page = 0) =>
    get<DrawingDetail>(`/api/drawing?drawing=${encodeURIComponent(id)}&page=${page}`),
  thumb: (id: string) => `/api/thumb?drawing=${encodeURIComponent(id)}`,
  stats: () => get<Stats>("/api/stats"),
  // What a long answer is doing right now, while the app waits for it.
  async progress(drawing: string, question: string): Promise<string | null> {
    const res = await fetch(`/api/progress?drawing=${encodeURIComponent(drawing)}&question=${encodeURIComponent(question)}`);
    return res.ok ? ((await res.json()) as { text: string | null }).text : null;
  },
  async ask(drawing: string, question: string, useModel: boolean): Promise<AskResult> {
    // A long answer is worked on by the server on its own. A request that outlives it comes back as
    // "still working" (202) well before any proxy would cut it off; asking again joins the same work.
    for (let tries = 0; tries < 40; tries++) {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "content-type": "application/json" },
        // auto: where no exact engine and no printed label answers, the vision model is asked without
        // a second click. Its cost shows on the answer, and the server's spending cap still holds.
        body: JSON.stringify({ drawing, question, use_model: useModel, auto: true }),
      });
      if (res.status === 202) {
        await new Promise((wait) => window.setTimeout(wait, 1500));
        continue;
      }
      if (!res.ok) throw new Error(await message(res));
      return res.json();
    }
    throw new Error("the answer took too long; ask again to pick it up where it is");
  },
  async upload(file: File): Promise<DrawingSummary> {
    const res = await fetch(`/api/upload?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
    if (!res.ok) throw new Error(await message(res));
    return res.json();
  },
  async share(drawing: string, question: string): Promise<{ url: string; hosted: "cloudinary" | "local"; note?: string }> {
    const res = await fetch("/api/share", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ drawing, question }),
    });
    if (!res.ok) throw new Error(await message(res));
    return res.json();
  },
  enhanced: (drawing: string) => get<{ url: string }>(`/api/enhanced?drawing=${encodeURIComponent(drawing)}`),
  partners: () => get<{ gemini: boolean; claude: boolean; cloudinary: boolean; elevenlabs: boolean }>("/api/partners"),
  async mesh(url: string): Promise<ArrayBuffer> {
    const res = await fetch(url);
    if (!res.ok) throw new Error(await message(res));
    return res.arrayBuffer();
  },
};

/** A run that counted a repeated outline as the dataset's answers do (BLUEPRINTS_COUNT=outlines). */
export const countsAsDataset = (run: Run): boolean => run.method.includes("+count=outlines");

/**
 * The newest logged run of a kind on the fixed 200-question development sample.
 * "alone": the vision model answering from the picture. "system": engines first, scored against the
 * dataset with a repeated outline counted as the dataset counts it. "app": the same system counting
 * each place once, which is what the app answers.
 */
export function headlineRun(runs: Run[], kind: "alone" | "system" | "app"): Run | undefined {
  const newest = [...runs].reverse();
  if (kind === "alone") return newest.find((r) => r.method.startsWith("vlm-image-only") && r.sample === "dev_baseline_200");
  const mine = newest.filter((r) => r.method.startsWith("router:v1+fallback") && r.sample === "dev_baseline_200");
  return kind === "app" ? mine.find((r) => !countsAsDataset(r)) : mine.find(countsAsDataset) ?? mine[0];
}

export const SOURCE_NAMES: Record<string, string> = {
  swiss_dwellings: "Swiss floors",
  cubicasa5k: "Finnish scans",
  ifc_models: "IFC models",
  ifc_bench: "IFC-Bench",
  opening_design: "US drawing sets",
  uploads: "Your drawings",
  finna: "Helsinki archive",
  habs: "US historic survey",
  europeana: "Europeana",
  wikimedia: "Wikimedia",
};

export function formatValue(value: number | string | null, unit: string | null): string {
  if (value === null || value === undefined) return "—";
  if (typeof value !== "number") return String(value);
  if (unit === "count" || (unit === null && Number.isInteger(value))) return value.toLocaleString("en");
  // A whole number in a unit printed whole (16 in, 26,562 ft²) is not given decimals it does not have.
  if (Number.isInteger(value) && unit !== "m2" && unit !== "m3" && unit !== "m") return value.toLocaleString("en");
  return value.toLocaleString("en", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

const UNIT_LABELS: Record<string, string> = { m2: "m²", m3: "m³", m: "m", mm: "mm", cm: "cm", ft: "ft", in: "in", ft2: "ft²", mm2: "mm²", cm2: "cm²", in2: "in²", ft3: "ft³" };

export function unitLabel(unit: string | null): string {
  return UNIT_LABELS[unit ?? ""] ?? "";
}

export function euro(value: number): string {
  if (value === 0) return "€0";
  if (value < 0.01) return `€${value.toFixed(4)}`;
  return `€${value.toFixed(3)}`;
}

// What the drawing says aloud: the number with what it counted, then how far to trust it.
const SPOKEN_THINGS: [RegExp, string, string][] = [
  [/door|ovi|ovea|tür|porte|uks/i, "door", "doors"],
  [/window|ikkuna|fenster|fenêtre|aken/i, "window", "windows"],
  [/toilet|wc-istui|toilette/i, "toilet", "toilets"],
  [/sink|basin|allas|altaa/i, "sink", "sinks and basins"],
  [/bedroom|makuuhuone|schlafzimmer/i, "bedroom", "bedrooms"],
  [/shower|bathtub|suihku|kylpyamme/i, "shower or bathtub", "showers and bathtubs"],
  [/sauna/i, "sauna", "saunas"],
  [/balcon|parveke/i, "balcony", "balconies"],
  [/kitchen|keittiö|küche/i, "kitchen", "kitchens"],
];

export function spoken(result: AskResult): string {
  if (result.answer === null) return `I can't answer that reliably. ${result.reason ?? ""}`.trim();
  const trust = result.state === "verified" ? "Verified. You don't need to double-check me."
    : "Check this one: my evidence is marked on the drawing.";
  if (result.unit === "count" && typeof result.answer === "number") {
    const thing = SPOKEN_THINGS.find(([test]) => test.test(result.question));
    const what = thing ? ` ${result.answer === 1 ? thing[1] : thing[2]}` : "";
    return `I count ${formatValue(result.answer, result.unit)}${what}. ${trust}`;
  }
  const unit = result.unit === "m2" ? " square metres" : result.unit === "m3" ? " cubic metres" : result.unit === "m" ? " metres" : "";
  return `${formatValue(result.answer, result.unit)}${unit}. ${trust}`;
}
