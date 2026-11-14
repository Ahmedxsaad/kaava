import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api, type DrawingDetail, type Evidence, type Room } from "./api";
import { type Box, centroid, pointList } from "./geometry";

interface Props {
  detail: DrawingDetail;
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

type Tool = "move" | "measure" | "loupe";

interface Measure {
  a: [number, number];
  b: [number, number];
}

const LENS = 200;  // the loupe's diameter on screen

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

// A small label on a white plate, sized to its text.
function Plate({ text, className = "" }: { text: string; className?: string }) {
  const w = text.length * 6.5 + 16;
  return (
    <g className={`plate ${className}`}>
      <rect x={-w / 2} y={-11} width={w} height={22} rx={11} />
      <text textAnchor="middle" y={4}>{text}</text>
    </g>
  );
}

export function Viewer2D(props: Props) {
  const { detail, evidence, context, visible, sweepAt, excluded, hover, focus, leftOut, focusKey, onHover, onToggle } = props;
  const frame = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [t, setT] = useState<Transform>({ k: 1, x: 0, y: 0 });
  const [gliding, setGliding] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [tip, setTip] = useState<{ x: number; y: number; room: Room } | null>(null);
  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const [tool, setTool] = useState<Tool>("move");
  const [measures, setMeasures] = useState<Measure[]>([]);
  const [drawing, setDrawing] = useState<Measure | null>(null);  // a measurement being drawn
  const [lens, setLens] = useState<{ x: number; y: number } | null>(null);
  const { width, height } = detail.view;
  const pxPerM = detail.view.px_per_m;
  // A small scan can be swapped for Cloudinary's AI-enlarged copy: the same picture, sharper.
  // The marks keep their place, because the engines read (and measure on) the original.
  const [enlarged, setEnlarged] = useState<{ url: string } | "working" | null>(null);
  const [sharp, setSharp] = useState<string | null>(null);  // the enlarged copy that has finished loading
  const [attempt, setAttempt] = useState(0);  // Cloudinary can answer "still working" for a format it has not made yet
  const url = detail.view.url;
  const sharpUrl = enlarged && enlarged !== "working" ? enlarged.url : null;
  useEffect(() => { setEnlarged(null); setSharp(null); setAttempt(0); }, [detail.id]);
  const retry = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(retry.current), [detail.id, sharpUrl]);
  const enlargedFailed = () => {
    if (attempt >= 12) return setEnlarged(null);  // give up quietly: the scan as it came stays on screen
    retry.current = window.setTimeout(() => setAttempt((n) => n + 1), 2000);
  };
  const enlarge = () => {
    if (enlarged) return setEnlarged(null);
    setAttempt(0);
    setEnlarged("working");
    api.enhanced(detail.id).then(setEnlarged).catch(() => setEnlarged(null));
  };

  // Screen position -> image pixels.
  const toImage = (e: { clientX: number; clientY: number }): [number, number] => {
    const rect = frame.current!.getBoundingClientRect();
    return [(e.clientX - rect.left - t.x) / t.k, (e.clientY - rect.top - t.y) / t.k];
  };
  useEffect(() => { setMeasures([]); setDrawing(null); setTool("move"); }, [detail.id]);

