# Junction submission: text to paste

Text for the fields of the project form on hackjunction.app. Every figure is from the run log in `results/` (15 November 2026).

## Project name

Kaava

## Punchline

Ask a drawing. Get a number you do not have to double-check.

## Challenge

Make Blueprints Talk (Stoa)

## Links

- Demo: https://stoa.ahmedxsaad.me
- Source: https://github.com/Ahmedxsaad/kaava (make it public before submitting)
- How it works, in the app: https://stoa.ahmedxsaad.me/#p=how · Numbers: https://stoa.ahmedxsaad.me/#p=numbers

## Description

A wrong quantity becomes a wrong order. A frontier vision model asked to count doors or measure rooms from the picture is right 62.5% of the time on 200 held-out questions of the dataset, and it cannot show which doors it counted.

Kaava (Finnish for both "formula" and "plan") computes the number instead of generating it. A router sends each question to the cheapest method that is exact for that drawing: a query on the IFC model, the geometry of the DXF, the labels printed on a scan. Only where nothing exact exists does a vision model take part, and then only as a pointer: it boxes each symbol, or names the printed figures and the formula. Its reply format has no field for the answer. Code counts, measures and does the arithmetic, and re-reads every figure the model named.

Every answer ends in one of three states. **Verified** is earned, not claimed: the bar is set on drawings the system never tuned on, and of 16,982 Verified answers on the development split none was wrong. **Check this** shows the evidence drawn on the sheet. **Can't answer reliably** gives the reason instead of a guess.

What makes it hard to obsolete with next year's model:

- **Examiners.** A slab's area from its printed dimensions is right about the rectangle and wrong about the slab if a trench is cut into it. Two independent readers look inside what was measured; the program measures what they find on the drawing and takes it out (Stoa's slab sheet: 61.69 m² becomes 59.28 m²).
- **Standards as a ruler.** The scale comes from the scale bar, else a printed dimension, else door widths, and door openings must come out as wide as doors are or nothing is measured.
- **Rooms of any shape, with their holes**, on CAD plans, sheets and scans.
- **A question in your own words** is read as a query by two different models (Gemini and Gemma); only readings that agree are run.

Everything counted is marked on the sheet and stands in a 3D model: an IFC model, a plan raised from its CAD data, or a scan raised from the walls found in its own pixels.

**Results.** 95.8% right on the same 200 questions, at €0.003 per question against €0.008 for the model alone. On Stoa's four sample sheets (slab areas, baseboards, door counts, piping): 27 of 27 right against an answer key of our own. Held-out Verified error: 6 wrong in 2,612 areas and 8 in 5,710 counts, under 1% with 95% confidence. The test split has not been opened.

## Technologies used

Python (FastAPI, IfcOpenShell, ezdxf, Shapely, PyMuPDF, OpenCV, RapidOCR), React, TypeScript, Three.js, Google Gemini API, Gemma, Cloudinary, ElevenLabs, Aiven for PostgreSQL, Docker, Cloudflare Tunnel, GitHub Actions.

## Tech challenges that apply

- **Best Use of Gemini API:** Gemini 4 Carbon is the only model in the answer path, always as a pointer: boxes on scans in its native detection format, printed figures and formulas on sheets, questions read as queries, examiners.
- **Best Use of Gemma:** Gemma 4 31B is the second, independent reader of a question in your own words. Measured on 50 questions with known readings: right on every reading whose call went through, never agreeing with Gemini on a wrong one.
- **Best Use of Cloudinary:** AI super-resolution for viewing small scans, and hosting of shareable evidence sheets. Their rules also ask for their survey (https://cld.media/hackathon-survey).
- **Best Use of ElevenLabs:** answers read aloud in the app.
- **Aiven:** every question asked on the demo is logged in Aiven for PostgreSQL (never who asked) and read back as live figures on the Numbers page.
- **Best use of CRACI:** only if the CI is seen running on CRACI's runners before submitting. The workflow is migrated (tests, the web build and an accuracy regression of the exact engines on every push; `.github/workflows/ci.yml`, switch `CI_RUNNER=craci`), but CRACI's GitHub connection is for an organisation and does not cover the repository on a personal account, so today the jobs run on GitHub's runners. Do not tick this challenge unless that changes.

## Video and deck

The two-minute video and the deck are made separately. `pitch/kaava-deck.pdf` is an earlier deck rebuilt under the new name; its content dates from 14 November and does not cover the examiners, the scale reasoning or scans in 3D.
