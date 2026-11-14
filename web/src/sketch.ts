// Hand-drawn marks, the way an engineer redlines a drawing: a wobbly loop round a
// counted item, a scalloped revision cloud round a doubtful one, a wavy underline.
// Every shape is seeded, so the same item is always drawn the same way.

function random(seed: number): () => number {
  let s = (Math.floor(seed) * 2654435761) >>> 0 || 1;
  return () => {
    s ^= s << 13;
    s >>>= 0;
    s ^= s >>> 17;
    s ^= s << 5;
    s >>>= 0;
    return s / 4294967296;
  };
}

type Point = [number, number];

// A smooth curve through the points (Catmull-Rom, written as cubic Béziers).
function curve(points: Point[]): string {
  if (points.length < 2) return "";
  const at = (i: number) => points[Math.max(0, Math.min(points.length - 1, i))];
  let d = `M${points[0][0].toFixed(1)} ${points[0][1].toFixed(1)}`;
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = at(i - 1), p1 = at(i), p2 = at(i + 1), p3 = at(i + 2);
    const c1: Point = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2: Point = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += `C${c1[0].toFixed(1)} ${c1[1].toFixed(1)} ${c2[0].toFixed(1)} ${c2[1].toFixed(1)} ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`;
  }
  return d;
}

/** A loop drawn in one stroke round a centre: slightly uneven, and overshooting where it closes. */
export function loop(cx: number, cy: number, rx: number, ry: number, seed: number): string {
  const rand = random(seed + 17);
  const steps = 11;
  const start = -Math.PI * (0.55 + rand() * 0.5);
  const sweep = Math.PI * 2 * (1.07 + rand() * 0.07);
  const tilt = (rand() - 0.5) * 0.5;
  const points: Point[] = [];
  for (let i = 0; i <= steps; i++) {
    const t = start + (sweep * i) / steps;
    const grow = 1 + (rand() - 0.5) * 0.1 + (i / steps) * 0.07;  // the pen drifts outwards
    const x = Math.cos(t) * rx * grow, y = Math.sin(t) * ry * grow;
    points.push([cx + x * Math.cos(tilt) - y * Math.sin(tilt), cy + x * Math.sin(tilt) + y * Math.cos(tilt)]);
  }
  return curve(points);
}

/** A revision cloud round a rectangle: the mark for "look at this again". */
export function cloud(x0: number, y0: number, x1: number, y1: number, bump: number): string {
  const corners: Point[] = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
  let d = `M${x0.toFixed(1)} ${y0.toFixed(1)}`;
  for (let side = 0; side < 4; side++) {
    const [ax, ay] = corners[side], [bx, by] = corners[(side + 1) % 4];
    const length = Math.hypot(bx - ax, by - ay);
    const count = Math.max(2, Math.round(length / (bump * 1.7)));
    for (let i = 1; i <= count; i++) {
      const x = ax + ((bx - ax) * i) / count, y = ay + ((by - ay) * i) / count;
      const r = length / count / 1.7;
      d += `A${r.toFixed(1)} ${r.toFixed(1)} 0 0 1 ${x.toFixed(1)} ${y.toFixed(1)}`;
    }
  }
  return `${d}Z`;
}

/** A polygon traced by hand: each corner lands a little off, and the stroke closes past its start. */
export function trace(points: Point[], seed: number, wobble: number): string {
  const rand = random(seed + 5);
  const jitter = (): number => (rand() - 0.5) * 2 * wobble;
  const moved = points.map(([x, y]) => [x + jitter(), y + jitter()] as Point);
  const [sx, sy] = moved[0];
  let d = `M${sx.toFixed(1)} ${sy.toFixed(1)}`;
  for (let i = 1; i <= moved.length; i++) {
    const [x, y] = moved[i % moved.length];
    d += `L${x.toFixed(1)} ${y.toFixed(1)}`;
  }
  const [nx, ny] = moved[1 % moved.length];
  return `${d}L${(sx + (nx - sx) * 0.06).toFixed(1)} ${(sy + (ny - sy) * 0.06).toFixed(1)}`;
}

/** A wavy underline from one x to another. */
export function squiggle(x0: number, x1: number, y: number, amplitude: number): string {
  const waves = Math.max(2, Math.round((x1 - x0) / (amplitude * 5)));
  const points: Point[] = [];
  for (let i = 0; i <= waves * 2; i++) {
    points.push([x0 + ((x1 - x0) * i) / (waves * 2), y + (i % 2 ? amplitude : -amplitude)]);
  }
  return curve(points);
}

/** A cross struck through something left out. */
export function strike(cx: number, cy: number, rx: number, ry: number, seed: number): string {
  const rand = random(seed + 91);
  const j = () => (rand() - 0.5) * 0.3;
  return `M${(cx - rx * (1 + j())).toFixed(1)} ${(cy - ry * (1 + j())).toFixed(1)}L${(cx + rx * (1 + j())).toFixed(1)} ${(cy + ry * (1 + j())).toFixed(1)}` +
    `M${(cx + rx * (1 + j())).toFixed(1)} ${(cy - ry * (1 + j())).toFixed(1)}L${(cx - rx * (1 + j())).toFixed(1)} ${(cy + ry * (1 + j())).toFixed(1)}`;
}

export function centroid(points: Point[]): Point {
  let area = 0, cx = 0, cy = 0;
  for (let i = 0; i < points.length; i++) {
    const [x0, y0] = points[i];
    const [x1, y1] = points[(i + 1) % points.length];
    const cross = x0 * y1 - x1 * y0;
    area += cross;
    cx += (x0 + x1) * cross;
    cy += (y0 + y1) * cross;
  }
  if (Math.abs(area) < 1e-6) {
    const n = points.length || 1;
    return [points.reduce((s, p) => s + p[0], 0) / n, points.reduce((s, p) => s + p[1], 0) / n];
  }
  return [cx / (3 * area), cy / (3 * area)];
}
