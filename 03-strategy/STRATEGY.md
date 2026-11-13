# Strategy: accurate, verifiable, cheap

Stoa scores four things equally: accuracy, visual verifiability, creativity, low inference cost. The dataset says how accuracy is judged (exact counts; 2% tolerance on areas, lengths, volumes; free-form text). The plan below is built so that every one of the four criteria comes from the same design decision: **answer from the most structured representation available, and keep the evidence.**

## 1. The router

```
question + path
   │
   ├─ .ifc (or a plan rendered from an .ifc)  → IFC engine        (exact, ~€0)
   ├─ has a sibling .dxf / .dwg / vector PDF  → vector engine     (exact, ~€0)
   ├─ raster floor plan (PNG/JPG)             → symbol detector + OCR (cheap)
   └─ anything else / low confidence          → frontier VLM with tools (expensive, last)
          │
          ▼
   verifier: second method where cheap → agree? answer : "needs review" + evidence
```

Each engine returns the same structure, which the UI renders:

```json
{
  "answer": 7, "unit": "count", "confidence": 0.96,
  "method": "detector:yolo-floorplan-v1",
  "evidence": [{"kind": "box", "page": 0, "bbox": [x0, y0, x1, y1], "label": "window", "id": 1}, ...],
  "cost_eur": 0.0004, "latency_ms": 180,
  "explanation": "7 window symbols detected; 0 below threshold."
}
```

Evidence kinds: `box` (counted symbol), `polygon` (room, with its area), `polyline` (a measured length), `ifc_element` (GlobalId for 3D highlight), `text_span` (a cell in a schedule or a label), `formula` (how a total was summed).

## 2. Engines

### IFC engine (ifc_models, ifc_bench, opening_design IFC)
- IfcOpenShell: `by_type("IfcDoor")`, `IfcWindow`, `IfcSpace`, `IfcWall`/`IfcWallStandardCase`, `IfcSlab`, `IfcBeam`, `IfcStair`; storey via `IfcRelContainedInSpatialStructure` / `ifcopenshell.util.element.get_container`.
- Quantities: read `Qto_*BaseQuantities` (`NetFloorArea`, `NetVolume`, `Length`) when present; otherwise compute from geometry (`ifcopenshell.geom` + shapely/trimesh). Check which convention the expected answers follow on dev data (net vs gross, which spaces count).
- Names are multilingual (Dutch, Danish, Estonian…): match room/storey names exactly as written in the question, with normalisation for quotes.
- Text questions (ifc_bench): an agent that **writes and runs IfcOpenShell code, reads the output, and iterates** ("adaptive exploration"). On IFC-Bench v2 this beat single-pass code generation by about 37 percentage points (see research). Use a mid-size model; log every tool call as evidence.
- The `ifc_models` PDFs/PNGs/SVGs are renders of the same IFC: questions about "the floor plan of 'Level 1'" can be answered from the IFC storey, and the 3D view highlights the counted elements.

### Vector engine (swiss_dwellings, opening_design DXF/DWG/PDF)
- `ezdxf` for DXF: inspect layers and entity types on a few files first (room outlines as closed polylines/hatches? labels as TEXT/MTEXT? doors/windows as INSERT blocks or arcs?). Then:
  - room polygons + the label inside each (point-in-polygon) → counts by label, net areas (shoelace via shapely), apartment totals;
  - door/window/sanitary entities → counts.
- Scale and units: confirm the drawing unit (mm/cm/m) against a known answer on dev data.
- DWG → DXF with ODA File Converter or LibreDWG before parsing.
- Vector PDFs: PyMuPDF `page.get_text("words")` (text with coordinates) and `page.get_drawings()` (vector paths). Schedules (door schedules on sheets like A-801) become tables: `page.find_tables()` or word-coordinate clustering; the evidence is the highlighted rows.

### Raster engine (cubicasa5k PNG, scans)
- Symbol detector (YOLO-class model) for: door (with leaf), window, toilet, sink/washbasin, shower, bathtub, sauna (bench/stove), stairs; plus room-label OCR for "MH", "KH", "OH", "S" etc. on Finnish plans.
- Training data options (ask Stoa first; see the leakage note in CLAUDE.md): CubiCasa5k's public SVG annotations have icon and room polygons; the CubiCasa5k repo ships a pretrained multi-task model. **Split by drawing** and report accuracy only on held-out drawings. If allowed, a detector fine-tuned for one or two hours on a Verda GPU is the low-cost, "won't be obsolete next year" story.
- Counting rules matter more than the model: "a double-bowl sink counts as one", "doorways without a door leaf do not count", "floors shown" counts drawings of storeys. Encode these as post-processing rules with tests.
- Zero-shot fallback: Gemini returns `box_2d` boxes (normalised to 0–1000) and segmentation polygons; Moondream offers cheap local pointing/detection. Use them for symbols the detector doesn't cover and for live questions on unseen drawings, and **tile large drawings** (counts on full-page downscaled images are where VLMs fail).

### Frontier VLM with tools (fallback)
- Give the model tools, not just pixels: crop/zoom, OCR region, run detector, query DXF/IFC. It decides, the tools produce evidence.
- Constrain outputs to JSON with the evidence schema above. Cap tokens; downscale and tile with `media_resolution` controls.

## 3. Verifier (details in RELIABILITY.md)
- Two independent methods where cheap (e.g. DXF count vs PNG detector count; IFC Qto area vs geometry area vs the area printed on the drawing; detector vs VLM points). Agreement → candidate for ✅ Verified. Disagreement → 🟡 or ⛔, show both evidence layers in different colours.
- Sanity rules: areas positive and below the floor total; counts integer and plausible for the plan size; units match the question; same answer across languages.
- Show the interpreted query ("Counting: doors with a leaf, interior + entrance, this floor") before the number.
- Verified thresholds are calibrated per question family on a separate split; report coverage and error-among-Verified on test.

## 4. Evaluation (do this first)
- Stratified dev/test split by drawing, per source × type; ~1,000 test questions is plenty for the pitch.
- Metrics: accuracy (dataset rule), coverage (answered vs abstained), accuracy-when-answered, € per question, p50/p95 latency.
- Baselines: (a) a frontier VLM on the image alone, (b) the same model with tiling. Report our system against both, per source.
- Keep `results/leaderboard.csv` per commit; the pitch table comes from here.

## 5. Cost story (25% of the score)
- Show **€ per 1,000 questions** for each engine and for the blended system, next to the frontier-only baseline. Deterministic engines ≈ €0; detector on CPU ≈ fractions of a cent; VLM calls counted exactly from token usage.
- Show the router's share of traffic by engine, as one measured sentence for the pitch, e.g. "X% of questions never touch a frontier model" (fill X from the eval log).

## 6. What to ask Stoa early (booth / Discord)
1. How will accuracy be judged: a hidden question set, new drawings, or live questions from the jury?
2. May we train on the public CubiCasa5k / Swiss Dwellings annotations if we evaluate on held-out drawings?
3. Which question types matter most to their customers (counts, areas, schedules, text)?
4. Do they care about DWG and scanned historic drawings in the demo?

## 7. Risks
- Over-investing in the 3D wow before accuracy works. Order: eval harness → engines → evidence UI → 3D.
- Leakage claims: never present numbers on drawings we trained on.
- Large IFC files (some are hundreds of MB): pre-process to a light JSON + glTF/Fragments for the viewer; don't parse on every question.
