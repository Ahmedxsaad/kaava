# Blueprints Talk

Answers to questions about construction drawings and building models that a project engineer does not need to double-check. Built at Junction 2026 for Stoa's challenge "Make Blueprints Talk".

Every answer ends in one of three states:

- **Verified**: no need to double-check. Backed by a measured error rate on drawings that were held back.
- **Check this**: an answer with its evidence drawn on the drawing, where one of the checks is weak.
- **Can't answer reliably**: no number, with the reason.

Numbers are computed from the drawing's geometry and objects, never written by a language model. Each counted door or measured room is drawn on the plan and lit in 3D, so a person can check an answer in seconds.

## How it works

A router sends each question to the cheapest method that can answer it exactly.

| Drawing | Method | Cost per question |
| --- | --- | --- |
| IFC model, or a plan rendered from one | IfcOpenShell query: enumerated elements, geometry and the model's own quantities | €0 |
| Floor plan with a DXF | Room outlines, door, window and fixture entities from the DXF, cross-checked against the PDF's text | €0 |
| Scan with no vector data | A vision model points at each item and the code counts the points. Never Verified | a fraction of a cent |

An answer becomes Verified only when its independent checks agree and answers of that kind stayed under 1% wrong (95% upper bound) on a calibration split of drawings that the engines were not built on.

## Results so far

All figures come from the run log in `results/` and are shown live on the app's Numbers page. The test split has not been opened.

| | Vision model alone | This system |
| --- | --- | --- |
| Correct, same 200 development questions | 65.1% | 91.7% |
| Re-weighted to the dataset's question mix | 75.7% | 86.8% |
| Cost per 1,000 questions | €12.41 | €3.49 |

- Swiss floor plans, held-out: counts 99.9% correct (6,346 of 6,354), areas 97.6% (2,962 of 3,034).
- Verified answers on held-out drawings: 8 wrong in 6,354 counts and 6 wrong in 2,612 areas, so the error rate is below 0.25% and 0.53% with 95% confidence.
- Development split: 18,198 Verified answers, none wrong.
- Not solved yet: scans (67% of the dataset's questions) are answered by the vision model and are never Verified; IFC answers are not Verified yet because too few were held out to prove 1%.

The vision model in these runs is Claude Sonnet 5.5, image only, high effort.

## Run it

Python 3.11+ and Node 20+.

```bash
# 1. Python package and the dataset preview (0.5 GB)
uv venv && uv pip install -e ".[dev]"        # or: python -m venv .venv && .venv/bin/pip install -e ".[dev]"
bash 02-dataset/download.sh preview

# 2. The web app, built once
cd web && npm install && npm run build && cd ..

# 3. The app and its API on http://127.0.0.1:8000
./bp serve
```

The exact engines need no API key. To let the app ask a vision model about scans, put `GEMINI_API_KEY` or `ANTHROPIC_API_KEY` in `.env` (see `.env.example`). The app spends at most €1 per server start (`BLUEPRINTS_UI_BUDGET_EUR`).

Other commands:

```bash
./bp ask <drawing> "<question>" --out answer.png   # one question, evidence drawn to a PNG
./bp run router --split dev                        # evaluate and log a run to results/
./bp calibrate                                     # set the Verified thresholds on the calibration split
.venv/bin/python -m pytest                         # tests
cd web && npm run dev                              # front end with hot reload (API on port 8000)
```

### Docker

The image holds the app, the API and the OCR models, but no drawings: mount the dataset and a cache folder.

```bash
docker build -t blueprints-talk .
docker run --rm -p 8000:8000 --user "$(id -u):$(id -g)" \
  -v "$PWD/data:/app/data:ro" -v "$PWD/cache:/app/cache" --env-file .env blueprints-talk
```

Leave out `--env-file .env` to run without a vision model. The first time a large IFC model is opened its 3D mesh is built, which can take minutes; it is cached after that.

## Layout

| Path | What |
| --- | --- |
| `src/blueprints/engines/` | IFC engine, DXF engine, router |
| `src/blueprints/verify.py` | Calibrated Verified thresholds |
| `src/blueprints/harness.py`, `scoring.py`, `split.py` | Evaluation harness, Stoa's scoring rule, the frozen split by drawing |
| `src/blueprints/server.py`, `mesh.py` | API for the app; 3D meshes from IFC models and DXF plans |
| `web/` | The verification app (React, Three.js) |
| `results/` | Log of every evaluation run |
| `splits/` | The split, the fixed samples and the Verified thresholds |
| `CLAUDE.md`, `01-challenge/` to `04-research/` | Challenge brief, dataset notes, strategy and research |

## Data and licences

The drawings come from Stoa's challenge dataset; each source keeps its own licence (`LICENSE.md` in each source folder) and the app shows the credit under every drawing. CubiCasa5K is non-commercial. Answers are never looked up from the public annotations of CubiCasa5K or Swiss Dwellings.
