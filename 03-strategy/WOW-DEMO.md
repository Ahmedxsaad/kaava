# The wow: make the evidence the show

The team wants a demo that makes people say "wow" **and** is correct. The trick is that in this challenge the most impressive visuals are also the scoring criteria: Stoa gives 25% for "how well the solution visualizes its reasoning so a human can verify the answer". So every animation below shows evidence; none is decoration.

## The product in one screen

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  Ask: "Kuinka monta ikkunaa pohjapiirustuksessa on?"            🎙  [Ask]     │
├───────────────────────────────────────────────┬──────────────────────────────┤
│                                               │  Answer: 7 windows            │
│   DRAWING (zoomable, deep zoom tiles)         │  ✓ 2 methods agree            │
│   • each counted window gets a numbered pin   │  Method: detector + vector    │
│   • rooms fill with colour + m² label         │  Cost: €0.0003 · 180 ms       │
│   • click a pin to exclude it → answer -1     │  Evidence: 1 2 3 4 5 6 7      │
│                                               │  [Show in 3D] [Show steps]    │
├───────────────────────────────────────────────┴──────────────────────────────┤
│  3D: walls extruded from the plan / IFC model; counted windows glow           │
└──────────────────────────────────────────────────────────────────────────────┘
```

## The trust layer comes before the wow

Every answer card shows its state first: **✅ Verified**, **🟡 Check this** or **⛔ Can't answer reliably** (RELIABILITY.md). The ✅ badge opens a "Why verified" panel listing the checks that passed and the measured error rate for that kind of question. In the demo, show one 🟡 and one ⛔ on purpose: a system that knows its limits is the most impressive thing you can show a construction company, whose own product still tells users to "verify important information".

## Wow moments (in order of value per hour of work)

1. **Counting pins that land one by one.** When the answer arrives, numbered pins drop onto each symbol (staggered animation), the counter ticks up to the final number. Verifiable in seconds: a missed or extra symbol is obvious. Click a pin to toggle it; the answer updates live (human stays in control).
2. **Rooms that fill with their area.** For area questions, the room polygon fills with a translucent colour, the m² appears in the middle, and a small "formula" line shows the sum (e.g. `14.2 + 9.8 + 3.1 = 27.1 m²`). For "total net floor area excluding balconies", excluded rooms are hatched grey.
3. **Plan → 3D in one click.** Walls extruded from the DXF/IFC (or from detected walls on raster plans) into a Three.js scene; the counted doors/windows glow; camera flies from top view to eye level. For IFC models, use That Open Components (web-ifc) to load the real model and highlight elements by GlobalId. Two WebMCP Challenge winners (Alza, ArchMorph) won with exactly this 2D↔3D sync; ours adds **verified answers** on top.
4. **Two methods, two colours.** When the vector engine and the detector both count, show both layers (e.g. blue = DXF entities, orange = detector boxes). Perfect overlap = trust. A lone orange box = the suspicious one.
5. **Cost meter.** A live ticker: "This answer cost €0.0003. A frontier model alone: €0.04." Plus a bar chart of € per 1,000 questions by engine. Turns the "low inference cost" criterion into a visual.
6. **Any language in, same language out.** Ask in Finnish, German, Dutch or Estonian; the answer and labels come back in that language. The dataset is in ~30 languages; this is cheap to show and memorable.
7. **"Make blueprints talk", literally.** Voice question and spoken answer (site engineers have their hands full). Low effort with a speech API; optional, and also a fit for the ElevenLabs tech challenge.
8. **Historic drawings nobody prepared.** End the demo on a 1920s Helsinki archive scan (finna) or a US HABS measured drawing with no prepared questions: ask something live, watch the pins land. Shows generalisation.
9. **The agent's steps** (for text/IFC questions): a collapsible trace of the code the agent ran against the IFC and what it found, each step linked to the 3D elements it touched.

## Front-end building blocks

- 2D: an SVG/Canvas overlay on top of the drawing (Konva, Pixi or plain SVG); OpenSeadragon or a tiling approach for big sheets.
- 3D: Three.js for extruded plans; That Open Components / web-ifc for IFC; highlight via material swap by GlobalId.
- PDF rendering: PDF.js in the browser, or pre-render PNG tiles with PyMuPDF on the backend (simpler, consistent coordinates).
- Keep **one coordinate system** per drawing (pixels of the rendered page) and convert all evidence to it on the backend.

## 2-minute video / pitch script

| Time | Beat |
|---|---|
| 0:00–0:10 | The problem: "Site engineers spend hours looking for information. Frontier models get counts wrong on drawings." Show a frontier model miscounting windows on a plan. |
| 0:10–0:40 | Ask the same question in our app (in Finnish). Pins drop on each window; ✅ Verified, "Why verified" shows 2 methods agree + measured error rate; €0.0003. Then one 🟡 answer with the doubtful symbol highlighted. |
| 0:40–1:05 | Area question on a Swiss floor: rooms fill with m²; the formula line; "exact from the DXF geometry". |
| 1:05–1:25 | IFC model: "How many doors on Level 1?" → 3D view, the 6 doors glow; agent trace for a text question. |
| 1:25–1:45 | The numbers: accuracy vs frontier-only baseline per source on held-out drawings; € per 1,000 questions; % routed to free engines. |
| 1:45–2:00 | Live question on a 100-year-old Helsinki archive drawing. Close: "Answers you can check in seconds, at a fraction of the cost, and better every time the models improve." |

All numbers in the mock-up and script (costs, counts, latency) are placeholders: replace them with measured values from the eval log before recording.

## Inspiration (look before building)

- **Alza** (WebMCP Challenge winner): floor-plan editor, plan raised to walkable 3D, constraint checks in metres. Devpost: https://devpost.com/software/alza · demo: https://alza-dev.pages.dev · code: https://github.com/Elioz404/Alza
- **ArchMorph** (WebMCP Challenge winner): 2D plan, 3D orbit and walk mode in sync, live room areas. Devpost: https://devpost.com/software/archmorph · code: https://github.com/Yousafkhanzadaa/ArchMorph
- **CanvasMind** (WebMCP Challenge entry): floor plans an agent can read, with accessibility auditing. https://devpost.com/software/canvasmind-26osxu
- **RoomDex** (WebMCP Challenge entry): shared room canvas for planning and measuring. https://devpost.com/software/roomdex
- All WebMCP Challenge winners: https://github.com/dcontessa/devpost-webmcp-2026-winners · gallery: https://webmcp.devpost.com/project-gallery
- AI visual takeoff demo (video): https://www.youtube.com/watch?v=WC4illn-M0w
- A critical look at a viral AI house-plan demo (what not to overclaim): https://www.youtube.com/watch?v=CePlSSi0qYo
- Gemini spatial understanding (boxes and masks) visualised: https://tools.simonwillison.net/gemini-mask
