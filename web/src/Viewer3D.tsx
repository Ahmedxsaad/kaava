import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import { api } from "./api";

export interface Highlight {
  id: string;
  n: number;
  tone: "count" | "area";
}

interface Props {
  meshUrl: string;
  highlights: Highlight[];
  revealed: number;
  hover: number | null;
  excluded: Set<number>;
  focusKey: string;
}

interface MeshElement {
  id: string;
  cls: string;
  name: string;
  storey: number | null;
  v: [number, number];
  i: [number, number];
}

interface Parsed {
  header: {
    kind: "ifc" | "plan";
    elements: MeshElement[];
    storeys: { name: string; z: number | null }[];
    bbox: number[];
    triangles: number;
    vertices: number;
  };
  positions: Float32Array;
  indices: Uint32Array;
}

function parse(buffer: ArrayBuffer): Parsed {
  const view = new DataView(buffer);
  const magic = String.fromCharCode(view.getUint8(0), view.getUint8(1), view.getUint8(2), view.getUint8(3));
  if (magic !== "BPM1") throw new Error("not a mesh file");
  const headLength = view.getUint32(4, true);
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 8, headLength)));
  const start = 8 + headLength;
  const positions = new Float32Array(buffer, start, header.vertices * 3);
  const indices = new Uint32Array(buffer, start + header.vertices * 12, header.triangles * 3);
  return { header, positions, indices };
}

interface Style {
  color: number;
  opacity?: number;
  hidden?: boolean;
}

// How each kind of element is drawn when nothing is highlighted.
function styleOf(cls: string): [string, Style] {
  if (cls === "IfcSpace") return ["space", { color: 0x2f6fe4, hidden: true }];
  if (cls === "IfcSite" || cls === "IfcBuildingElementProxy") return ["site", { color: 0xcfc9bb, hidden: cls === "IfcSite" }];
  if (cls.startsWith("IfcWall") || cls === "wall") return ["wall", { color: 0xf3f0e8 }];
  if (cls === "IfcCurtainWall" || cls === "IfcPlate") return ["glass", { color: 0x9cc3ee, opacity: 0.35 }];
  if (cls === "IfcWindow" || cls === "window") return ["glass", { color: 0x9cc3ee, opacity: 0.35 }];
  if (cls === "IfcDoor" || cls === "door") return ["door", { color: 0xb98d5f }];
  if (cls === "IfcSlab" || cls === "IfcRoof" || cls === "IfcFooting" || cls === "IfcCovering") return ["slab", { color: 0xd8d3c7 }];
  if (cls === "room") return ["floor", { color: 0xe9e4d8 }];
  if (cls.startsWith("IfcFurnishing") || cls === "IfcFurniture") return ["furniture", { color: 0xc8c0b0 }];
  if (cls === "IfcStair" || cls === "IfcStairFlight" || cls === "IfcRailing" || cls === "IfcRamp") return ["stair", { color: 0xbcb5a6 }];
  if (cls === "IfcColumn" || cls === "IfcBeam" || cls === "IfcMember") return ["frame", { color: 0xa8a297 }];
  if (cls === "toilet" || cls === "washbasin" || cls === "bathtub" || cls === "shower") return ["fixture", { color: 0xa9bfd4 }];
  return ["other", { color: 0x86aaa3 }];
}

const COUNT_COLOUR = 0xff5a36;
const AREA_COLOUR = 0x2f6fe4;
const MUTED_COLOUR = 0x9a968c;

