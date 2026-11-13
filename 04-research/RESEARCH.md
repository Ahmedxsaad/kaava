# Research: prior work, repos, tools and demos

What already exists that we can reuse or learn from, grouped by the part of the system it helps. Links checked on 13 Nov 2026.

## 1. Questions over IFC / BIM models

- **BIM Information Extraction Through LLM-based Adaptive Exploration** — Hellin, Jang, Fuchs, Nousias, Borrmann (TU Munich), arXiv 2605.01698, May 2026. https://arxiv.org/pdf/2605.01698
  An LLM agent writes and runs IfcOpenShell code against the model, reads the result, and adapts until it can answer (CodeAct-style). On **ifc-bench v2** (1,027 tasks, 37 IFC models, 21 projects; 514-question test split) it reached **56.0%** with GLM 4.7 vs **19.1%** for single-pass code generation; static approaches abstained on 43–55% of questions. Documentation retrieval helped a smaller model a little; hand-written tools did not help.
  → This is the method for our `ifc_bench` text questions, and Stoa's dataset literally contains "IFC models with the IFC-Bench v2 questions".
  Benchmark: https://huggingface.co/datasets/sylvainHellin/ifc-bench · code: https://github.com/sylvainHellin/cobbie
- **BIM-Edit: Benchmarking LLMs for IFC-based BIM** — arXiv 2606.20146. https://arxiv.org/html/2606.20146 · Editing (not QA), but confirms the agent + single Python/IfcOpenShell execution tool pattern; best model only ~49/100, so expect agents to need verification.
- **Natural Language Information Retrieval from BIM Models: an LLM-based multi-agent approach** (EC3 2025). https://ec-3.org/publication/ec32025_265/
- **IfcOpenShell** (Python IFC toolkit): https://ifcopenshell.org · https://github.com/IfcOpenShell/IfcOpenShell

## 2. Floor-plan understanding (raster)

- **CubiCasa5K: A Dataset and an Improved Multi-Task Model for Floorplan Image Analysis** — Kalervo et al., SCIA 2019. Paper: https://arxiv.org/pdf/1904.01920.pdf · repo with pretrained weights: https://github.com/CubiCasa/CubiCasa5k
  5,000 Finnish floor plans with 80+ object categories (rooms and icons such as doors, windows, toilets, sinks, bathtubs, saunas). The `cubicasa5k` folder in Stoa's data comes from this dataset (`colorful`, `high_quality`, `high_quality_architectural` subsets). **Leakage warning:** answers can likely be derived from its public annotations; use them only to train, evaluate on held-out drawings, and ask Stoa what's allowed.
- **Symbol as Points: Panoptic Symbol Spotting via Point-based Representation (SymPoint)** — ICLR 2024. https://arxiv.org/abs/2401.10556 · symbol spotting on vector CAD (FloorPlanCAD).
- **CADSpotting: Robust Panoptic Symbol Spotting on Large-Scale CAD Drawings** — arXiv 2412.07377.
- **Symbol Spotting on Digital Architectural Floor Plans Using a Deep Learning-based Framework** — arXiv 2006.00684.
- FloorPlanCAD dataset overview: https://www.catalyzex.com/s/Floorplancad
- Recent floor-plan analysis paper with CubiCasa5K experiments: https://arxiv.org/abs/2507.10343

## 3. Swiss Dwellings (vector geometry)

- **MSD: A Benchmark Dataset for Floor Plan Generation of Building Complexes** (ECCV 2024) — https://arxiv.org/abs/2407.10121 · data: https://doi.org/10.4121/e1d89cb5-6872-48fc-be63-aadd687ee6f9
- Swiss Dwellings (Archilyse) on Zenodo: https://zenodo.org/records/7070952
  Stoa's `swiss_dwellings` floors were "drawn from published geometry" — the DXF is the reliable source for room polygons and areas. Same leakage caution: compute from the provided DXF, never from the public tables.

## 4. Zero-shot grounding models (fallback and live questions)

