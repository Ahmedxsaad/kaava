import { useEffect, useState } from "react";
import { api, type Stats } from "./api";
import { type Stop, TOUR } from "./tour";

// How it works, for someone who wants to know what is under the answers: the one rule, the route a
// question takes, what the vision model is given and what it may give back, when the system says no,
// and the hard cases, each one click away.

const ENGINES: { name: string; when: string; how: string; cost: string; state: string }[] = [
  { name: "IFC query", when: "a building model, or a plan rendered from one", how: "IfcOpenShell: elements enumerated, geometry and the model's own quantities", cost: "€0", state: "can be Verified" },
  { name: "DXF geometry", when: "a floor plan that comes with a DXF", how: "room outlines (holes cut out), door, window and fixture entities; checked against the areas printed on the plan", cost: "€0", state: "can be Verified" },
  { name: "Printed labels", when: "a scan, asked to count rooms", how: "local OCR reads the room labels; the code counts them and checks the apartment's type line", cost: "€0", state: "Check this" },
  { name: "Boxes on a scan", when: "a scan, asked to count symbols", how: "the vision model boxes each item; the code counts the boxes", cost: "about €0.006", state: "Check this" },
  { name: "Working shown", when: "a sheet with no model or DXF: a schedule, a slab plan, a piping plan", how: "the model names printed figures, rows or rooms; the code computes, counts and measures", cost: "€0.02 to €0.15", state: "Check this" },
];

const TOOLS: { tool: string; gives: string; does: string }[] = [
  { tool: "The sheet's texts", gives: "nothing: it receives them", does: "Reads every text on the sheet at full size, once: the PDF's own text layer, or local OCR tile by tile. The model works from this list, because at its own resolution a 2 mm figure is a smudge." },
  { tool: "A calculator", gives: "the printed figures it uses, each with its place, and an expression over them: a × (b + c)", does: "Evaluates the expression (plus, minus, times, divide and brackets; nothing else can run), converts the unit, and reads each figure again at the place marked." },
  { tool: "A counter", gives: "the list of rows or tags that count, each by the printed text that identifies it", does: "Counts the list. The same text named twice is one item." },
  { tool: "A closer look", gives: "one sentence on what counts as one item, and where on the sheet to search", does: "Cuts that part of the sheet into overlapping tiles and has each symbol boxed at full size, twice. Counts the boxes both passes drew; a box only one pass drew is shown as doubtful and left out." },
  { tool: "A ruler", gives: "which room, and a rough box around it", does: "Reads the scale from the scale bar, moves each side of the box onto the inner face of its wall, and measures area, perimeter, or baseboard with the door openings left out." },
];

const REFUSALS: { when: string; says: string }[] = [
  { when: "The sheet does not print what the question needs", says: "the sheet does not print what this needs: no dimension of this room is printed" },
  { when: "The program reads a figure differently from the model", says: "A figure could not be confirmed: the program reads 6,200 here, the model 6,800. No number is given." },
  { when: "Two independent workings of the same question disagree", says: "Two independent workings gave different results: 217.11 m² and 218.64 m². No number is given." },
  { when: "A room cannot be measured honestly", says: "no graphic scale bar was found · the room is not a plain rectangle · no wall found along the left side" },
  { when: "An exact engine's own checks do not hold", says: "the area printed in the room is 15.20 m²: they differ → Check this, not Verified" },
];

const HARD: { label: string; why: string }[] = [
  { label: "A room with a hole in it", why: "The outline encloses 17.23 m². A lift stands inside it; its 3.24 m² are cut out, and the hole is drawn." },
  { label: "Doors, and a door drawn twice", why: "The file lists 32 door outlines; 9 lie exactly on another. The answer is 23, and it says why." },
  { label: "Baseboards of a hall, measured on the sheet", why: "Nothing is printed. Walls from pixels, scale from the bar, 8 door openings left out of the length." },
  { label: "Slab area by type, from the printed dimensions", why: "Two dimension figures, each boxed and read again by OCR; the calculator multiplies." },
  { label: "Every door of a school, boxed tile by tile", why: "Over a hundred small symbols on a sheet 14,000 pixels wide: eight tiles at full size, looked at twice. Counted where both passes agree." },
  { label: "A question the plan cannot answer", why: "No number, and the reason. A wrong confident answer is the worst outcome." },
];

