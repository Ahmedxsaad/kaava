# Junction submission: text to paste

Text for the fields of the project form on hackjunction.app. Every figure is from the run log in `results/` (15 November 2026).

## Project name

Kaava

## Punchline

Ask a construction drawing a question. The number is computed by code, marked on the sheet, and labelled Verified, Check this, or Can't answer.

## Challenge

Make Blueprints Talk (Stoa)

## Links

- Project demo: https://stoa.ahmedxsaad.me
- Source code: https://github.com/Ahmedxsaad/kaava
- Other link (how the numbers were measured): https://github.com/Ahmedxsaad/kaava/blob/main/docs/EVALUATION.md

## Description

<!-- description:start -->
**A wrong quantity becomes a wrong order.** A frontier vision model asked to count doors or measure rooms from the picture is right 62.5% of the time on 200 held-out questions of Stoa's dataset, and it cannot show which doors it counted.

**Kaava** (Finnish for both "formula" and "plan") computes the number instead of generating it. A router sends each question to the cheapest method that is exact for that drawing: a query on the IFC model, the geometry of the DXF, the labels printed on a scan. Only where nothing exact exists does a vision model take part, and then only as a pointer: it boxes each symbol, or names the printed figures and the formula. Its reply format has no field for the answer. Code counts, measures and does the arithmetic, and re-reads every figure the model named.

### Three states, never a bare number

- **Verified** is earned, not claimed. The bar is set on drawings the system never tuned on; of 16,982 Verified answers on the development split, none was wrong.
- **Check this** shows the answer with its evidence drawn on the sheet, so a person checks it in seconds.
- **Can't answer reliably** gives the reason instead of a guess.

### What next year's model does not make obsolete

- **Examiners.** A slab's area from its printed dimensions is right about the rectangle and wrong about the slab if a trench is cut into it. Two independent readers look inside what was measured; the program measures what they find and takes it out (Stoa's slab sheet: 61.69 m² becomes 59.28 m²).
- **Standards as a ruler.** The scale comes from the scale bar, else a printed dimension, else door widths, and door openings must come out as wide as doors are, or nothing is measured.
- **One size given, the rest worked out.** Told only that a door is 1.7 m wide, Kaava measures the hall at 244.14 m², 0.35% from the scale bar's figure. Told a size that contradicts the sheet, it refuses.
- **Rooms of any shape, with their holes**, on CAD plans, sheets and scans.
- **A question in your own words** is read as a query by two different models (Gemini and Gemma); only readings that agree are run.

Everything counted is marked on the sheet and stands in a 3D model: an IFC model, a plan raised from its CAD data, or a scan raised from the walls found in its own pixels.

### Results

- **95.8% right** on the same 200 questions, scored as the dataset counts; 93.8% (180 of 192 graded) with a door shared by two flats counted once, as the app counts. The model alone: 62.5% (120 of 192).
- **€0.003 per question** against €0.008 for the model alone.
- Stoa's four sample sheets (slab areas, baseboards, door counts, piping): **27 of 27** right against an answer key of our own, every one marked Check this. The model alone got 26, at a quarter of the cost: the extra checks are what the difference pays for.
- Verified answers on held-out drawings: 6 wrong in 2,612 areas and 8 wrong in 5,710 counts, under 1% with 95% confidence.
- Twelve hard questions asked live and filmed, none replayed: all 8 that have something to check against came out right, and a wrong given size was refused.

### What we do not claim

- The door count for the whole school sheet moves between runs (113 to 124), so it stays Check this.
- One slab question (the 300 mm slab with D16 bars) has no stable answer: Kaava refuses when its two workings differ, and shows both.
- Answers from IFC models are never marked Verified: too few held-out models to prove the 1% bar.
- The test split has not been opened. A blind trial, where a person marks a fresh sheet before seeing Kaava's answer, is built into the demo (/blind) and has not yet been run with people.

### Try it

Open the demo, pick one of Stoa's sheets or bring your own image, PDF page or IFC model, and ask. Every answer shows what its inference cost and what this request cost; a kept answer shown again costs €0 and says so.

**Built with:** Python (FastAPI, IfcOpenShell, ezdxf, Shapely, PyMuPDF, OpenCV, RapidOCR), React, TypeScript, Three.js, Google Gemini API, Gemma, Cloudinary, ElevenLabs, Aiven for PostgreSQL, Docker, GitHub Actions. MIT licence.
<!-- description:end -->

## Tech challenges that apply

- **Best Use of Gemini API:** Gemini 4 Carbon is the only model in the answer path, always as a pointer: boxes on scans in its native detection format, printed figures and formulas on sheets, questions read as queries, examiners.
- **Best Use of Gemma:** Gemma 4 31B is the second, independent reader of a question in your own words. Measured on 50 questions with known readings: right on every reading whose call went through, never agreeing with Gemini on a wrong one.
- **Best Use of Cloudinary:** AI super-resolution for viewing small scans, and hosting of shareable evidence sheets. Their rules also ask for their survey (https://cld.media/hackathon-survey).
- **Best Use of ElevenLabs:** answers read aloud in the app.
- **Best Use of Aiven:** every question asked on the demo is logged in Aiven for PostgreSQL (never who asked) and read back as live figures on the Numbers page.
- **Best use of CRACI: do not tick.** The workflow is migrated (`.github/workflows/ci.yml`, switch `CI_RUNNER=craci`), but CRACI's GitHub connection is for an organisation and does not cover the repository on a personal account, so the jobs run on GitHub's runners.

## Video and deck

- Presentation: `pitch/Kaava_Junction_2026_Pitch_Deck_v2.pdf` (6 slides, 0.9 MB). Its figures agree with the README: 23 doors from 32 outlines, 61.69 − 2.41 = 59.28 m², 27/27, 180/192 against 120/192, €3.15 against €7.99 per 1,000 questions, €0.024 against €0.006 on Stoa's sheets.
- Video: the team's own recording.
- `pitch/deck.html` and `pitch/kaava-deck.pdf` are an earlier, longer deck kept for reference, with its figures brought up to date on 15 November. It is not the one submitted.
