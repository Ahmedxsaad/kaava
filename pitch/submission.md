# Junction submission: text to paste

Draft text for the fields of the project form on hackjunction.app. Figures are from the run log in `results/` (14 November 2026).

## Project name

Blueprints Talk

## Punchline

Ask a drawing. See where the answer came from.

## Challenge

Make Blueprints Talk (Stoa)

## Description

A wrong quantity becomes a wrong order. A vision model asked to count doors or measure rooms is right 62.5% of the time on our 200 development questions, and it cannot show which doors it counted.

Blueprints Talk computes the number instead of generating it. A router sends each question to the cheapest method that can answer it from the drawing itself: a query on the IFC model, the geometry of the DXF, or the labels printed on a scan, all at no cost. Only where a scan offers nothing else does Gemini step in, and even then it does not state a number: it draws a box round each item and the code counts the boxes.

Every answer ends in one of three states. Verified means measured: on drawings held back for testing, Verified counts were wrong 8 times in 6,354 and areas 6 times in 2,612. Check this shows the answer with its evidence and the reason it needs a look. Can't answer reliably gives no number and says why.

Every counted item is marked on the sheet and in a 3D model raised from the plan. Click one to leave it out and the answer follows.

On the same 200 questions the system is right 95.8% of the time at 40% of the model's cost, with 69 Verified answers and none wrong. We also report what did not work: two readings of a scan that agree are still wrong 1.3% of the time, so nothing on a scan is marked Verified.

## Technologies used

Python (FastAPI, IfcOpenShell, ezdxf, PyMuPDF, Shapely, RapidOCR), React, TypeScript, Three.js, Google Gemini API, Cloudinary, ElevenLabs, Docker.

## Tech challenges that apply

- Best Use of Gemini API: Gemini 4 Carbon boxes each counted item on a scan in its native detection format; Gemini 4 Hydrogen restates free-form questions for the exact engines. All headline figures are measured on Gemini.
- Best Use of Cloudinary: AI super-resolution for viewing small scans, and hosting of shareable evidence sheets. Their rules also ask for their Skills Pack and their survey (https://cld.media/hackathon-survey).
- Best Use of ElevenLabs: answers read aloud in the app; the narration of the pitch video.

## Video

`pitch/blueprints-talk-pitch.mp4` (1 min 57 s, built by `pitch/video/make.py`). Upload it where anyone can watch it and paste the link.

## Pitch deck

`pitch/blueprints-talk-deck.pdf` (built by `node pitch/build.mjs`).

## Still to fill in by hand

Demo link, source code link, team members' phone numbers and Discord names, and whether to be credited.