function ease(t: number): number {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

export function Viewer3D({ meshUrl, highlights, revealed, hover, excluded, focusKey }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const stage = useRef<{
    parsed: Parsed;
    root: THREE.Group;
    base: { mesh: THREE.Mesh; style: Style; storey: number | null; edges?: THREE.LineSegments }[];
    lit: THREE.Group;
    items: Map<number, { mesh: THREE.Mesh; label?: CSS2DObject; tone: "count" | "area"; born: number }>;
    camera: THREE.PerspectiveCamera;
    controls: OrbitControls;
    fly(box: THREE.Box3, fromAbove?: boolean): void;
    byId: Map<string, MeshElement>;
    modelBox: THREE.Box3;
    redraw(): void;
  } | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | string>("loading");
  const [info, setInfo] = useState("");

  // ---- scene, once per mesh
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    let disposed = false;
    setStatus("loading");

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(el.clientWidth, el.clientHeight);
    el.appendChild(renderer.domElement);
    const labels = new CSS2DRenderer();
    labels.setSize(el.clientWidth, el.clientHeight);
    labels.domElement.className = "labels3d";
    el.appendChild(labels.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(38, el.clientWidth / Math.max(el.clientHeight, 1), 0.1, 5000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.maxPolarAngle = Math.PI * 0.495;
    scene.add(new THREE.HemisphereLight(0xffffff, 0xb9b2a2, 1.15));
    const sun = new THREE.DirectionalLight(0xffffff, 1.5);
    sun.position.set(0.6, 1, 0.45);
    scene.add(sun);
    const fill = new THREE.DirectionalLight(0xffffff, 0.5);
    fill.position.set(-0.7, 0.4, -0.6);
    scene.add(fill);

    let flight: { t0: number; ms: number; p0: THREE.Vector3; p1: THREE.Vector3; q0: THREE.Vector3; q1: THREE.Vector3 } | null = null;
    let frame = 0;
    let dirty = true;  // draw only when something moved: a still model costs nothing
    controls.addEventListener("change", () => { dirty = true; });
    const tick = (now: number) => {
      frame = requestAnimationFrame(tick);
      if (flight) {
        dirty = true;
        const u = Math.min(1, (now - flight.t0) / flight.ms);
        const e = ease(u);
        camera.position.lerpVectors(flight.p0, flight.p1, e);
        controls.target.lerpVectors(flight.q0, flight.q1, e);
        if (u >= 1) flight = null;
      }
      const s = stage.current;
      if (s) {
        for (const item of s.items.values()) {  // newly lit elements swell into place
          const age = (now - item.born) / 380;
          if (age < 1 && item.mesh.visible) {
            const k = 1 + 0.35 * Math.sin(Math.min(1, Math.max(0, age)) * Math.PI);
            item.mesh.scale.setScalar(k);
            dirty = true;
          } else if (item.mesh.scale.x !== 1) {
            item.mesh.scale.setScalar(1);
            dirty = true;
          }
        }
      }
      controls.update();
      if (!dirty) return;
      dirty = false;
      renderer.render(scene, camera);
      labels.render(scene, camera);
    };
    frame = requestAnimationFrame(tick);

    const observer = new ResizeObserver(() => {
      const w = el.clientWidth, h = Math.max(el.clientHeight, 1);
      renderer.setSize(w, h);
      labels.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      dirty = true;
    });
    observer.observe(el);

    api.mesh(meshUrl).then((buffer) => {
      if (disposed) return;
      const parsed = parse(buffer);
      const root = new THREE.Group();
      root.rotation.x = -Math.PI / 2;  // the model's z axis points up
      scene.add(root);
      const shared = new THREE.BufferAttribute(parsed.positions, 3);

      // One merged mesh per kind of element: few draw calls, one vertex buffer.
      // Split by storey too, so the storeys above an answer can be lifted off.
      const groups = new Map<string, { style: Style; storey: number | null; ranges: MeshElement[]; count: number }>();
      const byId = new Map<string, MeshElement>();
      for (const element of parsed.header.elements) {
        byId.set(element.id, element);
        const [kind, style] = styleOf(element.cls);
        const key = `${kind}|${element.storey ?? "-"}`;
        const group = groups.get(key) ?? { style, storey: element.storey, ranges: [], count: 0 };
        group.ranges.push(element);
        group.count += element.i[1];
        groups.set(key, group);
      }
      const base: { mesh: THREE.Mesh; style: Style; storey: number | null; edges?: THREE.LineSegments }[] = [];
      const withEdges = parsed.header.triangles < 450_000;
      for (const [, group] of groups) {
        const index = new Uint32Array(group.count);
        let at = 0;
        for (const element of group.ranges) {
          index.set(parsed.indices.subarray(element.i[0], element.i[0] + element.i[1]), at);
          at += element.i[1];
        }
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute("position", shared);
        geometry.setIndex(new THREE.BufferAttribute(index, 1));
        const glass = group.style.opacity !== undefined;
        const material = new THREE.MeshStandardMaterial({
          color: group.style.color, roughness: 0.92, metalness: 0, flatShading: true, side: THREE.DoubleSide,
          transparent: glass, opacity: group.style.opacity ?? 1, depthWrite: !glass,
          polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
        });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.visible = !group.style.hidden;
        root.add(mesh);
        const entry: { mesh: THREE.Mesh; style: Style; storey: number | null; edges?: THREE.LineSegments } =
          { mesh, style: group.style, storey: group.storey };
        if (withEdges && !group.style.hidden) {
          const edges = new THREE.LineSegments(
            new THREE.EdgesGeometry(geometry, 28),
            new THREE.LineBasicMaterial({ color: 0x1f242b, transparent: true, opacity: 0.42 }),
          );
          root.add(edges);
          entry.edges = edges;
        }
        base.push(entry);
      }
      const lit = new THREE.Group();
      root.add(lit);

      // Frame what is drawn: a hidden site or space volume must not push the camera away.
      const modelBox = new THREE.Box3();
      const corner = new THREE.Vector3();
      for (const element of parsed.header.elements) {
        if (styleOf(element.cls)[1].hidden) continue;
        const end = (element.v[0] + element.v[1]) * 3;
        for (let k = element.v[0] * 3; k < end; k += 3) {
          modelBox.expandByPoint(corner.set(parsed.positions[k], parsed.positions[k + 2], -parsed.positions[k + 1]));
        }
      }
      if (modelBox.isEmpty()) {
        const b = parsed.header.bbox;
        modelBox.set(new THREE.Vector3(b[0], b[2], -b[4]), new THREE.Vector3(b[3], b[5], -b[1]));
      }
      const fly = (box: THREE.Box3, fromAbove = false) => {
        const centre = box.getCenter(new THREE.Vector3());
        const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1.5);
        const distance = (radius / Math.sin((camera.fov * Math.PI) / 360)) * 0.8;
        const direction = new THREE.Vector3(0.62, 0.72, 0.78).normalize();
        const end = centre.clone().addScaledVector(direction, distance);
        if (fromAbove) {  // start looking straight down at the plan, then come round
          camera.position.copy(centre).add(new THREE.Vector3(0.001, distance * 1.15, 0.001));
          controls.target.copy(centre);
        }
        camera.near = Math.max(0.05, distance / 500);
        camera.far = distance * 30;
        camera.updateProjectionMatrix();
        flight = { t0: performance.now(), ms: fromAbove ? 1700 : 1100, p0: camera.position.clone(), p1: end,
                   q0: controls.target.clone(), q1: centre };
      };
      stage.current = { parsed, root, base, lit, items: new Map(), camera, controls, fly, byId, modelBox,
                        redraw: () => { dirty = true; } };
      fly(modelBox, true);
      setInfo(`${parsed.header.elements.length.toLocaleString("en")} elements · ${parsed.header.triangles.toLocaleString("en")} triangles`);
      setStatus("ready");
    }).catch((error: Error) => !disposed && setStatus(error.message));

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      scene.traverse((object) => {
        const mesh = object as THREE.Mesh;
        mesh.geometry?.dispose?.();
        const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
        if (Array.isArray(material)) material.forEach((m) => m.dispose());
        else material?.dispose?.();
      });
      renderer.dispose();
      el.replaceChildren();
      stage.current = null;
    };
  }, [meshUrl]);

  // ---- the answer's elements: built when the answer (or the mesh) changes
  useEffect(() => {
    const s = stage.current;
    if (!s || status !== "ready") return;
    for (const item of s.items.values()) {
      item.mesh.geometry.dispose();
      (item.mesh.material as THREE.Material).dispose();
      if (item.label) item.mesh.remove(item.label);
    }
    s.lit.clear();
    s.items.clear();
    const active = highlights.length > 0;
    // When the whole answer sits on one storey, the storeys above are lifted off and that
    // storey stays readable; everything else steps back so the answer shows through.
    const storeys = s.parsed.header.storeys;
    const touched = new Set(highlights.map((h) => s.byId.get(h.id)?.storey).filter((v) => v !== undefined));
    const only = touched.size === 1 ? [...touched][0] : null;
    const level = only != null ? storeys[only]?.z ?? null : null;
    for (const entry of s.base) {
      const material = entry.mesh.material as THREE.MeshStandardMaterial;
      const glass = entry.style.opacity !== undefined;
      const z = entry.storey != null ? storeys[entry.storey]?.z ?? null : null;
      const above = active && level != null && z != null && z > level + 0.2;
      const same = active && only != null && entry.storey === only;
      const opacity = !active ? entry.style.opacity ?? 1 : same ? (glass ? 0.22 : 0.5) : 0.07;
      entry.mesh.visible = !entry.style.hidden && !above;
      material.transparent = active || glass;
      material.opacity = opacity;
      material.depthWrite = !active && !glass;
      material.needsUpdate = true;
      if (entry.edges) {
        entry.edges.visible = entry.mesh.visible;
        (entry.edges.material as THREE.LineBasicMaterial).opacity = !active ? 0.42 : same ? 0.5 : 0.1;
      }
    }
    s.redraw();
    if (!active) {
      s.fly(s.modelBox);
      return;
    }
    const box = new THREE.Box3();
    const withLabels = highlights.length <= 160;
    for (const h of highlights) {
      const element = s.byId.get(h.id);
      if (!element) continue;
      const [v0, vn] = element.v;
      const positions = s.parsed.positions.slice(v0 * 3, (v0 + vn) * 3);
      const index = new Uint32Array(element.i[1]);
      for (let k = 0; k < index.length; k++) index[k] = s.parsed.indices[element.i[0] + k] - v0;
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      geometry.setIndex(new THREE.BufferAttribute(index, 1));
      geometry.computeBoundingBox();
      const extent = geometry.boundingBox!.clone();  // in model coordinates, before centring
      const centre = extent.getCenter(new THREE.Vector3());
      geometry.translate(-centre.x, -centre.y, -centre.z);  // so it can swell around its own centre
      const colour = h.tone === "area" ? AREA_COLOUR : COUNT_COLOUR;
      const material = new THREE.MeshStandardMaterial({
        color: colour, emissive: colour, emissiveIntensity: 0.35, roughness: 0.6, flatShading: true,
        side: THREE.DoubleSide, transparent: h.tone === "area", opacity: h.tone === "area" ? 0.62 : 1,
        depthWrite: h.tone !== "area",
      });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.copy(centre);
      mesh.visible = false;
      mesh.add(new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 28),
        new THREE.LineBasicMaterial({ color: h.tone === "area" ? 0x173a8c : 0x8f1d06 })));
      let label: CSS2DObject | undefined;
      if (withLabels) {
        const tag = document.createElement("div");
        tag.className = `tag3d ${h.tone}`;
        tag.textContent = String(h.n);
        label = new CSS2DObject(tag);
        label.position.set(0, 0, (extent.max.z - extent.min.z) / 2);
        mesh.add(label);
      }
      s.lit.add(mesh);
      s.items.set(h.n, { mesh, label, tone: h.tone, born: 0 });
      box.union(new THREE.Box3(
        new THREE.Vector3(extent.min.x, extent.min.z, -extent.max.y),
        new THREE.Vector3(extent.max.x, extent.max.z, -extent.min.y)));
    }
    if (!box.isEmpty()) {
      const whole = s.modelBox.getSize(new THREE.Vector3()).length();
      const part = box.getSize(new THREE.Vector3()).length();
      s.fly(part < whole * 0.55 ? box.expandByScalar(Math.max(2, part * 0.25)) : s.modelBox);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusKey, status, meshUrl]);

  // ---- reveal one by one, in step with the plan and the counter
  useEffect(() => {
    const s = stage.current;
    if (!s) return;
    const now = performance.now();
    for (const [n, item] of s.items) {
      const show = n <= revealed;
      if (show && !item.mesh.visible) item.born = now;
      item.mesh.visible = show;
      if (item.label) item.label.visible = show;
    }
    s.redraw();
  }, [revealed, focusKey, status]);

  useEffect(() => {
    const s = stage.current;
    if (!s) return;
    for (const [n, item] of s.items) {
      const material = item.mesh.material as THREE.MeshStandardMaterial;
      const off = excluded.has(n);
      const colour = off ? MUTED_COLOUR : item.tone === "area" ? AREA_COLOUR : COUNT_COLOUR;
      material.color.setHex(colour);
      material.emissive.setHex(colour);
      material.emissiveIntensity = off ? 0 : hover === n ? 0.9 : 0.35;
      item.label?.element.classList.toggle("hot", hover === n);
      item.label?.element.classList.toggle("off", off);
    }
    s.redraw();
  }, [hover, excluded, focusKey, status]);

  return (
    <div className="viewer3d">
      <div ref={host} className="stage3d" />
      {status === "loading" && <div className="viewer-wait">Building the 3D model…</div>}
      {status !== "loading" && status !== "ready" && <div className="viewer-wait">{status}</div>}
      {status === "ready" && <div className="viewer-info">{info} · drag to orbit, scroll to zoom</div>}
    </div>
  );
}
