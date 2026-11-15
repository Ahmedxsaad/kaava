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
| Stoa's four sample sheets, 27 questions | 27 right, 0 wrong, 0 refused, €0.024 per question | `results/stoa_sheets/` |

## Who reads a question the second time

`python scripts/bench_readers.py` writes free-form questions from development plans' own room names, so the right reading of each is known from the drawing, and has Gemini 4 Carbon read each twice and Gemma 4 31B once. On 50 questions over 10 plans (`results/readers/`): every reading whose call went through was right (Gemini 48 of 48, Gemma 34 of 34), and no pair agreed on a wrong reading. 16 of Gemma's 50 calls failed under the benchmark's load (the free tier's rate limit), which is why the app falls back to Gemini for the second reading when Gemma cannot be reached. Gemma's readings cost nothing; a Gemini reading €0.0035.

## Stoa's sample sheets

Stoa posted four sheets without answers. `examples/stoa/questions.jsonl` holds 27 questions on them with **an answer key of our own**; each line says where its answer comes from. The key is ours, not Stoa's, and one entry changed during the event: the 150 mm slab's area was the plain rectangle (61.69 m²) until we saw that two trenches are cut into the slab; it is now the area with them taken out (59.27 m²).

### What the 27 of 27 does not cover

The key holds questions whose answer a person can read off the sheet in a minute. It leaves out the three answers the demo shows off on the school plan, because nobody counted or measured them by hand first:

| Shown in the demo | What we know | What we do not know |
| --- | --- | --- |
| Every door on the school plan | The kept answer is 118, with 5 more boxed in one pass only and drawn as doubtful. Four fresh runs of the same question on 15 Nov 2026 gave 122, 113, 124 and 118. It is marked Check this. | The true count. The system's own runs spread over 113 to 124, about 5% either side of the middle. |
| The Multi-Purpose Hall's floor area, measured on the sheet | 245.0 m², and the same again on a fresh run; 244.14 m² when only a door's width is given as the ruler. | Whether 245 m² is right: no figure for it is printed on the sheet. |
| The hall's baseboard length | 50.4 m, and the same again on a fresh run. | The same. |

The fresh runs are in `results/kept_costs/` (they were made to measure what each kept answer costs to work out: `scripts/kept_costs.py`). Repeating an answer shows it is stable, not that it is right. The test that settles it is a blind trial: a person makes the key before the system answers, the system and the vision model alone then answer once each, and three people who do not know the system check its answer against the clock. The rig is built into the app (`/blind`) and described in [BLIND-TRIAL.md](BLIND-TRIAL.md). **No trial has been run by people yet**, so nothing is claimed from it.

### The hard cases, asked live and filmed

`examples/hard_cases.json` lists twelve questions of the kinds Stoa named (one size given and the rest worked out, baseboards, slab areas by type with their holes taken out, door counts, piping) with what each answer is held against. `scripts/hard_cases.mjs` asks them in the app one after another against a server started with `KAAVA_FRESH=1`, so that every answer is worked out during the run and none is a kept answer shown again; it writes `results/hard_cases/<time>.json` and films the run. The video is [hard-cases.mp4](hard-cases.mp4) (3 min 15 s; waiting is shown faster, with the real time on screen).

The run in the video (15 Nov 2026, 03:33 UTC, `results/hard_cases/20261115-033313.json`):

