# Evaluation

Every figure in the README comes from a logged run. This page says how runs are made, what is held out, and how to reproduce them.

## The data

Stoa's dataset: about 14,000 drawings and 127,000 questions (`questions.jsonl`: path, question, answer, unit, type). `bash 02-dataset/download.sh preview` fetches a 0.5 GB preview, `full` the whole set. Scoring is the dataset's rule (`scoring.py`): **counts exact; areas, lengths and volumes within 2%; text free-form**. A declined answer is not wrong; it is counted on its own.

## What is held out

`splits/split_v1.json` places every drawing in **dev (50%)**, **calibration (25%)** or **test (25%)**. The unit is the drawing group, not the question: a scan is one plan, a Swiss site is all its floors, an IFC project is its models and the plans rendered from them, and groups that share an IfcProject id stay together. Nothing tuned on dev can leak into the other two.

- **dev**: where the engines were built and the prompts tuned.
- **calibration**: only used to set the Verified thresholds; its answers were never looked at one by one.
- **test**: never opened. `./bp run ... --split test --final` is the one run it is for.

The public annotations of CubiCasa5K and Swiss Dwellings are never read: looking answers up would not work on anyone's own drawings.

## Commands

```bash
./bp info                                   # what is in the dataset
./bp split                                  # create or extend the split (new groups only; existing ones stay put)
./bp sample --help                          # save a fixed stratified sample of questions
./bp baseline --sample splits/dev_baseline_200.json     # the vision model alone, from the image
./bp run router --split dev                 # the exact engines over a whole split, logged
./bp run router --sample splits/dev_baseline_200.json --fallback-run <run id>   # the router, with a logged model run filling in where nothing exact answers
./bp run sheet --sample splits/dev_us_sheets_graded_31.json --effort low        # sheets: working shown
./bp calibrate                              # set the Verified thresholds on the calibration split (alpha 1%)
./bp errors <run id>                        # the answers of a logged run, with the questions
python scripts/bench_examples.py            # Stoa's four sample sheets: the system, and the model alone
```

`BLUEPRINTS_COUNT=outlines` counts a door outline the file holds twice as two, as the dataset's answers do; the default counts each door once. Runs in that mode carry `+count=outlines` in their method name.

## The run log

`results/leaderboard.csv` has one row per run, source and question type: commit, method, split, sample, n, answered, coverage, correct, wrong, accuracy, accuracy when answered, Verified count and share, **wrong Verified answers and the 95% upper bound on their rate**, cost per question, latency. `results/runs/<run id>/` holds each run's predictions. The app's Numbers page reads the same files.

## Verified thresholds

`./bp calibrate` writes `splits/verified_thresholds.json`: for each family (engine and question type) the lowest score at which the answers above it stayed under 1% wrong, by the upper end of a 95% confidence interval, on the calibration split. A family with too few held-out answers to prove that gets no threshold and is never Verified (the IFC families today).

| Family | Threshold | Verified on calibration | Wrong | 95% upper bound |
| --- | --- | --- | --- | --- |
| DXF areas | 0.85 | 2,612 | 6 | 0.53% |
| DXF counts | 0.90 | 5,710 | 8 | 0.28% |

## Headline results

| Measured | Result | Run |
| --- | --- | --- |
| 200 dev questions, stratified | 95.8% right; the vision model alone 62.5% | `router:v1+fallback[vlm-pointing…]` on `dev_baseline_200` |
| Whole dev split, exact engines only | 33,452 of 63,592 answered at no cost; 16,982 Verified, 0 wrong | `router:v1` on `all-dev` |
| Cost per question | €0.003; the model alone €0.008 | same sample |
| Stoa's four sample sheets, 27 questions | 27 right, 0 wrong, 0 refused, €0.022 per question | `results/stoa_sheets/` |

## Who reads a question the second time

`python scripts/bench_readers.py` writes free-form questions from development plans' own room names, so the right reading of each is known from the drawing, and has Gemini 4 Carbon read each twice and Gemma 4 31B once. On 50 questions over 10 plans (`results/readers/`): every reading whose call went through was right (Gemini 48 of 48, Gemma 34 of 34), and no pair agreed on a wrong reading. 16 of Gemma's 50 calls failed under the benchmark's load (the free tier's rate limit), which is why the app falls back to Gemini for the second reading when Gemma cannot be reached. Gemma's readings cost nothing; a Gemini reading €0.0035.

## Stoa's sample sheets

Stoa posted four sheets without answers. `examples/stoa/questions.jsonl` holds 27 questions on them with **an answer key of our own**; each line says where its answer comes from. The key is ours, not Stoa's, and one entry changed during the event: the 150 mm slab's area was the plain rectangle (61.69 m²) until we saw that two trenches are cut into the slab; it is now the area with them taken out (59.27 m²).

## What is not solved

- Nothing on a scan is Verified, and scans are two thirds of the dataset's questions.
- IFC answers are not Verified yet: too few were held out to prove the 1% bar.
- A scale taken from door widths is an estimate; it moves with which doors are measured and is shown with its range.
- The test split has not been run.
