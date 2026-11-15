# The API

The app is a FastAPI server (`src/blueprints/server.py`); the page in `web/` is one client of it. Interactive docs are served at `/api/docs`.

## Ask

`POST /api/ask`

```json
{ "drawing": "data/swiss_dwellings/site3565_bldg5449_floor23802",
  "question": "What is the net floor area of room 4.15 ('Korridor') in m²?",
  "use_model": false,
  "auto": true }
```

`auto` lets a model answer when nothing exact can; `use_model` asks one even where another reading already answers. The reply:

| Field | Meaning |
| --- | --- |
| `state` | `verified`, `check` or `declined` |
| `answer`, `unit` | the number (or text), and `count`, `m2`, `m`, `m3`, … ; `null` when declined |
| `explanation`, `reason` | how it was computed; or why there is no number |
| `evidence` | what was counted or measured, to draw: `box`, `polygon` (with `holes`), `point`, `element` (an IFC id), `formula`, and `context` items shown but not counted (a room searched, a door opening left out, a part taken out, the scale used) |
| `trace` | the steps taken, each with a title and a body |
| `checks` | each cross-check and what it gave |
| `engine`, `method`, `confidence` | which method answered, and its score |
| `verified_because` | for a Verified answer: the family's threshold and its measured error on held-out drawings |
| `cost_eur` | what working this answer out cost (the inference), whenever that was done; `null` for an answer kept before costs were recorded |
| `spent_now_eur`, `replayed` | what this request added: `0` and `true` when a kept or logged answer is shown again |
| `cost_note`, `replayed_from` | where the inference figure comes from (the first asking, a benchmark run, a fresh run made to measure it), and where the answer was kept |
| `latency_ms` | how long the answer took to work out |
| `failed` | `true` when a model call failed and nothing was computed (the reason then says so): not a judgement that the drawing cannot answer |
| `restated` | for a free-form question: how the model read it, and that it was read twice |
| `estimate` | when the scale was not printed: the range the answer may lie in, and what the scale was taken from |
| `caveat`, `bound` | what examiners found inside the thing measured and could not take into account; `at most` or `at least` |

While a model is still working, the server answers **202** with `{"pending": true}`; ask again with the same body. `GET /api/progress?drawing=…&question=…` says what it is doing.

## Drawings

| Route | Gives |
| --- | --- |
| `GET /api/sources` | the sources and how many drawings each has |
| `GET /api/drawings?source=…&q=…&offset=…&limit=…` | a page of drawings: id, title, formats, questions, `has_3d` |
| `GET /api/drawing?drawing=…` | one drawing: its view (image or page, size, scale), suggested questions, credit, `mesh_kind` |
| `GET /api/image?drawing=…`, `GET /api/thumb?drawing=…` | the picture, and a thumbnail |
| `GET /api/mesh?drawing=…` | the 3D mesh (binary, `BPM1`); **202** while a large model is being built; 404 when there is no 3D view; 413 when the model is too large |
| `GET /api/ground?drawing=…` | where the drawing lies under its 3D model |
| `POST /api/upload?name=…&page=…` | bring a file of your own (the file as the request body, 25 MB at most): an image, a PDF (one page becomes the drawing: the first, or `page` counted from 0), or an IFC model. 415 with the reason for anything else, a DXF or DWG included |
| `POST /api/upload/page` | `{"drawing": …, "page": n}`: another page of a brought PDF, as a drawing of its own. `GET /api/drawing` gives `pdf: {page, pages}` for such a drawing |
| `POST /api/known` | which of a list of drawing ids this server can show |

## Around an answer

| Route | Gives |
| --- | --- |
| `POST /api/share` | an evidence sheet for an answer, hosted (Cloudinary when configured) |
| `GET /api/enhanced?drawing=…` | a small scan enlarged with super-resolution (Cloudinary) |
| `POST /api/speak` | the answer read aloud, as MP3 (ElevenLabs) |
| `GET /api/partners` | which optional services are switched on |
| `GET /api/stats` | the run log, the Verified thresholds and the benchmark on Stoa's sheets: what the Numbers page shows |

## From the command line

```bash
./bp ask <drawing> "<question>" --out answer.png     # one question, the evidence drawn to a PNG
```

## Blind trial

The page is `/blind`; [BLIND-TRIAL.md](BLIND-TRIAL.md) says what it is for and who does what.

| Route | Gives |
| --- | --- |
| `POST /api/blind/key` | a person's answer key for one question on one sheet, locked: `{drawing, question, by, kind, quantity, marks, how, unseen}`. Starts a trial and returns its id |
| `GET /api/blind/<id>` | the trial's record. The key's quantity and marks are withheld until Kaava has answered; `?view=verify` gives a verifier the question, Kaava's answer and its evidence, and nothing else |
| `POST /api/blind/<id>/run` | asks Kaava and the vision model alone, once each, and compares both with the key (missed and added marks, or the difference against the 2% rule). **202** while still working |
| `POST /api/blind/<id>/verify/start`, `POST /api/blind/<id>/verify` | a verifier's timed check: `{by, verdict: right / wrong / cannot_tell, seconds, note, token}` |