- **Gemini image understanding**: object detection returns `box_2d` as `[ymin, xmin, ymax, xmax]` normalised to 0–1000; segmentation returns polygons. Images are tiled in 768×768 tiles (258 tokens each); `media_resolution` caps cost. https://ai.google.dev/gemini-api/docs/image-understanding
  Roboflow guide: https://blog.roboflow.com/gemini-2-5-object-detection-segmentation/ · mask visualiser: https://tools.simonwillison.net/gemini-mask
- **Moondream** (small, local, point/detect skills; cheap counting with points): https://docs.moondream.ai/ · point API: https://moondream.ai/c/docs/advanced/api/point · fine-tuning guide: https://blog.roboflow.com/finetuning-moondream2/
- Rule of thumb from both: **tile big drawings**, ask for points or boxes (not just a number), and count the returned items yourself.

## 5. Vector / document parsing

- **PyMuPDF** — text with coordinates (`get_text("words")`), vector paths (`get_drawings()`), tables (`find_tables()`), rendering to PNG: https://pymupdf.readthedocs.io
- **ezdxf** — read DXF entities, layers, blocks, polylines, text: https://ezdxf.readthedocs.io
- **DWG → DXF**: ODA File Converter (free, Open Design Alliance) or LibreDWG.
- **shapely** for polygon areas, point-in-polygon label matching.

## 6. 3D and IFC viewers (for the wow)

- **That Open Company** (ex IFC.js): web-ifc engine and components to load and highlight IFC in the browser. https://github.com/ifcjs/web-ifc · https://github.com/ThatOpen/web-ifc-three
- **xeokit** — another open-source BIM viewer for large models.
- **Three.js** — extrude walls from 2D polylines into 3D.

## 7. Hackathon projects to learn from (Devpost / X)

- **OpenAI WebMCP Challenge** (Devpost, 2,473 entries; winners announced by @OpenAIDevs on X: https://x.com/OpenAIDevs/status/2104617295683424526). Two of the 10 winners are architecture tools:
  - **Alza** — floor plan as typed geometry, plan → walkable 3D, constraint engine that reports problems in metres, 56-check end-to-end test suite. Vite + React + TypeScript + Three.js. https://devpost.com/software/alza · https://github.com/Elioz404/Alza · demo https://alza-dev.pages.dev
  - **ArchMorph** — 2D plan, 3D orbit and walk mode in sync, live room areas and layout checks, 57 agent tools. React/Next + Three.js. https://devpost.com/software/archmorph · https://github.com/Yousafkhanzadaa/ArchMorph
  - Also relevant entries: CanvasMind (agent-readable floor plans with accessibility audit) https://devpost.com/software/canvasmind-26osxu · RoomDex https://devpost.com/software/roomdex
  - Winners list with links: https://github.com/dcontessa/devpost-webmcp-2026-winners · gallery: https://webmcp.devpost.com/project-gallery
  - Lesson: judges rewarded **2D↔3D sync, real dimensions, and visible checks**. None of them answered questions about real drawings with verified accuracy; that is our gap to fill.
- **PlanForm.AI** (prompt-thon, structural intelligence from plans): https://github.com/SambhavBalki-codes/PlanForm.AI
- **3D-architect** (Meta AI developer hackathon gallery): https://cerebralvalley.ai/e/meta-ai-developer-hackathon/hackathon/gallery/731
- Video: AI visual takeoff from construction drawings — https://www.youtube.com/watch?v=WC4illn-M0w
- Critical review of a viral AI house-plan demo (what judges will be sceptical of): https://www.youtube.com/watch?v=CePlSSi0qYo
- "AI blueprint takeoff: what it gets right and what it misses": https://yours-tim.com/blog/ai-takeoff-from-blueprints

## 8. Takeaways for our build

1. Structured formats first (IFC, DXF): exact and nearly free. Most competitors will send every page to a frontier model.
2. For raster counts, a detector + counting rules + tiling beats one big VLM call on both accuracy and cost; keep the VLM as a pointing fallback.
3. For IFC text questions, use the adaptive-exploration agent pattern.
4. Evidence as first-class output (boxes, polygons, GlobalIds) makes the 25% visual criterion and the wow the same work.
