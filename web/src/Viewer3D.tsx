import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { CSS2DObject, CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import { api } from "./api";
import type { Theme } from "./Viewer2D";

export interface Highlight {
  id: string;
  n: number;
  tone: "count" | "area";
}

interface Props {
  meshUrl: string;
  groundUrl: string | null;  // where the plan image lies under the model, if it is lined up
  imageUrl: string | null;
  theme: Theme;
  highlights: Highlight[];
  visible: Set<number>;
  hover: number | null;
  focus: number | null;
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
  c?: [number, number, number];
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
  kind: string;
  paper: number;  // face colour on paper
  glass?: boolean;
  hidden?: boolean;
}

// How each kind of element is drawn when nothing is highlighted.
function styleOf(cls: string): Style {
  if (cls === "IfcSpace") return { kind: "space", paper: 0x2f6fe4, hidden: true };
  if (cls === "IfcSite") return { kind: "site", paper: 0xcfc9bb, hidden: true };
  if (cls === "IfcBuildingElementProxy") return { kind: "proxy", paper: 0xcfc9bb };
  if (cls.startsWith("IfcWall") || cls === "wall") return { kind: "wall", paper: 0xf3f0e8 };
  if (cls === "IfcCurtainWall" || cls === "IfcPlate" || cls === "IfcWindow" || cls === "window") return { kind: "glass", paper: 0x9cc3ee, glass: true };
  if (cls === "IfcDoor" || cls === "door") return { kind: "door", paper: 0xb98d5f };
  if (cls === "IfcSlab" || cls === "IfcRoof" || cls === "IfcFooting" || cls === "IfcCovering") return { kind: "slab", paper: 0xd8d3c7 };
  if (cls === "room") return { kind: "floor", paper: 0xe9e4d8 };
  if (cls.startsWith("IfcFurnishing") || cls === "IfcFurniture") return { kind: "furniture", paper: 0xc8c0b0 };
  if (cls === "IfcStair" || cls === "IfcStairFlight" || cls === "IfcRailing" || cls === "IfcRamp") return { kind: "stair", paper: 0xbcb5a6 };
  if (cls === "IfcColumn" || cls === "IfcBeam" || cls === "IfcMember") return { kind: "frame", paper: 0xa8a297 };
  if (cls === "toilet" || cls === "washbasin" || cls === "bathtub" || cls === "shower") return { kind: "fixture", paper: 0xa9bfd4 };
  return { kind: "other", paper: 0x86aaa3 };
}

const LOOK = {
  blueprint: {
    background: 0x0b2a63, face: 0x8fbcff, faceOpacity: 0.13, edge: 0xdcebff, edgeOpacity: 0.78, grid: 0x6fa0ea,
    count: new THREE.Color(0xff5a2e), area: new THREE.Color(0x38d6ff), off: new THREE.Color(0x7f93b8),
    countEdge: 0xffd0bd, areaEdge: 0xd6f7ff, glow: 2.3, sheet: "#12408f",
  },
  paper: {
    background: 0xe9e5db, face: 0xffffff, faceOpacity: 1, edge: 0x1f242b, edgeOpacity: 0.42, grid: 0xb9b3a4,
    count: new THREE.Color(0xff5a36), area: new THREE.Color(0x2f6fe4), off: new THREE.Color(0x9a968c),
    countEdge: 0x8f1d06, areaEdge: 0x173a8c, glow: 1, sheet: "#ffffff",
  },
} as const;

const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const easeOutBack = (t: number) => 1 + 2.2 * Math.pow(t - 1, 3) + 1.2 * Math.pow(t - 1, 2);

interface BaseEntry {
  mesh: THREE.Mesh;
  edges?: THREE.LineSegments;
  style: Style;
  storey: number | null;
}

interface Lit {
  mesh: THREE.Mesh;
  label?: CSS2DObject;
  tone: "count" | "area";
  born: number;
  storey: number | null;
  rest: number;  // its resting height (model z of its centre)
  box: THREE.Box3;  // in world coordinates, without any storey offset
}

interface Stage {
  parsed: Parsed;
  root: THREE.Group;
  base: BaseEntry[];
  lit: THREE.Group;
  items: Map<number, Lit>;
  tallies: THREE.Group;
  byId: Map<string, MeshElement>;
  modelBox: THREE.Box3;
  offsets: Map<number, { now: number; target: number }>;  // per storey, how far it is lifted
  ground: THREE.Mesh | null;
  groundStorey: number | null;
  fly(box: THREE.Box3, options?: { fromAbove?: boolean; low?: boolean; ms?: number }): void;
  redraw(): void;
  spin(on: boolean): void;
}

export function Viewer3D({ meshUrl, groundUrl, imageUrl, theme, highlights, visible, hover, focus, excluded, focusKey }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const stage = useRef<Stage | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | string>("loading");
  const [info, setInfo] = useState("");
  const [exploded, setExploded] = useState<boolean | null>(null);  // null: decided by the answer
  const [storeys, setStoreys] = useState(0);
  const look = LOOK[theme];

  // ---- scene, once per mesh and theme
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    let disposed = false;
    setStatus("loading");
    const blueprint = theme === "blueprint";

    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(el.clientWidth, el.clientHeight);
    el.appendChild(renderer.domElement);
    const labels = new CSS2DRenderer();
    labels.setSize(el.clientWidth, el.clientHeight);
    labels.domElement.className = "labels3d";
    el.appendChild(labels.domElement);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(look.background);
    const camera = new THREE.PerspectiveCamera(38, el.clientWidth / Math.max(el.clientHeight, 1), 0.1, 5000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.maxPolarAngle = Math.PI * 0.495;
    controls.autoRotateSpeed = 0.55;
    scene.add(new THREE.HemisphereLight(0xffffff, 0xb9b2a2, 1.15));
    const sun = new THREE.DirectionalLight(0xffffff, 1.5);
    sun.position.set(0.6, 1, 0.45);
    scene.add(sun);

    // The counted elements glow: only colours brighter than white reach the bloom pass.
    const composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    const bloom = new UnrealBloomPass(new THREE.Vector2(el.clientWidth, el.clientHeight), blueprint ? 0.85 : 0, 0.55, 1.0);
    if (blueprint) composer.addPass(bloom);
    composer.addPass(new OutputPass());

    let flight: { t0: number; ms: number; p0: THREE.Vector3; p1: THREE.Vector3; q0: THREE.Vector3; q1: THREE.Vector3; then?: () => void } | null = null;
    let rise: { t0: number; ms: number } | null = null;
    let frame = 0;
    let dirty = true;  // draw only when something moved: a still model costs nothing
    controls.addEventListener("change", () => { dirty = true; });
    controls.addEventListener("start", () => { controls.autoRotate = false; });

    const tick = (now: number) => {
      frame = requestAnimationFrame(tick);
      const s = stage.current;
      if (flight) {
        dirty = true;
        const p = Math.min(1, (now - flight.t0) / flight.ms);
        camera.position.lerpVectors(flight.p0, flight.p1, ease(p));
        controls.target.lerpVectors(flight.q0, flight.q1, ease(p));
        if (p >= 1) {
          const then = flight.then;
          flight = null;
          then?.();
        }
      }
      if (s) {
        if (rise) {  // walls grow out of the plan
          const p = Math.min(1, (now - rise.t0) / rise.ms);
          s.root.scale.z = Math.max(0.001, p <= 0 ? 0.001 : easeOutBack(p));
          dirty = true;
          if (p >= 1) { s.root.scale.z = 1; rise = null; }
        }
        let moving = false;
        for (const offset of s.offsets.values()) {  // storeys drop into place, or lift apart
          const gap = offset.target - offset.now;
          if (Math.abs(gap) > 0.004) { offset.now += gap * 0.11; moving = true; } else offset.now = offset.target;
        }
        if (moving || dirty) {
          for (const entry of s.base) {
            const lift = entry.storey != null ? s.offsets.get(entry.storey)?.now ?? 0 : 0;
            entry.mesh.position.z = lift;
            if (entry.edges) entry.edges.position.z = lift;
          }
          if (s.ground && s.groundStorey != null) s.ground.position.z = s.offsets.get(s.groundStorey)?.now ?? 0;
          for (const child of s.tallies.children) {
            child.position.z = (child.userData.rest as number) + (s.offsets.get(child.userData.storey as number)?.now ?? 0);
          }
        }
        for (const item of s.items.values()) {
          const lift = item.storey != null ? s.offsets.get(item.storey)?.now ?? 0 : 0;
          item.mesh.position.z = item.rest + lift;
          const age = (now - item.born) / 420;
          if (age < 1 && item.mesh.visible) {  // a newly marked element swells into place
            item.mesh.scale.setScalar(1 + 0.4 * Math.sin(Math.min(1, Math.max(0, age)) * Math.PI));
            dirty = true;
          } else if (item.mesh.scale.x !== 1) {
            item.mesh.scale.setScalar(1);
            dirty = true;
          }
        }
        if (moving) dirty = true;
      }
      if (controls.autoRotate) dirty = true;
      controls.update();
      if (!dirty) return;
      dirty = false;
      composer.render();
      labels.render(scene, camera);
    };
    frame = requestAnimationFrame(tick);

    const observer = new ResizeObserver(() => {
      const w = el.clientWidth, h = Math.max(el.clientHeight, 1);
      renderer.setSize(w, h);
      composer.setSize(w, h);
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
      const isPlan = parsed.header.kind === "plan";

      // One merged mesh per kind of element and storey: few draw calls, one vertex buffer,
      // and a storey can be lifted off as a whole.
      const groups = new Map<string, { style: Style; storey: number | null; ranges: MeshElement[]; count: number }>();
      const byId = new Map<string, MeshElement>();
      for (const element of parsed.header.elements) {
        byId.set(element.id, element);
        const style = styleOf(element.cls);
        const key = `${style.kind}|${element.storey ?? "-"}`;
        const group = groups.get(key) ?? { style, storey: element.storey, ranges: [], count: 0 };
        group.ranges.push(element);
        group.count += element.i[1];
        groups.set(key, group);
      }
      const base: BaseEntry[] = [];
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
        // On the blueprint the building is drawn in line: faint faces, bright edges.
        const material = blueprint
          ? new THREE.MeshBasicMaterial({
              color: look.face, transparent: true, opacity: withEdges ? look.faceOpacity : 0.3, depthWrite: false,
              side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
            })
          : new THREE.MeshStandardMaterial({
              color: group.style.paper, roughness: 0.92, metalness: 0, flatShading: true, side: THREE.DoubleSide,
              transparent: !!group.style.glass, opacity: group.style.glass ? 0.35 : 1, depthWrite: !group.style.glass,
              polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
            });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.visible = !group.style.hidden;
        root.add(mesh);
        const entry: BaseEntry = { mesh, style: group.style, storey: group.storey };
        if (withEdges && !group.style.hidden) {
          const edges = new THREE.LineSegments(
            new THREE.EdgesGeometry(geometry, 28),
            new THREE.LineBasicMaterial({ color: look.edge, transparent: true, opacity: look.edgeOpacity }),
          );
          root.add(edges);
          entry.edges = edges;
        }
        base.push(entry);
      }
      const lit = new THREE.Group();
      root.add(lit);
      const tallies = new THREE.Group();
      root.add(tallies);

      // Frame what is drawn: a hidden site or space volume must not push the camera away.
      const modelBox = new THREE.Box3();
      const corner = new THREE.Vector3();
      for (const element of parsed.header.elements) {
        if (styleOf(element.cls).hidden) continue;
        const end = (element.v[0] + element.v[1]) * 3;
        for (let k = element.v[0] * 3; k < end; k += 3) {
          modelBox.expandByPoint(corner.set(parsed.positions[k], parsed.positions[k + 2], -parsed.positions[k + 1]));
        }
      }
      if (modelBox.isEmpty()) {
        const b = parsed.header.bbox;
        modelBox.set(new THREE.Vector3(b[0], b[2], -b[4]), new THREE.Vector3(b[3], b[5], -b[1]));
      }

      // A drafting grid under the model.
      const span = modelBox.getSize(new THREE.Vector3());
      const reach = Math.max(span.x, span.z) * 2.6;
      const cells = Math.max(10, Math.min(80, Math.round(reach / (reach > 120 ? 5 : 1))));
      const grid = new THREE.GridHelper(reach, cells, look.grid, look.grid);
      const gridMaterial = grid.material as THREE.LineBasicMaterial;
      gridMaterial.transparent = true;
      gridMaterial.opacity = blueprint ? 0.2 : 0.35;
      grid.position.set((modelBox.min.x + modelBox.max.x) / 2, modelBox.min.y - 0.03, (modelBox.min.z + modelBox.max.z) / 2);
      scene.add(grid);

      const fly: Stage["fly"] = (box, options = {}) => {
        const centre = box.getCenter(new THREE.Vector3());
        const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1.5);
        // Fit the narrower of the two view angles, so a tall pane does not crop the model.
        const vertical = (camera.fov * Math.PI) / 360;
        const half = Math.min(vertical, Math.atan(Math.tan(vertical) * camera.aspect));
        const distance = (radius / Math.sin(half)) * 0.8;
        // Keep the side the viewer is on; come lower to look at one item.
        const side = new THREE.Vector3().subVectors(camera.position, controls.target).setY(0);
        if (side.lengthSq() < 1e-6 || options.fromAbove) side.set(0.62, 0, 0.78);
        side.normalize();
        const direction = side.multiplyScalar(Math.cos(options.low ? 0.5 : 0.8)).setY(Math.sin(options.low ? 0.5 : 0.8)).normalize();
        const end = centre.clone().addScaledVector(direction, distance);
        if (options.fromAbove) {  // start looking straight down, as on the plan, then come round
          camera.position.copy(centre).add(new THREE.Vector3(0, distance * 1.1, 0.001));
          controls.target.copy(centre);
        }
        camera.near = Math.max(0.05, distance / 500);
        camera.far = distance * 40;
        camera.updateProjectionMatrix();
        flight = { t0: performance.now() + (options.fromAbove ? 350 : 0), ms: options.ms ?? (options.fromAbove ? 2000 : 1100),
                   p0: camera.position.clone(), p1: end, q0: controls.target.clone(), q1: centre };
        dirty = true;
      };

      // Storeys of an IFC model drop into place one after another; a plan's walls rise from the sheet.
      const offsets: Stage["offsets"] = new Map();
      const order = parsed.header.storeys.map((storey, i) => ({ i, z: storey.z ?? 0 })).sort((a, b) => a.z - b.z);
      order.forEach(({ i }, rank) => offsets.set(i, { now: isPlan ? 0 : 14 + rank * 9, target: 0 }));
      if (isPlan) {
        root.scale.z = 0.001;
        rise = { t0: performance.now() + 900, ms: 1300 };
      }

      stage.current = {
        parsed, root, base, lit, items: new Map(), tallies, byId, modelBox, offsets, ground: null, groundStorey: null, fly,
        redraw: () => { dirty = true; },
        spin: (on) => { controls.autoRotate = on; dirty = true; },
      };
      fly(modelBox, { fromAbove: true });
      setStoreys(isPlan ? 0 : order.length);
      setInfo(`${parsed.header.elements.length.toLocaleString("en")} elements · ${parsed.header.triangles.toLocaleString("en")} triangles`);
      setStatus("ready");

      // The drawing itself as the floor the model stands on.
      if (groundUrl && imageUrl) {
        fetch(groundUrl).then((r) => (r.ok ? r.json() : null)).then((placed: { corners: number[][]; storey: number } | null) => {
          if (!placed || disposed || !stage.current) return;
          const image = new Image();
          image.onload = () => {
            if (disposed || !stage.current) return;
            const scale = Math.min(1, 2048 / Math.max(image.width, image.height));
            const canvas = document.createElement("canvas");
            canvas.width = Math.round(image.width * scale);
            canvas.height = Math.round(image.height * scale);
            const ctx = canvas.getContext("2d")!;
            ctx.fillStyle = look.sheet;
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            if (blueprint) {  // white lines on blue, like the sheet in the plan view
              ctx.globalCompositeOperation = "screen";
              ctx.filter = "invert(1) grayscale(1) contrast(1.5)";
            }
            ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
            const texture = new THREE.CanvasTexture(canvas);
            texture.colorSpace = THREE.SRGBColorSpace;
            texture.anisotropy = renderer.capabilities.getMaxAnisotropy();
            const geometry = new THREE.BufferGeometry();
            geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(placed.corners.flat()), 3));
            geometry.setAttribute("uv", new THREE.BufferAttribute(new Float32Array([0, 1, 1, 1, 1, 0, 0, 0]), 2));
            geometry.setIndex([0, 2, 1, 0, 3, 2]);
            const sheet = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ map: texture, side: THREE.DoubleSide, toneMapped: false }));
            sheet.renderOrder = -1;
            const s = stage.current;
            s.root.add(sheet);
            s.ground = sheet;
            s.groundStorey = isPlan ? null : placed.storey;
            if (isPlan) for (const entry of s.base) if (entry.style.kind === "floor") entry.mesh.visible = false;
            dirty = true;
          };
          image.src = imageUrl;
        }).catch(() => undefined);
      }
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
      composer.dispose();
      renderer.dispose();
      el.replaceChildren();
      stage.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meshUrl, theme, groundUrl, imageUrl]);

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
    for (const child of [...s.tallies.children]) s.tallies.remove(child);
    const blueprint = theme === "blueprint";
    const isPlan = s.parsed.header.kind === "plan";
    const active = highlights.length > 0;
    // When the whole answer sits on one storey, the storeys above are lifted off and that
    // storey stays readable; everything else steps back so the answer shows through.
    const storeyList = s.parsed.header.storeys;
    const perStorey = new Map<number, number>();
    for (const h of highlights) {
      const storey = s.byId.get(h.id)?.storey;
      if (storey != null) perStorey.set(storey, (perStorey.get(storey) ?? 0) + 1);
    }
    const only = perStorey.size === 1 ? [...perStorey.keys()][0] : null;
    const level = only != null ? storeyList[only]?.z ?? null : null;
    for (const entry of s.base) {
      const z = entry.storey != null ? storeyList[entry.storey]?.z ?? null : null;
      const above = active && level != null && z != null && z > level + 0.2;
      const same = active && only != null && entry.storey === only;
      const floorUnderSheet = isPlan && s.ground && entry.style.kind === "floor";
      entry.mesh.visible = !entry.style.hidden && !above && !floorUnderSheet;
      if (blueprint) {
        const material = entry.mesh.material as THREE.MeshBasicMaterial;
        material.opacity = !active ? LOOK.blueprint.faceOpacity : same ? 0.1 : 0.035;
      } else {
        const material = entry.mesh.material as THREE.MeshStandardMaterial;
        material.transparent = active || !!entry.style.glass;
        material.opacity = !active ? (entry.style.glass ? 0.35 : 1) : same ? (entry.style.glass ? 0.22 : 0.5) : 0.07;
        material.depthWrite = !active && !entry.style.glass;
        material.needsUpdate = true;
      }
      if (entry.edges) {
        entry.edges.visible = entry.mesh.visible;
        const full = blueprint ? LOOK.blueprint.edgeOpacity : LOOK.paper.edgeOpacity;
        (entry.edges.material as THREE.LineBasicMaterial).opacity = !active ? full : same ? full * 0.9 : full * 0.28;
      }
    }

    // An answer spread over several storeys: lift them apart and tally each one.
    const spread = perStorey.size > 1 && (exploded ?? true);
    const ranked = storeyList.map((storey, i) => ({ i, z: storey.z ?? 0 })).sort((a, b) => a.z - b.z);
    const height = s.modelBox.max.y - s.modelBox.min.y;
    const gap = spread || exploded ? Math.max(3, (height * 0.9) / Math.max(1, ranked.length)) : 0;
    ranked.forEach(({ i }, rank) => {
      const offset = s.offsets.get(i);
      if (offset) offset.target = rank * gap;
    });
    const lifted = s.modelBox.clone();
    lifted.max.y += gap * Math.max(0, ranked.length - 1);
    s.redraw();
    if (!active) {
      s.fly(lifted);
      return;
    }

    const lookNow = LOOK[theme];
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
      const colour = (h.tone === "area" ? lookNow.area : lookNow.count).clone().multiplyScalar(lookNow.glow);
      // Drawn after the faded building, so the answer is never dimmed by a wall in front of it.
      const material = new THREE.MeshBasicMaterial({
        color: colour, side: THREE.DoubleSide, transparent: true, opacity: h.tone === "area" ? 0.6 : 0.96,
        depthWrite: h.tone !== "area", toneMapped: false,
      });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.renderOrder = 2;
      const rest = centre.z + (isPlan && h.tone === "area" ? 0.035 : 0);  // a room plate sits just above the sheet
      mesh.position.set(centre.x, centre.y, rest);
      mesh.visible = false;
      const outline = new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 28),
        new THREE.LineBasicMaterial({ color: h.tone === "area" ? lookNow.areaEdge : lookNow.countEdge, transparent: true }));
      outline.renderOrder = 3;
      mesh.add(outline);
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
      const world = new THREE.Box3(
        new THREE.Vector3(extent.min.x, extent.min.z, -extent.max.y),
        new THREE.Vector3(extent.max.x, extent.max.z, -extent.min.y));
      s.items.set(h.n, { mesh, label, tone: h.tone, born: 0, storey: element.storey, rest, box: world });
      const lift = element.storey != null ? s.offsets.get(element.storey)?.target ?? 0 : 0;
      box.union(world.clone().translate(new THREE.Vector3(0, lift, 0)));
    }

    if (perStorey.size > 1) {  // how many on each storey, written beside it
      for (const [storey, count] of perStorey) {
        const tag = document.createElement("div");
        tag.className = "tally3d";
        const name = document.createElement("span");
        name.textContent = storeyList[storey]?.name || `Storey ${storey + 1}`;
        const figure = document.createElement("b");
        figure.textContent = String(count);
        tag.append(figure, name);
        const label = new CSS2DObject(tag);
        const rest = (storeyList[storey]?.z ?? 0) + 1.4;
        label.position.set(s.modelBox.max.x + 1.5, -s.modelBox.min.z + 0.5, rest);
        label.userData = { storey, rest };
        s.tallies.add(label);
      }
    }

    if (!box.isEmpty()) {
      const whole = lifted.getSize(new THREE.Vector3()).length();
      const part = box.getSize(new THREE.Vector3()).length();
      s.fly(part < whole * 0.55 ? box.expandByScalar(Math.max(2, part * 0.25)) : lifted);
    }
    s.spin(true);  // a slow turn shows depth; any touch stops it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusKey, status, meshUrl, theme, exploded]);

  // ---- marked one by one, in step with the plan and the counter
  useEffect(() => {
    const s = stage.current;
    if (!s) return;
    const now = performance.now();
    for (const [n, item] of s.items) {
      const show = visible.has(n);
      if (show && !item.mesh.visible) item.born = now;
      item.mesh.visible = show;
      if (item.label) item.label.visible = show;
    }
    s.redraw();
  }, [visible, focusKey, status, theme, exploded]);

  useEffect(() => {
    const s = stage.current;
    if (!s) return;
    const lookNow = LOOK[theme];
    for (const [n, item] of s.items) {
      const material = item.mesh.material as THREE.MeshBasicMaterial;
      const off = excluded.has(n);
      const hot = hover === n || focus === n;
      const colour = off ? lookNow.off : item.tone === "area" ? lookNow.area : lookNow.count;
      material.color.copy(colour).multiplyScalar(off ? 1 : hot ? lookNow.glow * 1.6 : lookNow.glow);
      item.label?.element.classList.toggle("hot", hot);
      item.label?.element.classList.toggle("off", off);
    }
    s.redraw();
  }, [hover, focus, excluded, focusKey, status, theme, exploded]);

  // ---- inspecting one item: come down to it
  useEffect(() => {
    const s = stage.current;
    if (!s || focus === null) return;
    const item = s.items.get(focus);
    if (!item) return;
    const lift = item.storey != null ? s.offsets.get(item.storey)?.target ?? 0 : 0;
    const box = item.box.clone().translate(new THREE.Vector3(0, lift, 0));
    s.spin(false);
    s.fly(box.expandByScalar(Math.max(2.2, box.getSize(new THREE.Vector3()).length() * 0.9)), { low: true, ms: 800 });
  }, [focus]);

  return (
    <div className={`viewer3d theme-${theme}`}>
      <div ref={host} className="stage3d" />
      {status === "loading" && <div className="viewer-wait">Raising the model…</div>}
      {status !== "loading" && status !== "ready" && <div className="viewer-wait">{status}</div>}
      {status === "ready" && <div className="viewer-info">{info} · drag to orbit, scroll to zoom</div>}
      {status === "ready" && storeys > 1 && (
        <div className="viewer-tools upper">
          <button className={`wide${exploded ? " on" : ""}`} onClick={() => setExploded(!(exploded ?? false))}>
            {exploded ? "Stack storeys" : "Lift storeys apart"}
          </button>
        </div>
      )}
    </div>
  );
}
