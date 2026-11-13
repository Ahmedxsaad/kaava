# Timeline and checklists (Helsinki time)

Stoa runs in parallel with the team's main project (Valio Aimo) and the Spread background project, so keep it lean: one or two people plus an AI agent.

| When | What |
|---|---|
| **Fri 23:59** | Junction preliminary submission: a draft project with challenge "Make Blueprints Talk" |
| Fri night | Download the preview set; data loader; eval harness; frontier-only baseline numbers |
| Sat morning | Ask Stoa (booth/Discord) the four questions in STRATEGY.md §6. IFC engine + vector engine (swiss_dwellings) working on dev split |
| Sat 12:00 | Junction submissions open |
| Sat afternoon | Raster engine (detector + counting rules) for cubicasa5k; router + verifier; first full eval table |
| Sat evening | Verification UI: pins, room fills, cost meter. Then 3D (extruded plan / IFC highlight) |
| Sat late | Freeze features. Record numbers on the held-out test split |
| Sun early | Rehearse the demo; record the 2-minute video; deck (5–8 slides) |
| **Sun 10:00** | **Junction final submission** (set to FINAL): video ≤ 2 min, demo link, code link, deck PDF; tick tech challenges |
| Sun 10:30–14:00 | Review time; all team members present for peer review |

## Must-have before recording
- [ ] Accuracy on held-out drawings per source, vs frontier-only baseline
- [ ] € per question per engine and blended
- [ ] Evidence overlay for every answer type (count pins, room polygons, schedule rows, IFC highlights)
- [ ] One live question on a drawing with no prepared questions
- [ ] Licence credits shown for drawings in the demo (from each `LICENSE.md`)

## Deck outline (5–8 slides)
1. The answer: accuracy + cost vs baseline, one screenshot of verified pins.
2. The idea: route each question to the most structured representation; evidence as output.
3. How it decides (router diagram).
4. Verification UI (2D pins / room fills / 3D).
5. Results table per source and type (held-out), cost per 1,000 questions, and the risk–coverage curve: "0 errors in N Verified answers; X% verified; the rest flagged or declined".
6. What fails and what we'd do next (honest limits).
7. Why it won't be obsolete next year: better models plug into the fallback; geometry stays exact.
