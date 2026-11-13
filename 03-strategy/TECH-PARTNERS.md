# Tech challenges (side prizes) that fit "Make Blueprints Talk"

One Junction project = one main challenge + any number of tech challenges ticked on the submission form. Prizes as listed on the Junction platform on 13 Nov 2026. Only tick what we really use.

## Strong fits

| Tech challenge | Prizes | How it fits |
|---|---|---|
| **Verda Compute Track** (Stoa is in Verda's track) | 1st $10,000 Verda cloud credits + hoodies · 2nd/3rd $2,500 | "Use Verda to build something you couldn't on a laptop": fine-tune the floor-plan symbol detector or a small VLM on Verda GPUs, batch-process the 14,000 drawings (pre-render tiles, run the detector, convert IFC to light 3D) with Batch jobs, serve the model behind the Inference API or auto-scaling containers. Judged on ambition, fit, working prototype (documented), path to scale. Every team gets free credits (QR code on the table / verda.com/blog/junction-2026); workshop Fri 20:30. |
| **Best Use of Gemma** (Google DeepMind) | €1,000 each to 3 teams | A small open model is exactly Stoa's "small fine-tuned models" and "low inference cost" ask: Gemma as the cheap reader for room labels, schedules and question parsing, or fine-tuned for counting with points. |
| **Best Use of Gemini API** (Google DeepMind) | 1st €4,000 + $1,000 Google Cloud credits per member · 2nd €2,000 · 3rd €1,000 | Gemini's native box/segmentation output is our zero-shot grounding fallback and the live "ask anything about this historic drawing" demo. Biggest tech prize, open to every team. |
| **Best Use of Nemotron** (NVIDIA) | RTX 5080 for each of the two best teams | Reasoning/tool-use model for the IFC adaptive-exploration agent (Verda hosts a Nemotron endpoint). Pick Nemotron *or* Gemini for the agent, not both. |
| **Best use of Modal** | see platform page | Serve the detector/VLM on a GPU behind a URL the judges can open; $100 credits per participant. Alternative to Verda for serving. |

## Possible fits

| Tech challenge | Prizes | Note |
|---|---|---|
| Best Use of ElevenLabs (MLH) | Wireless earbuds | Voice in/out: "make blueprints talk" literally, hands-free on site. Small effort, small prize. |
| Best Use of Cloudinary | $500 Amazon gift cards per member | Deliver huge drawing images as zoomable, transformed tiles; overlays. Must enter via cld.media/hackathon-survey. |
| Best Use of Aiven / Tiger Data / MongoDB Atlas | €1,000/750/500 (Aiven) · small MLH prizes | Store per-question results, evidence and costs for the eval dashboard. |
| Best use of CRACI | LEGO / Red Bull / Slush lunch invite | CI on CRACI (`runs-on: craci`) running the accuracy regression on every push. ~10 minutes of setup. |
| Best Use of Codex (OpenAI) | $10,000 / $5,000 / $2,500 API credits | Only if we really build with Codex. |

## Suggested set
Verda Compute Track + Gemma (or Gemini) + one of Nemotron/Modal only if used. Don't let side prizes dilute the main build.

Hackerpack credits worth claiming now (Junction → Developer Tools and Assets): Verda €200, Claude $20/participant, Gemini API credits, Modal $100, OpenAI Codex $100, Featherless.