  const fitTo = useCallback(
    (box: Box | null, pad = 0.88): Transform => {
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
    const reach = Math.max(box[2] - box[0], box[3] - box[1]) * 3 + 0.16 * Math.max(width, height);
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
  const single = pxPerM && evidence.filter((e) => e.points).length === 1 ? shown.find((e) => e.points) : undefined;

  return (
    <div
      ref={frame}
      className="viewer2d"
      data-tool={tool}
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
        if (tool === "measure") {
          const at = toImage(e);
          setDrawing({ a: at, b: at });
          return;
        }
        drag.current = { x: e.clientX, y: e.clientY, moved: false };
      }}
      onPointerMove={(e) => {
        if (tool === "loupe") {
          const rect = frame.current!.getBoundingClientRect();
          setLens({ x: e.clientX - rect.left, y: e.clientY - rect.top });
        }
        if (drawing) {
          setDrawing({ a: drawing.a, b: toImage(e) });
          return;
        }
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
      onPointerUp={(e) => {
        if (drawing) {
          if (Math.hypot(drawing.b[0] - drawing.a[0], drawing.b[1] - drawing.a[1]) * t.k > 6) setMeasures((list) => [...list, drawing]);
          setDrawing(null);
          return;
        }
        // A press that did not drag is a click on whatever mark lies under it. The frame holds the
        // pointer while panning, so the browser would hand the click to the frame, not to the mark:
        // find the mark here instead.
        if (drag.current && !drag.current.moved && tool === "move") {
          const hit = document.elementFromPoint(e.clientX, e.clientY)?.closest("[data-mark]");
          const n = Number(hit?.getAttribute("data-mark"));
          if (hit && Number.isFinite(n)) onToggle(n);
        }
        window.setTimeout(() => (drag.current = null), 0);
      }}
      onPointerLeave={() => setLens(null)}
      onDoubleClick={() => tool === "move" && glide(fitTo(null))}
    >
      <div
        className={`sheet${gliding ? " gliding" : ""}${loaded ? " loaded" : ""}`}
        style={{ width, height, transform: `translate(${t.x}px, ${t.y}px) scale(${t.k})` }}
      >
        {url && <img src={url} width={width} height={height} alt="" draggable={false} onLoad={() => setLoaded(true)} />}
        {sharpUrl && (
          <img key={attempt} className={`sharp${sharp === sharpUrl ? " in" : ""}`} src={sharpUrl} width={width} height={height} alt="" draggable={false}
            onLoad={() => setSharp(sharpUrl)} onError={enlargedFailed} />
        )}
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
          <defs>
            <pattern id="hatch" width={9 * u} height={9 * u} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2={9 * u} className="hatch-line" strokeWidth={1.2 * u} />
            </pattern>
            <linearGradient id="sweep-trail" x1="0" x2="1" y1="0" y2="0">
              <stop offset="0" className="sweep-stop-0" />
              <stop offset="1" className="sweep-stop-1" />
            </linearGradient>
          </defs>

          {/* Every room of the plan: invisible until hovered, so any area can be checked. */}
          {detail.rooms.map((room) => (
            <polygon
              key={room.ref}
              className="room-hit"
              points={pointList(room.points)}
              strokeWidth={1.2 * u}
              onPointerMove={(e) => {
                if (drag.current?.moved || tool !== "move") return;
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
                <polygon points={pointList(room.points)} fill="url(#hatch)" strokeWidth={1.1 * u} strokeDasharray={`${4 * u} ${4 * u}`} />
                <g transform={`translate(${cx},${cy}) scale(${u})`}><Plate text="not included" className="quiet" /></g>
              </g>
            );
          })}

          {/* Measured rooms: a tint, a fine outline, and the area on a plate below the room's own text. */}
          {shown.filter((e) => e.points).map((item) => {
            const [cx, mid] = centroid(item.points!);
            const ys = item.points!.map((q) => q[1]);
            const cy = mid + 0.24 * (Math.max(...ys) - Math.min(...ys));
            return (
              <g
                key={`room-${item.n}`}
                className={`mark room${hover === item.n || focus === item.n ? " hot" : ""}${excluded.has(item.n!) ? " off" : ""}`}
                data-mark={item.n}
                onPointerEnter={() => onHover(item.n!)}
                onPointerLeave={() => onHover(null)}
              >
                {item.holes?.length ? (
                  // A room around a shaft or another room: the tint stops at the holes, and each hole is
                  // outlined and hatched, so what was taken out of the area can be seen.
                  <>
                    <path className="tint" fillRule="evenodd"
                      d={[item.points!, ...item.holes].map((ring) => `M${ring.map((q) => q.join(",")).join("L")}Z`).join("")} />
                    {item.holes.map((ring, k) => (
                      <polygon key={k} className="hole" points={pointList(ring)} fill="url(#hatch)" strokeWidth={1.3 * u}
                        strokeDasharray={`${3 * u} ${2.4 * u}`} />
                    ))}
                  </>
                ) : (
                  <polygon className="tint" points={pointList(item.points!)} />
                )}
                <polygon className="edge" points={pointList(item.points!)} strokeWidth={1.6 * u} />
                <g transform={`translate(${cx},${cy}) scale(${u})`}>
                  <Plate text={item.area_m2 != null ? `${item.area_m2.toFixed(2)} m²` : String(item.n)} />
                </g>
              </g>
            );
          })}

          {/* One measured room: its overall width and depth, as on a dimensioned drawing. */}
          {single && pxPerM && (() => {
            const xs = single.points!.map((q) => q[0]), ys = single.points!.map((q) => q[1]);
            const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
            const gap = 18 * u, tick = 4 * u;
            return (
              <g className="dims">
                <path strokeWidth={1.1 * u}
                  d={`M${x0} ${y0 - gap}H${x1}M${x0} ${y0 - gap - tick}v${2 * tick}M${x1} ${y0 - gap - tick}v${2 * tick}` +
                     `M${x0 - gap} ${y0}V${y1}M${x0 - gap - tick} ${y0}h${2 * tick}M${x0 - gap - tick} ${y1}h${2 * tick}`} />
                <g transform={`translate(${(x0 + x1) / 2},${y0 - gap}) scale(${u})`}><Plate text={`${((x1 - x0) / pxPerM).toFixed(2)} m`} className="quiet" /></g>
                <g transform={`translate(${x0 - gap},${(y0 + y1) / 2}) scale(${u})`}><Plate text={`${((y1 - y0) / pxPerM).toFixed(2)} m`} className="quiet" /></g>
              </g>
            );
          })()}

          {/* The rooms that were searched for what the answer counts: outlined first, before any item is marked. */}
          {context.filter((item) => item.points).map((item, i) => (
            <polygon key={`searched-${i}`} className="searched" points={pointList(item.points!)} strokeWidth={1.4 * u}
              strokeDasharray={`${5 * u} ${3.5 * u}`} style={{ animationDelay: `${Math.min(i * 45, 700)}ms` }} />
          ))}

          {/* Text the engine read and did not count: the type line it checked against, labels it doubted. */}
          {context.filter((item) => item.bbox).map((item, i) => {
            const [x0, y0, x1, y1] = item.bbox!;
            const doubt = item.role === "not counted";
            const second = item.role === "marked by the vision model";  // the same thing, counted a second way
            // Measured on the sheet: a door opening left out of a length, and the scale bar it was read against.
            // Left out of a length (a door opening) or taken out of an area (a trench the program measured where it is drawn).
            const opening = item.role === "door opening, left out" || (item.role ?? "").startsWith("taken out");
            // A door measured in the pixels and taken as a standard door: the ruler, when no scale is printed.
            const measured = opening || item.role === "scale bar" || item.role === "door used as the ruler";
            // Boxed in one of two passes over the tiles: doubtful, drawn dashed, not counted.
            const once = item.role === "boxed in one pass only";
            // Looked at and not counted: a window that sits in the next room's wall.
            const leftOut = (item.role ?? "").startsWith("left out");
            const pad = opening ? 1.5 * u : 4 * u;
            if (once) {
              return (
                <g key={`ctx-${i}`} className="aside once">
                  <rect x={x0 - pad} y={y0 - pad} width={x1 - x0 + 2 * pad} height={y1 - y0 + 2 * pad} rx={3 * u}
                    strokeWidth={1.6 * u} strokeDasharray={`${4 * u} ${3 * u}`} />
                  <text x={x1 + pad} y={y0 - pad} fontSize={11 * u} textAnchor="middle">?</text>
                </g>
              );
            }
            return (
              <g key={`ctx-${i}`} className={`aside${doubt ? " doubt" : ""}${second ? " second" : ""}${opening ? " opening" : ""}${measured && !opening ? " ruler" : ""}${leftOut ? " leftout" : ""}`}>
                {leftOut && <title>{item.role}</title>}
                <rect x={x0 - pad} y={y0 - pad} width={x1 - x0 + 2 * pad} height={y1 - y0 + 2 * pad} rx={opening ? u : 3 * u}
                  strokeWidth={1.2 * u} strokeDasharray={measured ? undefined : `${4 * u} ${3 * u}`} />
                <g transform={`translate(${(x0 + x1) / 2},${y1 + 18 * u}) scale(${u})`}>
                  <Plate text={doubt ? "not counted" : leftOut ? "left out" : second ? "vision model" : measured ? item.label || item.role! : item.role ?? ""} className="quiet" />
                </g>
              </g>
            );
          })}

          {/* Counted items: a fine ring round each one and its number. */}
          {shown.filter((e) => !e.points).map((item) => {
            const box = boxOf(item)!;
            const isPoint = box[0] === box[2] && box[1] === box[3];
            const pad = isPoint ? 11 * u : 4 * u;
            const x0 = box[0] - pad, y0 = box[1] - pad, w = box[2] - box[0] + 2 * pad, h = box[3] - box[1] + 2 * pad;
            const hot = hover === item.n || focus === item.n;
            return (
              <g
                key={`count-${item.n}`}
                className={`mark count${hot ? " hot" : ""}${excluded.has(item.n!) ? " off" : ""}`}
                data-mark={item.n}
                onPointerEnter={() => onHover(item.n!)}
                onPointerLeave={() => onHover(null)}
              >
                <rect className="ring" x={x0} y={y0} width={w} height={h} rx={isPoint ? w / 2 : 3 * u} strokeWidth={(hot ? 2.2 : 1.5) * u} />
                <g transform={`translate(${x0},${y0}) scale(${u})`}>
                  <g className="dot">
                    <circle r={9} />
                    <text textAnchor="middle" y={3.4} style={{ fontSize: (item.n ?? 0) > 99 ? 8 : 10 }}>{item.n}</text>
                  </g>
                  {item.times && item.times > 1 && (  // the file has this outline more than once, here
                    <g className="times" transform="translate(15,0)">
                      <rect x={-8} y={-7} width={17} height={14} rx={7} />
                      <text textAnchor="middle" x={0.5} y={3.2}>×{item.times}</text>
                    </g>
                  )}
                </g>
              </g>
            );
          })}

          {/* The reader's own tape measure. */}
          {pxPerM && [...measures, ...(drawing ? [drawing] : [])].map((m, i) => {
            const dx = m.b[0] - m.a[0], dy = m.b[1] - m.a[1];
            const length = Math.hypot(dx, dy);
            if (length < 1e-6) return null;
            const nx = (-dy / length) * 5 * u, ny = (dx / length) * 5 * u;
            return (
              <g key={`tape-${i}`} className="tape">
                <path strokeWidth={1.3 * u}
                  d={`M${m.a[0]} ${m.a[1]}L${m.b[0]} ${m.b[1]}M${m.a[0] - nx} ${m.a[1] - ny}L${m.a[0] + nx} ${m.a[1] + ny}M${m.b[0] - nx} ${m.b[1] - ny}L${m.b[0] + nx} ${m.b[1] + ny}`} />
                <g transform={`translate(${(m.a[0] + m.b[0]) / 2},${(m.a[1] + m.b[1]) / 2}) scale(${u})`}>
                  <Plate text={`${(length / pxPerM).toFixed(2)} m`} className="ink" />
                </g>
              </g>
            );
          })}

          {/* The reading line that crosses the sheet while items are marked. */}
          {sweepAt !== null && (
            <g className="sweep" transform={`translate(${sweepAt},0)`}>
              <rect x={-70 * u} y={0} width={70 * u} height={height} fill="url(#sweep-trail)" />
              <line x1={0} y1={0} x2={0} y2={height} strokeWidth={1.2 * u} />
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

      {/* A loupe on the drawing, enlarged. */}
      {tool === "loupe" && lens && url && (
        <div
          className="loupe"
          style={{
            left: lens.x - LENS / 2, top: lens.y - LENS / 2, width: LENS, height: LENS,
            backgroundImage: `url(${sharp && sharp === sharpUrl ? sharpUrl : url})`,
            backgroundSize: `${width * t.k * 2.4}px ${height * t.k * 2.4}px`,
            backgroundPosition: `${LENS / 2 - (lens.x - t.x) * 2.4}px ${LENS / 2 - (lens.y - t.y) * 2.4}px`,
          }}
        />
      )}

      <div className="tools" onPointerDown={(e) => e.stopPropagation()} onPointerMove={(e) => e.stopPropagation()} onDoubleClick={(e) => e.stopPropagation()}>
        {pxPerM && (
          <button className={tool === "measure" ? "on" : ""} onClick={() => setTool(tool === "measure" ? "move" : "measure")} title="Drag on the sheet to measure a length">
            Measure
          </button>
        )}
        {measures.length > 0 && <button onClick={() => setMeasures([])}>Clear</button>}
        {detail.can_enhance && (
          <button className={sharpUrl && sharp === sharpUrl ? "on" : ""} onClick={enlarge} disabled={enlarged === "working" || (!!sharpUrl && sharp !== sharpUrl)}
            title="Show the scan enlarged with Cloudinary's AI super-resolution. The engine still reads the original.">
            {enlarged === "working" || (sharpUrl && sharp !== sharpUrl) ? "Enhancing…" : "Enhance"}
          </button>
        )}
        <button className={tool === "loupe" ? "on" : ""} onClick={() => { setTool(tool === "loupe" ? "move" : "loupe"); setLens(null); setTip(null); }} title="Enlarge the drawing under the pointer">
          Loupe
        </button>
        <span className="sep" />
        <button className="glyph" onClick={() => zoomBy(1.5)} aria-label="Zoom in">+</button>
        <button className="glyph" onClick={() => zoomBy(1 / 1.5)} aria-label="Zoom out">−</button>
        <button onClick={() => glide(fitTo(null))} aria-label="Fit the sheet">Fit</button>
      </div>
      {detail.view.note && <div className="viewer-note">{detail.view.note}</div>}
      {sharpUrl && sharp === sharpUrl && (
        <div className="viewer-note">Enlarged by AI for viewing. Fine detail can differ from the scan; answers are read from the original.</div>
      )}
    </div>
  );
}
