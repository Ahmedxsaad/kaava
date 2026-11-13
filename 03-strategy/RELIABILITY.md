# Reliability: answers a project engineer doesn't have to double-check

Construction is a sensitive field: a wrong door count goes into an order, a wrong area into a cost estimate or a contract. Stoa's own product today ends answers with "AI can make mistakes. Verify important information." (stoabuild.com/product). Our goal is different: **for the answers we certify, the user does not need to check; for the rest, we say exactly what to look at, or we decline.** Reliability is a feature we design, measure and prove, not a hope.

## 1. Three answer states, never more

| State | Meaning | What the user does |
|---|---|---|
| **✅ Verified** | Passed every check below, and answers of this kind have a measured error rate under our target (e.g. ≤ 1%) on held-out drawings | Nothing. Use it. |
| **🟡 Check this** | We have an answer and its evidence, but one check is weak (single method, borderline confidence, unusual drawing) | Look at the highlighted evidence; one click to confirm or correct |
| **⛔ Can't answer reliably** | Methods disagree, the drawing doesn't contain the information, the question is ambiguous, or the scan is unreadable | Read the reason; rephrase or check the source |

Rule: **a wrong Verified answer is the worst outcome**, worse than abstaining. The threshold for Verified is set from data (section 4), not by feel.

## 2. Where errors come from, and the guard for each

| Error source | Example | Guard |
|---|---|---|
| Misreading the question | "doors" incl. open doorways? "net area" incl. balconies? which storey? | **Show the interpreted query** before the answer: "Counting: doors with a leaf, interior + entrance, on this floor." The dataset's questions state these rules; encode them and display them. |
| Wrong definition / unit | Net vs gross area, m² vs ft², mm drawing units | Every area states its definition and source ("IfcSpace NetFloorArea from the model's quantities", "polygon inside walls from the DXF"), and the unit conversion. Area standards differ by country, so state the assumption instead of hiding it. |
| Generative guessing | An LLM "reads" 7 windows and says 8 | Numbers are **computed, not generated**: counts come from enumerated objects (DXF entities, IFC elements, detector boxes), areas from polygons. The LLM only parses the question and writes explanations. |
| Missed / double-counted symbols | Double-bowl sink counted twice; window cut by page edge | Counting rules as tested code; tiling with overlap + de-duplication; every counted item is a numbered pin. |
| Model failure on unusual drawings | Hand-drawn 1920s scan, rotated sheet | Out-of-distribution detector (image statistics, detector confidence spread); OOD ⇒ never Verified. |
| Silent pipeline bugs | Wrong scale factor, wrong storey mapping | Golden test set run on every commit; consistency checks (section 3). |

## 3. Independent checks (agreement = trust)

For each answer, run the cheap independent methods that exist for that file and compare:

- **Counts:** DXF/IFC entities ↔ raster detector ↔ VLM pointing (tiled). Two that agree exactly, with the third not contradicting, is strong evidence.
- **Areas:** polygon area from geometry ↔ IFC quantity set ↔ **the area printed on the drawing** (many plans print "12.5 m²" in each room; OCR it). Agreement within 2% across two sources.
- **Totals:** sum of room areas ↔ floor total; per-apartment sums ↔ floor sums; counts per room ↔ counts per floor.
- **Language invariance:** the same question in English, Finnish or German must produce the same answer (the dataset contains such pairs; test them).
- **Plausibility:** integer counts, positive areas, room area below floor area, counts plausible for the plan's size.

Show the checks in a "Why verified" panel:

```
✅ Verified — 7 windows
  ✓ DXF window entities: 7     ✓ Detector: 7 (min conf 0.91)
  ✓ All 7 inside the floor outline   ✓ Same answer in FI and EN
  ✓ This kind of question: 0 errors in 812 held-out checks (95% upper bound 0.4%)
```

## 4. Prove it with numbers: calibrated abstention

1. Split by drawing into dev / calibration / test.
2. For each answer compute a score from the checks (agreement, detector confidence, OOD score, method type).
3. On the **calibration** set, choose the threshold per question family (source × type) so that the error rate among Verified answers is ≤ α (e.g. 1%), with a statistical guarantee. The "Learn then Test" framework does exactly this (risk control with finite-sample guarantees). Simple version: require that, among calibration answers above the threshold, the Clopper–Pearson 95% upper bound on the error rate is ≤ α.
4. Report on the untouched **test** set: **coverage** (% Verified) and **error among Verified**, per family, plus the **risk–coverage curve**. This curve is the single best slide for "accuracy" and "reliability".

Example claim format (fill with measured values): "Verified answers: 0 errors in 1,904 held-out questions (95% upper bound 0.16%); 81% of all questions verified; the rest flagged with evidence or declined."

Rule of three for quick bounds: 0 errors in n checks ⇒ 95% upper bound ≈ 3/n.

## 5. Provenance and audit

Every answer stores: file path and hash, page, coordinates of every evidence item, method and model versions, the interpreted query, check results, cost, timestamp. Re-running gives the same answer (pin seeds and model versions). This matches Stoa's own direction (cited, logged answers; EU AI Act transparency).

## 6. Test like a sceptical engineer

- **Golden set**: ~200 hand-verified questions across all sources, run on every push (CI).
- **Trick questions**: ask for things not on the drawing ("How many lifts?" on a single-family plan), ambiguous storeys, wrong units. Expected: ⛔ with a reason.
- **Perturbations**: rotate, rescale, crop a margin, re-render a PDF at another DPI. Answers must not change.
- **Error review**: every wrong answer on dev gets a cause label (question parsing / detector / rule / data) and a fix or a new guard.

## 7. How this shows up in the demo and pitch

- Open with the contrast: a frontier model confidently miscounting, then our ✅ with its "Why verified" panel.
- Show one 🟡 and one ⛔ on purpose. Judges trust a system that knows its limits.
- Slide: risk–coverage curve + "0 errors in N verified answers".
- Line for Stoa: **"We replace 'verify important information' with a guarantee for the answers we certify, and point to exactly what to check for the rest."**

## References
- Learn then Test: Calibrating Predictive Algorithms to Achieve Risk Control — Angelopoulos, Bates, Candès, Jordan, Lei. https://arxiv.org/abs/2110.01052
- SAFER: Risk-Constrained Sample-then-Filter in Large Language Models. https://arxiv.org/abs/2510.10193
- Selective Classification for Deep Neural Networks — Geifman & El-Yaniv, NeurIPS 2017. https://arxiv.org/abs/1705.08500
- Detecting hallucinations in large language models using semantic entropy — Farquhar, Kossen, Kuhn, Gal, Nature 2024. https://oatml.cs.ox.ac.uk/publications/202406_FarquharKossenKuhn_HallucinatingLLMs.html (useful only for the free-text IFC answers)
- Finnish building-area terminology (net floor area etc.), Ministry of the Environment: https://sanastot.suomi.fi/en/terminology/rakymp/concept/c933 · IPMS net vs gross area leaflet: https://constructalia.arcelormittal.com/files/Net%20versus%20Gross%20Area%20-%20IPMS%204%20Leaflet--fa8a55e228e2610785756088bab84ffd.pdf