const pct = (v: number | null | undefined, digits = 2) => (v == null ? "—" : `${(100 * v).toFixed(digits)}%`);

export function How({ onOpen }: { onOpen(stop: Stop): void }) {
  const [stats, setStats] = useState<Stats | null>(null);
  useEffect(() => { api.stats().then(setStats).catch(() => undefined); }, []);
  const families = Object.entries(stats?.thresholds.families ?? {}).filter(([, f]) => f.threshold != null);
  const bench = stats?.sheets_bench;
  return (
    <main className="numbers guide">
      <div className="n-page">
        <header className="n-head">
          <h1>How it works</h1>
          <p>
            One rule holds everywhere: <b>a number is computed by code from things you can see on the drawing</b>: counted
            objects, measured geometry, printed figures. A language model never writes it. Where a model is used at all,
            its reply has no field for the answer.
          </p>
        </header>

        <section className="n-card">
          <h2>1. The route a question takes</h2>
          <p>A router sends each question to the cheapest method that can answer it from the drawing itself. The same drawing in a more structured format wins: IFC, then DXF, then vector PDF, then pixels.</p>
          <div className="how-route">
            {ENGINES.map((engine) => (
              <div key={engine.name} className="how-engine">
                <strong>{engine.name}</strong>
                <span className="when">{engine.when}</span>
                <span className="what">{engine.how}</span>
                <em><b>{engine.cost}</b> · {engine.state}</em>
              </div>
            ))}
          </div>
          <p className="how-after">Then the checks run, and the answer ends in one of three states: <b className="s-verified">Verified</b>, <b className="s-check">Check this</b> or <b className="s-declined">Can't answer reliably</b>.</p>
        </section>

        <section className="n-card">
          <h2>2. What the vision model is given, and what it may give back</h2>
          <p>
            On a sheet with no model or DXF behind it, the model has five tools. Each one is a job the program does; the
            model only says what to read, count or measure. It is told not to estimate a size by eye, and it cannot state
            the result: the reply format below is everything it can say.
          </p>
          <table className="how-tools">
            <thead><tr><th>Tool</th><th>The model gives</th><th>The program does</th></tr></thead>
            <tbody>
              {TOOLS.map((row) => <tr key={row.tool}><td><b>{row.tool}</b></td><td>{row.gives}</td><td>{row.does}</td></tr>)}
            </tbody>
          </table>
          <pre className="how-schema">{`reply = {
  items:        [ { text_id, box_2d, label } ]            // things to count
  figures:      [ { name, text_id, box_2d, printed, value, unit, what } ]
  expression:   "a * (b + c)"                             // for the calculator
  result_unit:  "mm2"
  look_closer:  { what, box_2d }                          // many small symbols
  measure:      { quantity, rooms: [ { name, box_2d } ] } // for the ruler
  text, sources                                           // a descriptive answer and where it was read
  cannot_tell:  true | false                              // "this sheet does not let me answer"
  basis:        "one or two sentences"
}                                                         // there is no field for the answer`}</pre>
          <h3 className="how-sub">A question in your own words, on a plan or a model</h3>
          <p>
            The exact engines read a fixed set of phrasings. Anything else ("the windows in Stanza", "the area of the
            Essen and the Reduit together") goes to the language model with the drawing's own list of rooms, storeys and
            items. It gives back a query: which rooms, and what to count or measure. The engine runs the query on the
            geometry and draws the rooms it searched before the items it counted.
          </p>
          <pre className="how-schema">{`query = {
  measure:  area | perimeter | baseboard | count_rooms | count_items
  rooms:    [ ids from the drawing's own list ]            // "Stanza" -> the 13 rooms with that name
  item:     door | window | toilet | ...
  combine:  sum | largest | smallest | average
  cannot:   true | false                                   // "this drawing does not hold that"
}                                                          // again, no field for the answer`}</pre>
          <p className="how-after">
            The question is read twice, independently. Two readings that differ give no number, and both readings are
            shown. A door or a window belongs to a room when at least half of its length runs along one of the room's
            walls; one that only touches a corner is drawn as left out. These answers are never marked Verified.
          </p>
        </section>

        <section className="n-card">
          <h2>3. When it says no</h2>
          <p>Abstaining is always allowed, and a wrong confident answer is the worst outcome. These are the ways an answer on a sheet ends without a number, in the words the app uses.</p>
          <ul className="how-no">
            {REFUSALS.map((row) => <li key={row.when}><b>{row.when}</b><span>{row.says}</span></li>)}
          </ul>
          <p className="how-after">Where there is something to work out (arithmetic over several figures, a long list, many small symbols) it is done twice, independently. Only two workings that come to the same result give a number.</p>
        </section>

        <section className="n-card">
          <h2>4. How "Verified" is earned</h2>
          <p>
            Drawings are split by drawing into development, calibration and test. Engines were built on development
            drawings only. On the calibration drawings, each engine's answers are sorted by how many of their checks
            hold; an answer is Verified only at a level where the error rate stays under 1% with 95% confidence.
            The test split has not been opened.
          </p>
          <ul className="how-verified">
            {families.map(([name, f]) => (
              <li key={name}>
                <b>{name.startsWith("dxf") ? "Swiss floors" : name.split(":")[0]} · {name.endsWith("area") ? "areas" : "counts"}</b>
                <span>{f.errors_at_threshold} wrong in {f.answers_at_threshold?.toLocaleString("en")} held-back answers: below {pct(f.upper_bound)}</span>
              </li>
            ))}
            <li><b>Everything a model had a hand in</b><span>never Verified, however sure it is</span></li>
          </ul>
        </section>

        <section className="n-card">
          <h2>5. Hard cases, one click away</h2>
          <p>Each opens the real drawing with the answer and its evidence.</p>
          <div className="how-hard">
            {HARD.map((item) => {
              const stop = TOUR.find((s) => s.label === item.label);
              return stop ? (
                <button key={item.label} onClick={() => onOpen(stop)}>
                  <strong>{item.label}</strong>
                  <span>{item.why}</span>
                </button>
              ) : null;
            })}
          </div>
        </section>

        {bench?.system && (
          <section className="n-card">
            <h2>6. Stoa's sample sheets, scored</h2>
            <p>
              Stoa posted four sheets without answers. We wrote an answer key for {bench.system.n} questions whose answers
              are plain from the sheet (printed in a table or a callout, added up by hand, or labels counted by hand); it
              is in <code>examples/stoa/questions.jsonl</code>, each line with where its answer comes from. The key is ours.
              These are questions a frontier model can also read off the sheet; what differs is what happens on the one
              it cannot: a confident wrong number, or no number.
            </p>
            <table className="how-tools how-bench">
              <thead><tr><th /><th>Right</th><th>Wrong</th><th>No number</th><th>Cost per question</th></tr></thead>
              <tbody>
                <tr><td><b>This system</b> (working shown)</td><td><b>{bench.system.correct}</b> of {bench.system.n}</td><td>{bench.system.wrong}</td><td>{bench.system.declined}</td><td>€{bench.system.eur_per_question.toFixed(3)}</td></tr>
                {bench.alone && <tr><td>Gemini alone, from the picture</td><td><b>{bench.alone.correct}</b> of {bench.alone.n}</td><td>{bench.alone.wrong}</td><td>{bench.alone.declined}</td><td>€{bench.alone.eur_per_question.toFixed(3)}</td></tr>}
              </tbody>
            </table>
          </section>
        )}
      </div>
    </main>
  );
}
