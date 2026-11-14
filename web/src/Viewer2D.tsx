import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { DrawingDetail, Evidence, Room } from "./api";

interface Props {
  detail: DrawingDetail;
  evidence: Evidence[];
  revealed: number;
  excluded: Set<number>;
  hover: number | null;
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

const PIN = "M0 0C-3.5-7-11-11-11-21a11 11 0 1 1 22 0C11-11 3.5-7 0 0Z";

function centroid(points: [number, number][]): [number, number] {
  let area = 0;
  let cx = 0;
  let cy = 0;
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

function anchorOf(item: Evidence): [number, number] | null {
  if (item.x !== undefined && item.y !== undefined) return [item.x, item.y];
  if (item.bbox) return [(item.bbox[0] + item.bbox[2]) / 2, (item.bbox[1] + item.bbox[3]) / 2];
  if (item.points) return centroid(item.points);
  return null;
}

function boundsOf(items: Evidence[]): [number, number, number, number] | null {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const grow = (x: number, y: number) => {
    x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
  };
  for (const item of items) {
    if (item.bbox) { grow(item.bbox[0], item.bbox[1]); grow(item.bbox[2], item.bbox[3]); }
    else if (item.points) item.points.forEach(([x, y]) => grow(x, y));
    else if (item.x !== undefined && item.y !== undefined) grow(item.x, item.y);
  }
  return Number.isFinite(x0) ? [x0, y0, x1, y1] : null;
}

export function Viewer2D({ detail, evidence, revealed, excluded, hover, leftOut, focusKey, onHover, onToggle }: Props) {
  const frame = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [t, setT] = useState<Transform>({ k: 1, x: 0, y: 0 });
  const [gliding, setGliding] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [tip, setTip] = useState<{ x: number; y: number; room: Room } | null>(null);
  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const { width, height, url } = detail.view;

  const fitTo = useCallback(
    (box: [number, number, number, number] | null, pad = 0.9): Transform => {
      const [x0, y0, x1, y1] = box ?? [0, 0, width, height];
      const bw = Math.max(x1 - x0, 1), bh = Math.max(y1 - y0, 1);
      const k = Math.min((size.w * pad) / bw, (size.h * pad) / bh, 6);
      return { k, x: (size.w - bw * k) / 2 - x0 * k, y: (size.h - bh * k) / 2 - y0 * k };
    },
    [width, height, size.w, size.h],
  );

  const glide = useCallback((next: Transform) => {
    setGliding(true);
    setT(next);
    window.setTimeout(() => setGliding(false), 750);
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

  // A new answer: move to its evidence when that is a small part of the sheet.
  useEffect(() => {
    if (!focusKey || !size.w) return;
    const box = boundsOf(evidence);
    if (!box) return glide(fitTo(null));
    // Zoom in only when the evidence would be small on screen with the whole sheet in view.
    const whole = fitTo(null);
    const onScreen = Math.max(box[2] - box[0], box[3] - box[1]) * whole.k;
    const margin = 60 / whole.k;
    const padded: [number, number, number, number] = [box[0] - margin, box[1] - 1.3 * margin, box[2] + margin, box[3] + margin];
    glide(onScreen < 0.42 * Math.min(size.w, size.h) ? fitTo(padded, 0.86) : whole);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusKey]);

  useEffect(() => {
    const el = frame.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
      setT((prev) => {
        const k = Math.min(24, Math.max(0.03, prev.k * Math.exp(-e.deltaY * 0.0016)));
        return { k, x: cx - ((cx - prev.x) * k) / prev.k, y: cy - ((cy - prev.y) * k) / prev.k };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const zoomBy = (factor: number) => {
    const cx = size.w / 2, cy = size.h / 2;
    const k = Math.min(24, Math.max(0.03, t.k * factor));
    glide({ k, x: cx - ((cx - t.x) * k) / t.k, y: cy - ((cy - t.y) * k) / t.k });
  };

  const shown = useMemo(() => evidence.filter((e) => (e.n ?? 0) <= revealed), [evidence, revealed]);
  const inverse = 1 / t.k;
  // Pins keep their size on screen, but shrink a little when the whole sheet is in view.
  const pinScale = inverse * Math.min(1, Math.max(0.6, t.k * 2.4));

  return (
    <div
      ref={frame}
      className={`viewer2d${drag.current ? " dragging" : ""}`}
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
            <pattern id="hatch" width={10 * inverse} height={10 * inverse} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2={10 * inverse} className="hatch-line" strokeWidth={2.4 * inverse} />
            </pattern>
          </defs>

          {/* Every room of the plan: invisible until hovered, so any area can be checked. */}
          {detail.rooms.map((room) => (
            <polygon
              key={room.ref}
              className="room-hit"
              points={room.points.map((p) => p.join(",")).join(" ")}
              onPointerMove={(e) => {
                if (drag.current?.moved) return;
                const rect = frame.current!.getBoundingClientRect();
                setTip({ x: e.clientX - rect.left, y: e.clientY - rect.top, room });
              }}
              onPointerLeave={() => setTip(null)}
            />
          ))}

          {leftOut.map((room) => {
            const [cx, cy] = centroid(room.points);
            return (
              <g key={room.ref} className="left-out">
                <polygon points={room.points.map((p) => p.join(",")).join(" ")} fill="url(#hatch)" />
                <g transform={`translate(${cx},${cy}) scale(${inverse})`}>
                  <text className="left-out-label" textAnchor="middle" y={-14}>left out</text>
                </g>
              </g>
            );
          })}

          {shown.filter((e) => e.points).map((item) => {
            // The drawing prints a room's own name and area around its middle; the computed
            // area sits below that, so the two can be compared side by side.
            const [cx, mid] = centroid(item.points!);
            const ys = item.points!.map((q) => q[1]);
            const cy = mid + 0.24 * (Math.max(...ys) - Math.min(...ys));
            const off = excluded.has(item.n!);
            const label = item.area_m2 != null ? `${item.area_m2.toFixed(2)} m²` : String(item.n);
            const w = label.length * 7.4 + 16;
            return (
              <g
                key={`poly-${item.n}`}
                className={`ev-room${hover === item.n ? " hot" : ""}${off ? " off" : ""}`}
                onPointerEnter={() => onHover(item.n!)}
                onPointerLeave={() => onHover(null)}
                onClick={() => !drag.current?.moved && onToggle(item.n!)}
              >
                <polygon points={item.points!.map((p) => p.join(",")).join(" ")} />
                <g transform={`translate(${cx},${cy}) scale(${inverse})`}>
                  <g className="plate">
                    <rect x={-w / 2} y={-11} width={w} height={22} rx={4} />
                    <text textAnchor="middle" y={4.5}>{label}</text>
                  </g>
                </g>
              </g>
            );
          })}

          {shown.filter((e) => e.bbox && !e.points).map((item) => {
            const [x0, y0, x1, y1] = item.bbox!;
            const pad = 5 * inverse;
            return (
              <rect
                key={`box-${item.n}`}
                className={`ev-box${hover === item.n ? " hot" : ""}${excluded.has(item.n!) ? " off" : ""}`}
                x={x0 - pad} y={y0 - pad} width={x1 - x0 + 2 * pad} height={y1 - y0 + 2 * pad} rx={3 * inverse}
              />
            );
          })}

          {shown.filter((e) => !e.points && (e.bbox || e.x !== undefined)).map((item) => {
            const at = anchorOf(item);
            if (!at) return null;
            const off = excluded.has(item.n!);
            return (
              <g
                key={`pin-${item.n}`}
                transform={`translate(${at[0]},${at[1]}) scale(${pinScale})`}
                className={`pin${hover === item.n ? " hot" : ""}${off ? " off" : ""}`}
                onPointerEnter={() => onHover(item.n!)}
                onPointerLeave={() => onHover(null)}
                onClick={(e) => { e.stopPropagation(); if (!drag.current?.moved) onToggle(item.n!); }}
              >
                <g className="pin-drop">
                  <ellipse className="pin-shadow" cx="0" cy="0" rx="5" ry="2" />
                  <path d={PIN} />
                  <text textAnchor="middle" y={-17}>{item.n}</text>
                </g>
              </g>
            );
          })}
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
