# Who Stoa is, and what that means for our build

_From stoabuild.com (home, /product, /case-examples, /about/team, /about/security), read on 13 Nov 2026. Figures are Stoa's own claims; the site gives no methodology for them._

## The product today

Stoa is an AI assistant for construction projects. It connects read-only to the project's systems and answers questions from the project's own documents: drawings, specifications, contracts, schedule, quality inspections, meeting minutes, email.

- **Connections:** Autodesk Construction Cloud, SharePoint, M-Files, Primavera P6, OneDrive, Google Drive, Dalux, Congrid. "Read-only, and it stays in sync." Permissions follow the source system.
- **Ask your project (chat):** reads drawings visually, not only their text. "Every sentence links to the document and page it came from." Answers in the question's language. `@` steers a question to a document or folder. UI shows "Show thought process", numbered citations and a "Where it is set out" list. "When the documents do not hold the answer, Stoa says so." The UI also says: **"AI can make mistakes. Verify important information."**
- **Linked information on sheets:** "Every code on the sheet is live": clicking a wall type opens its defining detail, the schedule that lists it, and the governing spec clause. "Each result opens the document at the exact location."
- **Outputs:** work-phase plans (checks inputs, flags what is missing), claims drafted from the record, contract review cited to clauses, tender requests.
- **Skills:** saved, shareable workflows that run the same steps each time.
- **Changes:** detects changes between drawing revisions "whether or not anyone announced them"; visual compare modes Plain, Wipe, Overlay, Change boxes; daily summary email.
- **Awareness:** one project overview of blockers, reissued documents, open RFIs, decisions next to the programme.
- **Operate:** scheduled automations; reports export to Word, Excel, PDF.

## Who uses it
Project engineers and project managers at Nordic contractors, on projects "from data halls to rail". Stated: 20+ Nordic sites, €4B+ of project value, 2–8 hours a week saved per project engineer, 40% shorter time from problem raised to decision, €3M+ in mistakes caught before site (design-partner projects, 7/2026). Only one case is written up: a large data-centre build (frequent change notices, tight commissioning).

## Trust and data stance
EU storage and processing (incl. embeddings); no training on customer data; answers only from the project's own documents ("keeps citations exact and hallucinations rare"); every AI answer labelled and traced to its source, framed as support for EU AI Act transparency; deletion within 30 days; GDPR Art. 28 DPA; ISO/IEC 27001 in progress.

## Team
Co-founder & CPO **Taavi Paananen** is the only person named with a role; five founders in the photo. Team backgrounds listed: SRV, McKinsey, AWS, Supercell, SEB, ABB, Peikko, Wolt, Slush, Sensofusion, Lujabetoni. Backed by founders and leaders of Lifeline Ventures, Wave Ventures, Illusian, Zalando, Nokia, Supercell, Swappie, Wolt and AMD Silo AI (per the team page). Taavi's framing: the information that would prevent most mistakes already exists, but in the wrong system at the wrong time.

## What this tells us to build and say

1. **Their gap is our pitch.** Stoa cites *where* an answer came from, but still tells users to verify. Quantities (counts, areas) from drawings are exactly where citations aren't enough: a page reference doesn't prove there are 7 windows. Our certified answers with per-item evidence close that gap. See `03-strategy/RELIABILITY.md`.
2. **Speak their UI language.** Clicking evidence should open the drawing at the exact location (their pattern). Numbered citations ↔ our numbered pins. "Show thought process" ↔ our "Why verified" panel. Answer in the question's language.
3. **Fit their architecture.** Our engines are tools their assistant could call: `count(symbol, drawing)`, `area(room, drawing)`, `ifc_query(...)`, each returning evidence + a verified/check/decline status. Present it as an API/skill, not a separate app.
4. **Their customers are contractors, not architects.** Use quantity-takeoff, procurement and cost framing (doors to order, m² to finish), not design-studio framing.
5. **Revisions matter to them.** A strong "next step" slide: run the same takeoff on two revisions and show what changed (counts and areas per room), using their Overlay/Change-boxes idea.
6. **EU / trust framing.** Note that our deterministic engines run locally or on EU infrastructure and need no customer data for training.
7. **Ask at the booth:** how they will judge accuracy; whether training on public annotations is OK; which quantity questions their customers ask most.
