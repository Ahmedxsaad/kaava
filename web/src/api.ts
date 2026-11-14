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
  labels_on_scans: { run_id: string; scan_questions: number; answered: number; correct: number; share: number } | null;
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
  async ask(drawing: string, question: string, useModel: boolean): Promise<AskResult> {
    const res = await fetch("/api/ask", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ drawing, question, use_model: useModel }),
    });
    if (!res.ok) throw new Error(await message(res));
    return res.json();
  },
  async mesh(url: string): Promise<ArrayBuffer> {
    const res = await fetch(url);
    if (!res.ok) throw new Error(await message(res));
    return res.arrayBuffer();
  },
};

export const SOURCE_NAMES: Record<string, string> = {
  swiss_dwellings: "Swiss floors",
  cubicasa5k: "Finnish scans",
  ifc_models: "IFC models",
  ifc_bench: "IFC-Bench",
  opening_design: "US drawing sets",
  finna: "Helsinki archive",
  habs: "US historic survey",
  europeana: "Europeana",
  wikimedia: "Wikimedia",
};

export function formatValue(value: number | string | null, unit: string | null): string {
  if (value === null || value === undefined) return "—";
  if (typeof value !== "number") return String(value);
  if (unit === "count") return value.toLocaleString("en");
  return value.toLocaleString("en", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function unitLabel(unit: string | null): string {
  return unit === "m2" ? "m²" : unit === "m3" ? "m³" : unit === "m" ? "m" : "";
}

export function euro(value: number): string {
  if (value === 0) return "€0";
  if (value < 0.01) return `€${value.toFixed(4)}`;
  return `€${value.toFixed(3)}`;
}
