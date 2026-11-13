# The Stoa dataset

Inspected on 13 Nov 2026 by reading the zip archives' file directories and `questions.jsonl` directly from Stoa's links (no full download). Download with `download.sh`.

## Stoa's own README (verbatim, from `junction-dataset-preview/README.md`)

> # Junction construction drawings
>
> About 14,000 construction drawings (PDF, DWG, DXF, IFC, SVG, PNG and JPEG scans) from Europe and the US, in about 30 languages, with about 120,000 quantity-takeoff questions for roughly 40% of them. For non-commercial use in the Junction challenge.
>
> ```
> data/<source>/<file>       the drawings
> data/<source>/LICENSE.md   licence and credit for every file in that folder
> questions.jsonl            {"path", "question", "answer", "unit", "type"}
> ```
>
> Files with the same name and a different extension are the same drawing in another format. `ifc_models` floor plans `<model>_<level>.pdf` were rendered from the model `<model>.ifc`.
>
> ## Questions
>
> ```json
> {"path": "data/swiss_dwellings/site3387_bldg5238_floor23498.pdf", "question": "How many apartments (residential units) are shown on this floor plan?", "answer": 2, "unit": "count", "type": "count"}
> ```
>
> `type` is count, area, length, volume, text or other. Scoring: counts must be exact; areas, lengths and volumes within 2%; text answers are free-form.
>
> ## Sources
>
> | source | what | licence |
> |---|---|---|
> | cubicasa5k | Finnish real-estate floor plans | CC BY-NC-SA 4.0 |
> | swiss_dwellings | Swiss apartment floors drawn from published geometry (PDF, DXF, PNG) | CC BY 4.0 |
> | opening_design | real drawing sets and CAD files of a US architecture practice (original folders kept) | CC BY-SA 4.0, MIT |
> | ifc_models | open IFC building models and floor plans rendered from them | per file |
> | ifc_bench | IFC models with the IFC-Bench v2 questions | per file |
> | habs | US historic measured drawings, Library of Congress | public domain |
> | finna | Helsinki City Archives and Architecture & Design Museum drawings | CC BY 4.0 |
> | europeana | drawings from European museums and archives | per file |
> | wikimedia | European, UK and US floor plans from Wikimedia Commons | per file |
>
> ## Licences
>
> Every file keeps its own licence, listed with its credit in `data/<source>/LICENSE.md`. Keep the credit when you show or share a drawing. NonCommercial (NC) files are for non-commercial use only. Adaptations of ShareAlike (SA) files must use the same licence. NoDerivatives (ND) files must stay unchanged. Questions inherit the licence of the drawing they ask about.

## Size

| Archive | Files | Unpacked | Questions | Drawings with questions |
|---|---|---|---|---|
| Preview | 924 | ~727 MB | 7,990 | 320 |
| Full | 16,902 | ~12.3 GB | 127,044 | 5,939 |

Full set by format: JPG 6,566 (3.8 GB) · PNG 6,327 (2.6 GB) · IFC 611 (2.7 GB) · PDF 1,825 (2.5 GB) · DXF 1,049 (334 MB) · DWG 271 (258 MB) · SVG 242 (47 MB).

## Files per source (full set)

| Source | Files | Has questions? |
|---|---|---|
| cubicasa5k | 4,787 PNG | Yes: 85,514 (all counts) |
| swiss_dwellings | 1,040 floors × (PDF + DXF + PNG) | Yes: 37,584 (25,568 counts, 12,016 areas) |
| ifc_models | 533 IFC + 107 PDF + 107 SVG + 500 PNG | Yes: 3,062 (2,218 area, 642 count, 138 volume, 64 length) |
| ifc_bench | 30 IFC | Yes: 820 text |
| opening_design | 678 PDF, 271 DWG, 9 DXF, 48 IFC (real project folders) | Yes: 64 (39 count, 16 text, 9 area in ft²) |
| habs | 1,506 JPG (historic measured drawings) | No |
| finna | 1,800 JPG (Helsinki archives) | No |
| europeana | 1,085 JPG | No |
| wikimedia | 2,175 JPG + 135 SVG | No |

The ~60% of drawings without questions (historic scans, archives) are perfect for **live demo questions** that nobody prepared for.

## Question mix (full set)

| Type | Count | Unit |
|---|---|---|
| count | 111,763 (88.0%) | count |
| area | 14,243 (11.2%) | m² (9 in ft²) |
| text | 836 (0.7%) | null, free-form |
| volume | 138 | m³ |
| length | 64 | m |

Questions are multilingual: Cubicasa5k questions come in English and Finnish pairs; Swiss Dwellings in English and German; IFC models in English, Dutch, Danish, Estonian and others (room and storey names in the model's language).

## Question templates by source (most frequent first)

**cubicasa5k** (24 templates, every question in EN + FI)
- How many windows are shown on this floor plan? (4,787×)
- How many floors (storeys) does this floor plan show?
- How many toilet bowls (WC seats) are drawn on this floor plan?
- How many sinks and wash basins (kitchen and bathroom combined; a double-bowl sink counts as one) are drawn on this floor plan?
- How many showers and bathtubs are drawn on this floor plan in total?
- How many doors are shown on this floor plan? Count doorways fitted with a door; open doorways without a door leaf do not count.
- How many bedrooms (makuuhuone, usually marked MH) are on this floor plan? · How many saunas…?

**swiss_dwellings** (193 templates, EN + DE)
- How many rooms labelled 'X' are there on this floor? (6,227×; X = Schlafzimmer, Wohnen, Küche, Bad, Reduit, Balkon, Korridor, Treppenhaus…)
- What is the net floor area of room N ('X') in m²? (2,045×)
- How many apartments / doors (interior + entrance) / windows / toilets / washbasins / bathtubs are drawn on this floor plan?
- What is the total net floor area in m² of all numbered indoor rooms on this floor (excluding balconies, loggias, terraces, other outdoor spaces, shafts and voids)?
- What is the total net area in m² of all rooms labelled 'X' on this floor? · What is the net floor area of apartment A in m², summing all its rooms except outdoor spaces?

**ifc_models** (397 templates)
- What is the net floor area of the room 'X' on 'Y', in m²? (EN 1,182×; also NL, DA, ET…)
- How many rooms (spaces) / doors / stairs are shown on the floor plan of 'Level'?
- What is the total net floor area of all rooms (spaces) on 'Level', in m²?
- What is the total net volume of all slabs / walls on 'Level', in m³?
- How many doors / windows / rooms / walls / beams / stairs are there in the model 'Name'?

**ifc_bench** (681 templates, free text): space breakdowns by level with areas, residential units with gross areas, total exterior wall length, wall type thicknesses, software and IFC schema used…

**opening_design** (64 hand-written questions on real sheets, e.g. "On sheet A-801 of the Xcel Sports Complex drawings, how many doors (door numbers) are listed in the door schedule?", door types, materials, frames, notes, plus areas in ft²).

`sample-questions.jsonl` in this folder holds 40 representative questions with answers.

## What this means for the solution

- **Each question names its file**, so there is no retrieval problem except inside multi-sheet sets (opening_design).
- **~30% of questions (swiss_dwellings) come with a DXF** of the same floor: room polygons and labels give exact areas and counts.
- **IFC questions are database queries**: IfcOpenShell answers them exactly and almost for free; the PDFs/PNGs of ifc_models are renders of the same IFC.
- **~67% (cubicasa5k) are raster-only symbol counts** on Finnish floor plans: this is where a small detector beats a frontier model on cost and, with care, on accuracy.
- Scoring is strict: an off-by-one count is wrong. Show every counted item so a human can spot the miss in a second.
