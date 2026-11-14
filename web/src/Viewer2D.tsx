import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { DrawingDetail, Evidence, Room } from "./api";
import { centroid, cloud, loop, squiggle, strike, trace } from "./sketch";

export type Theme = "blueprint" | "paper";

interface Props {
  detail: DrawingDetail;
  theme: Theme;
  evidence: Evidence[];
  context: Evidence[];
  visible: Set<number>;  // the items marked so far
  sweepAt: number | null;  // where the reading line is, in image pixels, while items are being marked
  excluded: Set<number>;
  hover: number | null;
  focus: number | null;  // an item being inspected: the view moves to it
  leftOut: Room[];
  focusKey: string;
  onHover(n: number | null): void;
  onToggle(n: number): void;
}

interface Transform {
  k: number;
  x: number;
  y: number;
}

type Box = [number, number, number, number];

export function boxOf(item: Evidence): Box | null {
  if (item.points?.length) {
    const xs = item.points.map((p) => p[0]), ys = item.points.map((p) => p[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }
  if (item.bbox) return item.bbox;
  if (item.x !== undefined && item.y !== undefined) return [item.x, item.y, item.x, item.y];
  return null;
}

export function anchorOf(item: Evidence): [number, number] | null {
  if (item.x !== undefined && item.y !== undefined) return [item.x, item.y];
  if (item.bbox) return [(item.bbox[0] + item.bbox[2]) / 2, (item.bbox[1] + item.bbox[3]) / 2];
  if (item.points?.length) return centroid(item.points);
  return null;
}

function boundsOf(items: Evidence[]): Box | null {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const item of items) {
    const box = boxOf(item);
    if (!box) continue;
    x0 = Math.min(x0, box[0]); y0 = Math.min(y0, box[1]); x1 = Math.max(x1, box[2]); y1 = Math.max(y1, box[3]);
  }
  return Number.isFinite(x0) ? [x0, y0, x1, y1] : null;
}

const points = (list: [number, number][]) => list.map((p) => p.join(",")).join(" ");

export function Viewer2D(props: Props) {
  const { detail, theme, evidence, context, visible, sweepAt, excluded, hover, focus, leftOut, focusKey, onHover, onToggle } = props;
  const frame = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [t, setT] = useState<Transform>({ k: 1, x: 0, y: 0 });
  const [gliding, setGliding] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [tip, setTip] = useState<{ x: number; y: number; room: Room } | null>(null);
  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const { width, height, url } = detail.view;

  const fitTo = useCallback(
    (box: Box | null, pad = 0.9): Transform => {
      const [x0, y0, x1, y1] = box ?? [0, 0, width, height];
      const bw = Math.max(x1 - x0, 1), bh = Math.max(y1 - y0, 1);
      const k = Math.min((size.w * pad) / bw, (size.h * pad) / bh, 8);
      return { k, x: (size.w - bw * k) / 2 - x0 * k, y: (size.h - bh * k) / 2 - y0 * k };
    },
    [width, height, size.w, size.h],
  );

  const glide = useCallback((next: Transform) => {
    setGliding(true);
    setT(next);
    window.setTimeout(() => setGliding(false), 780);
  }, []);

  useLayoutEffect(() => {
    const el = frame.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    observer.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => observer.disconnect();
  }, []);

  // A new drawing, or a new frame size: show the whole sheet.
  useLayoutEffect(() => {
    if (size.w && size.h) setT(fitTo(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail.id, detail.view.page, size.w, size.h]);

  useEffect(() => setLoaded(false), [url]);

  // A new answer: move to its evidence when that would be small with the whole sheet in view.
  useEffect(() => {
    if (!focusKey || !size.w) return;
    const box = boundsOf(evidence);
    const whole = fitTo(null);
    if (!box) return glide(whole);
    const onScreen = Math.max(box[2] - box[0], box[3] - box[1]) * whole.k;
    const margin = 70 / whole.k;
    glide(onScreen < 0.42 * Math.min(size.w, size.h)
      ? fitTo([box[0] - margin, box[1] - 1.3 * margin, box[2] + margin, box[3] + margin], 0.86) : whole);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusKey]);

  // Inspecting one item: bring it close, with its surroundings.
  useEffect(() => {
    if (focus === null || !size.w) return;
    const item = evidence.find((e) => e.n === focus);
    const box = item && boxOf(item);
    if (!box) return;
    const reach = Math.max(box[2] - box[0], box[3] - box[1], 40) * 2.2 + 120;
    const cx = (box[0] + box[2]) / 2, cy = (box[1] + box[3]) / 2;
    glide(fitTo([cx - reach / 2, cy - reach / 2, cx + reach / 2, cy + reach / 2], 1));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus]);

  useEffect(() => {
    const el = frame.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
      setT((prev) => {
        const k = Math.min(30, Math.max(0.03, prev.k * Math.exp(-e.deltaY * 0.0016)));
        return { k, x: cx - ((cx - prev.x) * k) / prev.k, y: cy - ((cy - prev.y) * k) / prev.k };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const zoomBy = (factor: number) => {
    const cx = size.w / 2, cy = size.h / 2;
    const k = Math.min(30, Math.max(0.03, t.k * factor));
    glide({ k, x: cx - ((cx - t.x) * k) / t.k, y: cy - ((cy - t.y) * k) / t.k });
  };

  const shown = useMemo(() => evidence.filter((e) => visible.has(e.n ?? -1)), [evidence, visible]);
  const u = 1 / t.k;  // one screen pixel, in image pixels
  const pen = 2.5 * u;

  return (
    <div
      ref={frame}
      className={`viewer2d theme-${theme}`}
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        drag.current = { x: e.clientX, y: e.clientY, moved: false };
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (!d) return;
        const dx = e.clientX - d.x, dy = e.clientY - d.y;
        if (!d.moved && Math.hypot(dx, dy) < 4) return;
        d.moved = true;
        d.x = e.clientX;
        d.y = e.clientY;
        setTip(null);
        setT((prev) => ({ ...prev, x: prev.x + dx, y: prev.y + dy }));
      }}
      onPointerUp={() => { window.setTimeout(() => (drag.current = null), 0); }}
      onDoubleClick={() => glide(fitTo(null))}
    >
      <div
        className={`sheet${gliding ? " gliding" : ""}${loaded ? " loaded" : ""}`}
        style={{ width, height, transform: `translate(${t.x}px, ${t.y}px) scale(${t.k})` }}
      >
        {url && <img src={url} width={width} height={height} alt="" draggable={false} onLoad={() => setLoaded(true)} />}
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
          <defs>
            <pattern id="hatch" width={11 * u} height={11 * u} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2={11 * u} className="hatch-line" strokeWidth={2.2 * u} />
            </pattern>
            <linearGradient id="sweep-glow" x1="0" x2="1" y1="0" y2="0">
              <stop offset="0" className="sweep-stop-0" />
              <stop offset="1" className="sweep-stop-1" />
            </linearGradient>
          </defs>

          {/* Every room of the plan: invisible until hovered, so any area can be checked. */}
          {detail.rooms.map((room) => (
            <polygon
              key={room.ref}
              className="room-hit"
              points={points(room.points)}
              strokeWidth={1.5 * u}
              strokeDasharray={`${5 * u} ${4 * u}`}
              onPointerMove={(e) => {
                if (drag.current?.moved) return;
                const rect = frame.current!.getBoundingClientRect();
                setTip({ x: e.clientX - rect.left, y: e.clientY - rect.top, room });
              }}
              onPointerLeave={() => setTip(null)}
            />
          ))}

          {/* Rooms a sum leaves out (balconies and other outdoor space): hatched, with a note. */}
          {leftOut.map((room) => {
            const [cx, cy] = centroid(room.points);
            return (
              <g key={room.ref} className="left-out">
                <polygon points={points(room.points)} fill="url(#hatch)" strokeWidth={1.6 * u} strokeDasharray={`${6 * u} ${5 * u}`} />
                <g transform={`translate(${cx},${cy}) scale(${u})`}><text className="hand note" textAnchor="middle" y={-12}>left out</text></g>
              </g>
            );
          })}

          {/* Measured rooms: coloured in with a highlighter, traced with the pen, the area written in. */}
          {shown.filter((e) => e.points).map((item) => {
            const [cx, mid] = centroid(item.points!);
            const ys = item.points!.map((q) => q[1]);
            const xs = item.points!.map((q) => q[0]);
            const cy = mid + 0.24 * (Math.max(...ys) - Math.min(...ys));  // below the room's own printed text
            // Written to fit the room: a cupboard gets small figures, a living room large ones.
            const across = (Math.max(...xs) - Math.min(...xs)) * t.k;
            const lettering = Math.min(22, Math.max(10.5, across / 5.2));
            const off = excluded.has(item.n!);
            return (
              <g
                key={`room-${item.n}`}
                className={`mark room${hover === item.n ? " hot" : ""}${off ? " off" : ""}`}
                onPointerEnter={() => onHover(item.n!)}
                onPointerLeave={() => onHover(null)}
                onClick={() => !drag.current?.moved && onToggle(item.n!)}
              >
                <polygon className="highlighter" points={points(item.points!)} />
                <path className="ink draw" pathLength={1} d={trace(item.points!, item.n!, 1.6 * u)} strokeWidth={pen} />
                <g transform={`translate(${cx},${cy}) scale(${u})`}>
                  <text className="hand figure" textAnchor="middle" y={lettering / 3} style={{ fontSize: lettering, strokeWidth: lettering / 4.5 }}>
                    {item.area_m2 != null ? `${item.area_m2.toFixed(2)} m²` : item.n}
                  </text>
                </g>
              </g>
            );
          })}

          {/* Text the engine read and did not count: the type line it checked against, labels it doubted. */}
          {context.map((item, i) => {
            const [x0, y0, x1, y1] = item.bbox!;
            const doubt = item.role === "not counted";
            return (
              <g key={`ctx-${i}`} className={`mark aside${doubt ? " doubt" : ""}`}>
                {doubt
                  ? <path className="ink draw" pathLength={1} d={cloud(x0 - 6 * u, y0 - 6 * u, x1 + 6 * u, y1 + 6 * u, 7 * u)} strokeWidth={1.8 * u} />
                  : <path className="ink draw" pathLength={1} d={squiggle(x0 - 2 * u, x1 + 2 * u, y1 + 5 * u, 2.2 * u)} strokeWidth={1.8 * u} />}
                <g transform={`translate(${(x0 + x1) / 2},${doubt ? y0 - 9 * u : y1 + 9 * u}) scale(${u})`}>
                  <text className="hand note" textAnchor="middle" y={doubt ? -4 : 16}>{doubt ? "not counted" : item.role}</text>
                </g>
              </g>
            );
          })}

          {/* Counted items: a loop round each one and its number, as a reviewer would mark them. */}
          {shown.filter((e) => !e.points).map((item) => {
            const box = boxOf(item)!;
            const cx = (box[0] + box[2]) / 2, cy = (box[1] + box[3]) / 2;
            const rx = Math.max(((box[2] - box[0]) / 2) * 1.5, 12 * u), ry = Math.max(((box[3] - box[1]) / 2) * 1.5, 12 * u);
            const off = excluded.has(item.n!);
            return (
              <g
                key={`loop-${item.n}`}
                className={`mark count${hover === item.n ? " hot" : ""}${off ? " off" : ""}`}
                onPointerEnter={() => onHover(item.n!)}
                onPointerLeave={() => onHover(null)}
                onClick={(e) => { e.stopPropagation(); if (!drag.current?.moved) onToggle(item.n!); }}
              >
                <ellipse className="hit" cx={cx} cy={cy} rx={rx * 1.15} ry={ry * 1.15} />
                <path className="ink draw" pathLength={1} d={loop(cx, cy, rx, ry, item.n!)} strokeWidth={hover === item.n ? pen * 1.7 : pen} />
                {off && <path className="ink" d={strike(cx, cy, rx * 0.8, ry * 0.8, item.n!)} strokeWidth={pen} />}
                <g transform={`translate(${cx + rx * 0.78},${cy - ry * 0.78}) scale(${u})`}>
                  <text className="hand number" x={5} y={-3}>{item.n}</text>
                </g>
              </g>
            );
          })}

          {/* The reading line that sweeps the sheet while items are marked. */}
          {sweepAt !== null && (
            <g className="sweep" style={{ transform: `translateX(${sweepAt}px)` }}>
              <rect x={-90 * u} y={0} width={90 * u} height={height} fill="url(#sweep-glow)" />
              <line x1={0} y1={0} x2={0} y2={height} strokeWidth={1.6 * u} />
            </g>
          )}
        </svg>
      </div>

      {tip && (
        <div className="room-tip" style={{ left: tip.x + 14, top: tip.y + 14 }}>
          <strong>{[tip.room.name, tip.room.number].filter(Boolean).join(" ") || "Unnamed room"}</strong>
          <span>{tip.room.area_m2.toFixed(2)} m² from its outline{tip.room.outdoor ? " · outdoor" : ""}</span>
        </div>
      )}

      <div className="viewer-tools" onPointerDown={(e) => e.stopPropagation()} onDoubleClick={(e) => e.stopPropagation()}>
        <button onClick={() => zoomBy(1.5)} aria-label="Zoom in">+</button>
        <button onClick={() => zoomBy(1 / 1.5)} aria-label="Zoom out">−</button>
        <button onClick={() => glide(fitTo(null))} aria-label="Fit the sheet" className="wide">Fit</button>
      </div>
      {detail.view.note && <div className="viewer-note">{detail.view.note}</div>}
    </div>
  );
}
