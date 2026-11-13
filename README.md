# Stoa context pack — "Make Blueprints Talk", Junction 2026

Everything an AI coding agent (or a teammate) needs to start on Stoa's challenge: the brief, a full analysis of the dataset and its 127,000 questions, a strategy built around the four judging criteria, a plan for a visually impressive and verifiable demo, and research on prior work, repos and winning hackathon projects.

## How to use it
1. Unzip into the root of a new project repo.
2. Open it in Claude Code (or upload to a Claude project) and say: **"Read CLAUDE.md and start."**
3. Run `bash 02-dataset/download.sh preview` to fetch the 0.5 GB preview (`full` for ~10 GB later).

## Contents
| Path | What |
|---|---|
| `CLAUDE.md` | Start here: mission, data facts, approach, ground rules, build order |
| `01-challenge/CHALLENGE.md` | The Junction challenge page verbatim: brief, criteria, prizes, dataset links |
| `01-challenge/STOA-COMPANY.md` | Stoa's product, customers, trust stance and team from stoabuild.com, and what it means for our pitch |
| `03-strategy/RELIABILITY.md` | Verified / Check / Can't-answer design, independent checks, calibrated error guarantees, tests |
| `02-dataset/DATASET.md` | Stoa's dataset README + our analysis: sizes, formats per source, question types and templates |
| `02-dataset/sample-questions.jsonl` | 40 representative questions with answers |
| `02-dataset/download.sh` | Resumable download of the preview or full set |
| `03-strategy/STRATEGY.md` | Router, engines (IFC, vector, raster, VLM), verifier, evaluation, cost story, questions for Stoa |
| `03-strategy/WOW-DEMO.md` | The verification UI as the wow: pins, room fills, 2D→3D, cost meter; 2-minute video script; inspiration |
| `03-strategy/TECH-PARTNERS.md` | Side prizes that fit (Verda Compute Track, Gemma, Gemini, Nemotron, Modal…) |
| `03-strategy/TIMELINE.md` | Schedule, checklists, deck outline |
| `04-research/RESEARCH.md` | Papers, benchmarks, repos, tools, Devpost/X projects, with links |

Captured on Friday 13 November 2026, evening (Helsinki).
