import type { AskResult } from "./api";

// The verdict as an inked rubber stamp on the sheet, the way a checked drawing is marked.
// It carries the proof in its small print: how often answers of this kind were wrong on
// drawings held back for measuring.

function smallPrint(result: AskResult): string {
  const v = result.verified_because;
  if (result.state === "verified" && v) {
    const bound = (100 * v.error_upper_bound).toFixed(2);
    return `ERROR < ${bound}% · ${v.calibration_answers.toLocaleString("en")} HELD-OUT ANSWERS`;
  }
  if (result.state === "check") {
    if (result.method.startsWith("vlm")) return "READ FROM PIXELS BY A VISION MODEL";
    const bar = result.family?.threshold;
    if (bar != null && result.confidence != null) return `CHECKS SCORE ${result.confidence.toFixed(2)} · BAR ${bar.toFixed(2)}`;
    return "NOT YET PROVEN UNDER 1% WRONG";
  }
  return "NO NUMBER IS BETTER THAN A WRONG ONE";
}

const WORDS = {
  verified: { main: "VERIFIED", sub: "NO CHECK NEEDED" },
  check: { main: "CHECK THIS", sub: "EVIDENCE IS ON THE DRAWING" },
  declined: { main: "CAN'T ANSWER", sub: "NOT RELIABLY" },
} as const;

export function Stamp({ result }: { result: AskResult }) {
  const words = WORDS[result.state];
  return (
    <div className={`stamp ${result.state}`} aria-hidden>
      <svg viewBox="0 0 300 132">
        <defs>
          <filter id="stamp-ink" x="-5%" y="-10%" width="110%" height="120%">
            <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" seed="4" result="fine" />
            <feDisplacementMap in="SourceGraphic" in2="fine" scale="1.5" result="rough" />
            <feTurbulence type="fractalNoise" baseFrequency="0.03 0.5" numOctaves="2" seed="11" result="grain" />
            <feColorMatrix in="grain" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -2.2 1.95" result="worn" />
            <feComposite in="rough" in2="worn" operator="in" />
          </filter>
        </defs>
        <g filter="url(#stamp-ink)">
          <rect x="5" y="5" width="290" height="122" rx="9" className="edge outer" />
          <rect x="12" y="12" width="276" height="108" rx="5" className="edge inner" />
          {result.state === "verified" && <path className="edge tick" d="M28 62l13 14 26-32" />}
          {result.state === "check" && (<><circle className="edge tick" cx="44" cy="56" r="13" /><path className="edge tick" d="M54 67l12 13" /></>)}
          {result.state === "declined" && (<><circle className="edge tick" cx="46" cy="60" r="16" /><path className="edge tick" d="M35 71l22-22" /></>)}
          <text x="80" y="66" className="main" textLength={result.state === "verified" ? 196 : 200} lengthAdjust="spacingAndGlyphs">{words.main}</text>
          <text x="81" y="86" className="sub">{words.sub}</text>
          <line x1="24" y1="96" x2="276" y2="96" className="edge rule" />
          <text x="150" y="111" className="small" textAnchor="middle">{smallPrint(result)}</text>
        </g>
      </svg>
    </div>
  );
}