| | Stoa asked for | The case | Answer | Held against | State | Inference | Time |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | One size given, the rest worked out | A door is 1.7 m wide: the hall's floor area | 244.14 m² | 0.35% under the scale bar's own measurement (245.0 m²) | Check this | €0.016 | 15 s |
| 2 | The same | The same door: metres of baseboard | 50.31 m | 0.18% under the scale bar's (50.4 m) | Check this | €0.017 | 24 s |
| 3 | Refuse instead of guessing | "The hall is 25 m long" (it is 17.8 m by the scale bar) | refused | a refusal is due: the size given is 29% off the sheet's scale | no number | €0.016 | 10 s |
| 4 | Linear metres for baseboards | The hall's baseboard, from the scale bar | 50.40 m | no key | Check this | €0.016 | 9 s |
| 5 | Counts of doors | Every door of the school, tile by tile | 118 | no key | Check this | €0.245 | 44 s |
| 6 | Slab area by type, holes taken out | The 150 mm slab less its two trenches | 59.28 m² | 0.02% over our key (59.27 m²) | Check this | €0.091 | 88 s |
| 7 | Slab area by type (thickness and bars) | The 300 mm slab with D16 bars | refused | no key. Two workings came to 217.69 and 215.85 m²: they differ, so no number | no number | €0.127 | 185 s |
| 8 | Floor areas | Circulation area, added from the sheet's table | 4,470 ft² | equals our key | Check this | €0.025 | 21 s |
| 9 | Piping plans | Fueling pumps, by their tags | 5 | equals our key | Check this | €0.023 | 10 s |
| 10 | Piping plans | The suction header's diameter | 16 in | equals our key | Check this | €0.022 | 8 s |
| 11 | An area with a hole in it | A corridor around a lift (DXF) | 13.99 m² | equals the dataset's answer | Verified | €0 | 1 s |
| 12 | A question in your own words | "how many toilets are there in the bagno rooms" (DXF, two readers) | 5 | no key | Check this | €0.0035 | 16 s |

**8 of the 8 cases that have something to be held against came out as it says** (cases 1 and 2 are held against the system's own scale-bar measurement, which is a consistency check, not an independent key). Four have no key, so nothing is claimed for their numbers. All twelve: €0.60 of model calls, 7 minutes of answering.

This was the third run. All of them are in `results/hard_cases/`, as they came:

- **Run 1** (02:38 UTC, all twelve): the same eight right. The door count was 113. The D16 slab got no number: its first model call ran past the 150 s limit.
- **Run 2** (about 03:08 UTC, stopped after seven): the first six as in the table, the door count 124. The D16 slab had no answer after seven minutes, and the run was stopped.
- **Run 3** is the table above.

What the runs showed, beyond the table:

- **The door count is not stable.** 113, 124 and 118 in three runs an hour apart, 122 earlier the same morning. Each run boxes the doors twice and counts only those both passes box, and the count still moves by about 5%. It is marked Check this, with the doubtful boxes drawn; a hand count is needed to say which is right ([BLIND-TRIAL.md](BLIND-TRIAL.md)).
- **The D16 slab has no stable answer.** Its workings, across that day's attempts, range from 215.85 to 220.14 m² (the model reads a stair landing's dimensions differently each time). When two workings of one run differ the system refuses and shows both, which is what it is built to do; when they agree it answers (217.81 m² in one attempt). The answer kept with the app (218.61 m², from 14 Nov) is one of these workings, not a settled figure.
- **A fault was found and fixed.** In about half the calls for that slab question (8 of 15 measured) the model thought in circles until its output cap and wrote nothing. At the cap of 16,000 tokens that took about 170 s, longer than a call may take, so it surfaced as a time-out and a bare "engine error". Now a reply holds at most 8,000 tokens, the first attempts 3,000 (a spiral ends in about 35 s), a call that brings nothing back is asked again up to three times, a second working lost this way is not counted as a disagreement, and a refusal for this cause says that the model did not answer, not that the drawing cannot (`vlm.MAX_OUTPUT_TOKENS`, `sheet_reading.asked_again`). The longest useful reply in the logged runs is 2,257 tokens. A call that timed out was not billed in our figures, so run 1's cost for that case (€0) understates what Google charged.
- The cases that measure against a size given in the question, the scale bar or printed dimensions gave the same number in every run (244.14, 50.31, 50.40, 59.28).

### What an answer costs, and what showing it again costs

An example's answer comes with the app, so opening it spends nothing. That zero is never the figure set beside another system's cost. Each kept answer carries the cost of its inference and where the figure comes from: the question log's row from when it was first worked out, a benchmark run of the same question (`results/stoa_sheets/`), or one fresh run made to measure it (`results/kept_costs/`, €0.38 in all for five answers). The 14 example answers cost between €0.015 and €0.24 each to work out; the tiled door count is the dearest. A vision model alone is set beside ours only on questions that were themselves put to it.

## What is not solved

- Nothing on a scan is Verified, and scans are two thirds of the dataset's questions.
- IFC answers are not Verified yet: too few were held out to prove the 1% bar.
- A scale taken from door widths is an estimate; it moves with which doors are measured and is shown with its range.
- The test split has not been run.
