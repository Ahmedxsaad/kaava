import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { api } from "./api";

// The page can direct the model itself (a story told by scrolling): how far the walls have risen,
// and where the camera stands. The model then follows these, softly, instead of moving on its own.
export interface Direction {
  rise: number;  // 0: flat on the sheet, 1: standing
  turn: number;  // where the camera stands around the model, radians
  tilt: number;  // how high it stands: about 1.5 looks straight down, 0.6 is a three-quarter view
  back: number;  // its distance, as a multiple of the distance that just fits the model
  sway?: number;  // while waiting: the walls breathe and the view drifts by about this much, on their own
  shift?: number;  // the model stands this share of the width to the right of centre
  drop?: number;  // and this share of the height below it
}

export interface Highlight {
  id: string;
  n: number;
  tone: "count" | "area";
}

interface Props {
  meshUrl: string;
  groundUrl: string | null;  // where the plan image lies under the model, if it is lined up
  imageUrl: string | null;
  highlights: Highlight[];
  visible: Set<number>;
  hover: number | null;
  focus: number | null;
  excluded: Set<number>;
  focusKey: string;
  theme?: "paper" | "night";  // night: a lit white model on a dark stage, for the landing page
  bare?: boolean;  // no captions or tools, no zoom: the stage alone, as a backdrop
  shift?: number;  // move the model sideways by this share of the width (text stands beside it)
  direct?: Direction;
  tags?: boolean;  // false hides the number tags (when the model is small on screen they only crowd it)
  onReady?(): void;
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
  if (cls === "IfcRoof") return { kind: "roof", paper: 0xd8d3c7 };
  if (cls === "IfcSlab" || cls === "IfcFooting" || cls === "IfcCovering") return { kind: "slab", paper: 0xd8d3c7 };
  if (cls === "room") return { kind: "floor", paper: 0xe9e4d8 };
  if (cls.startsWith("IfcFurnishing") || cls === "IfcFurniture") return { kind: "furniture", paper: 0xc8c0b0 };
  if (cls === "IfcStair" || cls === "IfcStairFlight" || cls === "IfcRailing" || cls === "IfcRamp") return { kind: "stair", paper: 0xbcb5a6 };
  if (cls === "IfcColumn" || cls === "IfcBeam" || cls === "IfcMember") return { kind: "frame", paper: 0xa8a297 };
  if (cls === "toilet" || cls === "washbasin" || cls === "bathtub" || cls === "shower") return { kind: "fixture", paper: 0xa9bfd4 };
  return { kind: "other", paper: 0x86aaa3 };
}

// A white architectural model: sunlight and soft shadows, fine dark edges, one warm accent for the answer.
const PAPER = {
  edge: 0x2a302b, edgeOpacity: 0.26, table: 0xf4f8f1, rim: 0xcfdccb,
  count: new THREE.Color(0xc46103), area: new THREE.Color(0xe8843a), off: new THREE.Color(0xa9b0a9),
  countEdge: 0x7a3a00, areaEdge: 0xa65100, sheet: "#ffffff",
  sky: 0xffffff, bounce: 0xe9ebe9, ambient: 2.35, sun: 2.1, fill: 0.5, shadow: 0.26,
};
// The same model at night: one key light on a dark table, so the paper and the walls stand out.
const NIGHT: typeof PAPER = {
  ...PAPER, edge: 0x0c1f0e, edgeOpacity: 0.34, table: 0x174a1e, rim: 0x2c6a34,
  count: new THREE.Color(0xf07a14), area: new THREE.Color(0xf0903f), sheet: "#f4f6f1",
  sky: 0xffffff, bounce: 0x8fa894, ambient: 2.35, sun: 2.9, fill: 0.5, shadow: 0.46,
};
// A painted model: white walls, and one clear colour for each kind of thing.
const FACE: Record<string, number> = {
  wall: 0xfffdf8, slab: 0xf0e8da, floor: 0xffffff, glass: 0x7cc4ff, door: 0xdfa368, furniture: 0xf4c25a,
  stair: 0xe58a6b, frame: 0x7f93b3, fixture: 0x4fc3e0, proxy: 0xe0d8cc, other: 0xb9d9c6, space: 0xe8843a, site: 0xcfe3bf,
  roof: 0xe07f63,
};

// Rooms are tinted by what they are for, read from the name printed in them (German, French, Italian, English).
const ROOM_TINTS: [RegExp, number][] = [
  [/wohn|séjour|sejour|soggiorno|essen|repas|pranzo|living|dining/i, 0xffd45e],            // living and dining: sun yellow
  [/küche|kueche|cuisine|cucina|kitchen/i, 0xff9a76],                                         // kitchen: coral
  [/bad|bain|bagno|wc|dusch|douche|wasch|buanderie|lavanderia|bath|toilet/i, 0x62d2ea],       // bath: aqua
  [/balkon|balcon|loggia|terrass|giardino|arkade|balcony|garden/i, 0x86d98f],                 // outdoors: green
  [/korridor|couloir|corridoio|halle|entr|flur|gang|hall|corridor/i, 0xf6e2b8],               // circulation: sand
  [/treppe|escalier|scala|lift|ascens|aufzug|stair|elevator/i, 0xc8ccd6],                     // stairs and lifts: grey
  [/reduit|réduit|ripostiglio|abstell|keller|storage|velo|kinderwagen/i, 0xdcc7b4],           // storage: taupe
  [/zimmer|pièce|piece|stanza|chambre|camera|schlaf|raum|room|büro|bureau|office/i, 0xb7a8f7], // rooms and bedrooms: lavender
];
const ROOM_PLAIN = 0xf3ede2;

function tintOf(name: string): number {
  for (const [test, colour] of ROOM_TINTS) if (test.test(name)) return colour;
  return ROOM_PLAIN;
}

const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const easeOutBack = (t: number) => 1 + 2.2 * Math.pow(t - 1, 3) + 1.2 * Math.pow(t - 1, 2);

interface BaseEntry {
  mesh: THREE.Mesh;
  edges?: THREE.LineSegments;
  style: Style;
  storey: number | null;
  raise?: number;  // held this far above its place (a plan's room tints lie on the sheet)
}

// A plan draws a door as the opening it fills: a block as thick as the wall. In the model a door
// is a thin leaf standing in that opening, hung at one end. This finds the opening's line from the
// block's own outline: its middle, the way it runs, its width and its height.
interface Leaf { x: number; y: number; angle: number; width: number; z0: number; z1: number }
const LEAF_THICK = 0.05;

function leafOf(positions: Float32Array): Leaf {
  const n = positions.length / 3;
  let mx = 0, my = 0, z0 = Infinity, z1 = -Infinity;
  for (let k = 0; k < positions.length; k += 3) {
    mx += positions[k]; my += positions[k + 1];
    z0 = Math.min(z0, positions[k + 2]); z1 = Math.max(z1, positions[k + 2]);
  }
  mx /= n; my /= n;
  let sxx = 0, sxy = 0, syy = 0;
  for (let k = 0; k < positions.length; k += 3) {
    const dx = positions[k] - mx, dy = positions[k + 1] - my;
    sxx += dx * dx; sxy += dx * dy; syy += dy * dy;
  }
  const angle = 0.5 * Math.atan2(2 * sxy, sxx - syy);
  const ax = Math.cos(angle), ay = Math.sin(angle);
  let low = Infinity, high = -Infinity;
  for (let k = 0; k < positions.length; k += 3) {
    const along = (positions[k] - mx) * ax + (positions[k + 1] - my) * ay;
    low = Math.min(low, along); high = Math.max(high, along);
  }
  const mid = (low + high) / 2;
  return { x: mx + mid * ax, y: my + mid * ay, angle, width: Math.max(0.2, high - low), z0, z1 };
}

// The leaf itself, hung at the origin and running along +x, standing from z0 to z1.
function leafGeometry(leaf: Leaf): THREE.BufferGeometry {
  const width = leaf.width * 0.94;
  const geometry = new THREE.BoxGeometry(width, LEAF_THICK, leaf.z1 - leaf.z0);
  geometry.translate(width / 2, 0, (leaf.z0 + leaf.z1) / 2);
  return geometry;
}

interface Lit {
  node: THREE.Group;  // what is placed, lifted and shown; a door turns about it
  swing: number;  // how far a door opens when it is counted (radians), 0 for anything else
  closed: number;  // the way a door's leaf runs when it is shut (radians)
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

export function Viewer3D({ meshUrl, groundUrl, imageUrl, highlights, visible, hover, focus, excluded, focusKey, theme = "paper", bare, shift = 0, direct, tags = true, onReady }: Props) {
  const directed = useRef(direct);
  directed.current = direct;
  const host = useRef<HTMLDivElement>(null);
  const stage = useRef<Stage | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | string>("loading");
  const [info, setInfo] = useState("");
  const [exploded, setExploded] = useState<boolean | null>(null);  // null: decided by the answer
  const [storeys, setStoreys] = useState(0);
  const look = theme === "night" ? NIGHT : PAPER;

  // ---- scene, once per mesh
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    let disposed = false;
    setStatus("loading");

    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true, alpha: true });  // the page shows behind
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
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
    controls.autoRotateSpeed = bare ? 0.38 : 0.55;
    controls.enableZoom = !bare;  // a backdrop must not take the page's scroll
    controls.enablePan = !bare;
    controls.enabled = !directed.current;
    // Where the directed camera is now. It starts far off and to one side, so the model glides in on arrival.
    const now3 = { rise: directed.current?.rise ?? 1, turn: (directed.current?.turn ?? 0) - 0.55, tilt: directed.current?.tilt ?? 0.8, back: (directed.current?.back ?? 1) * 1.7,
                   shift: directed.current?.shift ?? 0, drop: directed.current?.drop ?? 0 };
    const middle3 = new THREE.Vector3(), size3 = new THREE.Vector3();
    const frameView = (w: number, h: number) => {
      camera.aspect = w / h;
      if (shift) camera.setViewOffset(w, h, -shift * w, 0, w, h);
      camera.updateProjectionMatrix();
    };
    frameView(el.clientWidth, Math.max(el.clientHeight, 1));
    scene.add(new THREE.HemisphereLight(look.sky, look.bounce, look.ambient));
    const sun = new THREE.DirectionalLight(0xffffff, look.sun);  // the one light that casts shadows
    sun.position.set(0.55, 1, 0.4);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.bias = -0.0012;
    sun.shadow.normalBias = 0.14;
    scene.add(sun, sun.target);
    const fill = new THREE.DirectionalLight(0xffffff, look.fill);
    fill.position.set(-0.6, 0.5, -0.7);
    scene.add(fill);

    let flight: { t0: number; ms: number; p0: THREE.Vector3; p1: THREE.Vector3; q0: THREE.Vector3; q1: THREE.Vector3; then?: () => void } | null = null;
    let rise: { t0: number; ms: number } | null = null;
    let frame = 0;
    let dirty = true;  // draw only when something moved: a still model costs nothing
    controls.addEventListener("change", () => { dirty = true; });
    controls.addEventListener("start", () => { controls.autoRotate = false; });

    const tick = (now: number) => {
      frame = requestAnimationFrame(tick);
      const s = stage.current;
      const want = directed.current;
      if (want && s) {  // the page directs: follow it softly
        let moved = false;
        const sway = want.sway ?? 0;
        const aim = sway > 0.001
          ? { ...want, rise: want.rise + sway * (0.55 + 0.45 * Math.sin(now / 1100)), turn: want.turn + sway * 0.9 * Math.sin(now / 2300),
              tilt: want.tilt - sway * 1.6 * (0.5 + 0.5 * Math.sin(now / 1900)) }
          : want;
        if (sway > 0.001) moved = true;  // alive while it waits
        for (const key of ["rise", "turn", "tilt", "back", "shift", "drop"] as const) {
          const gap = (aim[key] ?? 0) - now3[key];
          if (Math.abs(gap) > 0.0004) { now3[key] += gap * 0.13; moved = true; } else now3[key] = aim[key] ?? 0;
        }
        if (moved || dirty) {
          s.root.scale.z = Math.max(0.001, now3.rise);
          s.modelBox.getCenter(middle3);
          const radius = Math.max(s.modelBox.getSize(size3).length() / 2, 1.5);
          const vertical = (camera.fov * Math.PI) / 360;
          const half = Math.min(vertical, Math.atan(Math.tan(vertical) * camera.aspect));
          const distance = (radius / Math.sin(half)) * now3.back;
          camera.position.set(
            middle3.x + distance * Math.cos(now3.tilt) * Math.sin(now3.turn),
            distance * Math.sin(now3.tilt),
            middle3.z + distance * Math.cos(now3.tilt) * Math.cos(now3.turn));
          controls.target.set(middle3.x, 0, middle3.z);
          camera.near = Math.max(0.05, distance / 500);
          camera.far = distance * 40;
          const w = el.clientWidth, h = Math.max(el.clientHeight, 1);
          camera.setViewOffset(w, h, -now3.shift * w, -now3.drop * h, w, h);
          camera.updateProjectionMatrix();
          camera.lookAt(controls.target);
          // The sun keeps to the viewer's side, a little to the left: the faces in view stay lit and
          // the shadows turn with the scroll, like a day passing.
          const reach = radius * 2.6;
          sun.position.set(middle3.x + reach * 0.62 * Math.sin(now3.turn - 0.75), reach * 0.78, middle3.z + reach * 0.62 * Math.cos(now3.turn - 0.75));
          dirty = true;
        }
      }
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
            const lift = (entry.storey != null ? s.offsets.get(entry.storey)?.now ?? 0 : 0) + (entry.raise ?? 0);
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
          item.node.position.z = item.rest + lift;
          if (item.swing && item.node.visible) {  // a counted door swings open and stays open
            const turn = Math.min(1, Math.max(0, (now - item.born) / 760));
            const angle = item.closed + item.swing * (turn >= 1 ? 1 : easeOutBack(turn));
            if (item.node.rotation.z !== angle) { item.node.rotation.z = angle; dirty = true; }
          }
          const age = (now - item.born) / 420;
          if (age < 1 && item.node.visible) {  // a newly marked element swells into place
            item.mesh.scale.setScalar(1 + (item.swing ? 0.12 : 0.4) * Math.sin(Math.min(1, Math.max(0, age)) * Math.PI));
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
      renderer.render(scene, camera);
      labels.render(scene, camera);
    };
    frame = requestAnimationFrame(tick);

    const observer = new ResizeObserver(() => {
      const w = el.clientWidth, h = Math.max(el.clientHeight, 1);
      renderer.setSize(w, h);
      labels.setSize(w, h);
      frameView(w, h);
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
      // Each room's floor takes the tint of its use; everything else is coloured by its material.
      const tints = new Float32Array(parsed.header.vertices * 3).fill(1);
      const tint = new THREE.Color();
      for (const element of parsed.header.elements) {
        if (element.cls !== "room") continue;
        tint.setHex(tintOf(element.name ?? ""));
        for (let k = element.v[0]; k < element.v[0] + element.v[1]; k++) tint.toArray(tints, k * 3);
      }
      const tinted = new THREE.BufferAttribute(tints, 3);

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
        let geometry = new THREE.BufferGeometry();
        geometry.setAttribute("position", shared);
        if (group.style.kind === "floor") geometry.setAttribute("color", tinted);
        geometry.setIndex(new THREE.BufferAttribute(index, 1));
        if (isPlan && group.style.kind === "door" && group.ranges.length) {
          // A plan's doors stand as thin leaves, shut, in their openings.
          const leaves = group.ranges.map((element) => {
            const leaf = leafOf(parsed.positions.subarray(element.v[0] * 3, (element.v[0] + element.v[1]) * 3));
            const one = leafGeometry(leaf);
            one.translate(-leaf.width * 0.47, 0, 0);  // centred on the opening
            one.rotateZ(leaf.angle);
            one.translate(leaf.x, leaf.y, 0);
            return one;
          });
          geometry = mergeGeometries(leaves) ?? geometry;
        }
        const material = new THREE.MeshStandardMaterial({
          color: FACE[group.style.kind] ?? FACE.other, roughness: 0.9, metalness: 0, flatShading: true, side: THREE.DoubleSide,
          vertexColors: group.style.kind === "floor",
          transparent: !!group.style.glass, opacity: group.style.glass ? 0.5 : 1, depthWrite: !group.style.glass,
          polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
        });
        if (group.style.kind === "wall") {
          // Where a wall is cut, it is inked, as on a drawing: faces that look straight up turn dark.
          // Flat on the sheet the walls read as a plan's solid walls; standing, as a model cut open.
          material.onBeforeCompile = (shader) => {
            shader.fragmentShader = shader.fragmentShader.replace("#include <color_fragment>", `#include <color_fragment>
              vec3 cutNormal = normalize(cross(dFdx(vViewPosition), dFdy(vViewPosition)));
              float cutUp = abs((vec4(cutNormal, 0.0) * viewMatrix).y);
              diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.085, 0.12, 0.1), smoothstep(0.93, 0.97, cutUp) * 0.94);`);
          };
        }
        const mesh = new THREE.Mesh(geometry, material);
        mesh.visible = !group.style.hidden;
        mesh.castShadow = !group.style.glass;
        mesh.receiveShadow = true;
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

      // A round table under the model, with a fine rim.
      const span = modelBox.getSize(new THREE.Vector3());
      const reach = Math.max(span.x, span.z) * 2.6;
      const round = Math.hypot(span.x, span.z) * 0.78;
      const grid = new THREE.Group();
      const top = new THREE.Mesh(new THREE.CircleGeometry(round, 128), new THREE.MeshBasicMaterial({ color: look.table }));
      const rim = new THREE.Mesh(new THREE.RingGeometry(round, round * 1.012, 128), new THREE.MeshBasicMaterial({ color: look.rim }));
      top.rotation.x = rim.rotation.x = -Math.PI / 2;
      grid.add(top, rim);
      grid.position.set((modelBox.min.x + modelBox.max.x) / 2, modelBox.min.y - 0.03, (modelBox.min.z + modelBox.max.z) / 2);
      scene.add(grid);

      // The sun stands over the model; its shadow camera covers the model with room for lifted storeys.
      const middle = modelBox.getCenter(new THREE.Vector3());
      const radius = Math.max(span.length() / 2, 2);
      sun.position.copy(middle).add(new THREE.Vector3(0.5, 1, 0.36).normalize().multiplyScalar(radius * 2.6));
      sun.target.position.copy(middle);
      const shade = sun.shadow.camera;
      shade.left = shade.bottom = -radius * 1.5;
      shade.right = shade.top = radius * 1.5;
      shade.near = 0.5;
      shade.far = radius * 7;
      shade.updateProjectionMatrix();
      // What the model stands on takes its shadow: on the sheet, or on the table when there is none.
      // Round like the table when there is one, so no shadow falls past its edge.
      const catcher = new THREE.Mesh(directed.current ? new THREE.PlaneGeometry(reach, reach) : new THREE.CircleGeometry(round, 96),
        new THREE.ShadowMaterial({ opacity: look.shadow, depthWrite: false }));
      catcher.position.set(middle.x, -middle.z, modelBox.min.y + 0.012);
      catcher.receiveShadow = true;
      catcher.renderOrder = 1;
      root.add(catcher);
      if (theme === "night" && !directed.current) scene.fog = new THREE.Fog(0x021803, radius * 4.2, radius * 10);  // the table fades into the dark

      const fly: Stage["fly"] = (box, options = {}) => {
        if (directed.current) return;  // the page holds the camera
        const centre = box.getCenter(new THREE.Vector3());
        const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1.5);
        // Fit the narrower of the two view angles, so a tall pane does not crop the model.
        const vertical = (camera.fov * Math.PI) / 360;
        const half = Math.min(vertical, Math.atan(Math.tan(vertical) * camera.aspect));
        const distance = (radius / Math.sin(half)) * (bare ? 1.22 : 1.04);  // as a backdrop the model keeps its distance
        // Keep the side the viewer is on; come lower to look at one item.
        const side = new THREE.Vector3().subVectors(camera.position, controls.target).setY(0);
        if (side.lengthSq() < 1e-6 || options.fromAbove) {  // stand facing the model's long side, a little off square
          const size = box.getSize(new THREE.Vector3());
          if (size.z > size.x) side.set(0.84, 0, 0.54); else side.set(0.54, 0, 0.84);
        }
        side.normalize();
        const height = options.low ? 0.5 : bare ? 0.6 : 0.8;  // how high the camera stands, as an angle
        const direction = side.multiplyScalar(Math.cos(height)).setY(Math.sin(height)).normalize();
        const end = centre.clone().addScaledVector(direction, distance);
        if (options.fromAbove) {  // start looking straight down, as on the plan, then come round
          camera.position.copy(centre).add(new THREE.Vector3(0, distance * 1.1, 0.001));
          controls.target.copy(centre);
        }
        camera.near = Math.max(0.05, distance / 500);
        camera.far = distance * 40;
        camera.updateProjectionMatrix();
        flight = { t0: performance.now() + (options.fromAbove ? (bare ? 150 : 350) : 0), ms: options.ms ?? (options.fromAbove ? (bare ? 3000 : 2000) : bare ? 2200 : 1100),
                   p0: camera.position.clone(), p1: end, q0: controls.target.clone(), q1: centre };
        dirty = true;
      };

      // Storeys of an IFC model drop into place one after another; a plan's walls rise from the sheet.
      const offsets: Stage["offsets"] = new Map();
      const order = parsed.header.storeys.map((storey, i) => ({ i, z: storey.z ?? 0 })).sort((a, b) => a.z - b.z);
      order.forEach(({ i }, rank) => offsets.set(i, { now: isPlan ? 0 : 14 + rank * 9, target: 0 }));
      if (isPlan && !directed.current) {
        root.scale.z = 0.001;
        rise = { t0: performance.now() + (bare ? 300 : 900), ms: bare ? 1600 : 1300 };
      }
      if (directed.current) grid.visible = false;  // on a coloured page the paper needs no table

      stage.current = {
        parsed, root, base, lit, items: new Map(), tallies, byId, modelBox, offsets, ground: null, groundStorey: null, fly,
        redraw: () => { dirty = true; },
        spin: (on) => { controls.autoRotate = on && !directed.current; dirty = true; },
      };
      fly(modelBox, { fromAbove: true });
      setStoreys(isPlan ? 0 : order.length);
      setInfo(`${parsed.header.elements.length.toLocaleString("en")} elements · ${parsed.header.triangles.toLocaleString("en")} triangles`);
      setStatus("ready");
      onReady?.();

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
            if (isPlan) {
              // From the table up: the paper, each room's tint lying on it like a wash, then the shadows.
              const paper = placed.corners[0][2];
              catcher.position.z = paper + 0.03;
              for (const entry of s.base) {
                if (entry.style.kind !== "floor") continue;
                const material = entry.mesh.material as THREE.MeshStandardMaterial;
                material.transparent = true;
                material.opacity = 0.6;
                material.depthWrite = false;
                material.polygonOffsetFactor = -2;  // towards the eye: the wash must win against the paper under it
                material.polygonOffsetUnits = -2;
                material.needsUpdate = true;
                entry.raise = paper + 0.015 - (modelBox.min.y + 0.05);  // the floors' top comes just above the paper
                entry.mesh.castShadow = false;
                entry.mesh.receiveShadow = false;
                entry.mesh.renderOrder = 0;
                if (entry.edges) entry.edges.visible = false;
              }
            }
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
      renderer.dispose();
      el.replaceChildren();
      stage.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meshUrl, groundUrl, imageUrl]);

  // ---- the answer's elements: built when the answer (or the mesh) changes
  useEffect(() => {
    const s = stage.current;
    if (!s || status !== "ready") return;
    for (const item of s.items.values()) {
      item.mesh.geometry.dispose();
      (item.mesh.material as THREE.Material).dispose();
      if (item.label) item.mesh.remove(item.label);
    }
    const lookNow = look;
    s.lit.clear();
    s.items.clear();
    for (const child of [...s.tallies.children]) s.tallies.remove(child);
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
    const doorsCounted = highlights.some((h) => h.tone === "count" && /door/i.test(s.byId.get(h.id)?.cls ?? ""));
    const level = only != null ? storeyList[only]?.z ?? null : null;
    for (const entry of s.base) {
      const z = entry.storey != null ? storeyList[entry.storey]?.z ?? null : null;
      const above = active && level != null && z != null && z > level + 0.2;
      const same = active && only != null && entry.storey === only;
      const wash = isPlan && !!s.ground && entry.style.kind === "floor";  // a plan's room tints on the paper
      // While a plan's doors are being counted, each is shown by its own counted leaf: the plain ones step aside.
      const replaced = isPlan && entry.style.kind === "door" && doorsCounted;
      entry.mesh.visible = !entry.style.hidden && !above && !replaced;
      const material = entry.mesh.material as THREE.MeshStandardMaterial;
      // A plan is one storey: its walls stay solid, and the doors open out of them. In a building
      // model the answer's storey stays readable and the rest steps back so the answer shows through.
      const solid = !active || (isPlan && !entry.style.glass);
      material.transparent = !solid || !!entry.style.glass;
      material.opacity = solid ? (entry.style.glass ? 0.5 : 1) : same ? (entry.style.glass ? 0.25 : 0.78) : 0.07;
      material.depthWrite = solid && !entry.style.glass;
      if (wash) {  // paler while an answer is shown, so the answer's own colour leads
        material.transparent = true;
        material.opacity = active ? 0.32 : 0.6;
        material.depthWrite = false;
      }
      material.needsUpdate = true;
      entry.mesh.castShadow = !wash && !entry.style.glass && (solid || same);
      if (entry.edges) {
        entry.edges.visible = entry.mesh.visible && !wash;
        (entry.edges.material as THREE.LineBasicMaterial).opacity = solid || same ? lookNow.edgeOpacity : lookNow.edgeOpacity * 0.28;
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
      // On a plan a counted door is its thin leaf, hung at one jamb, opening a little as it is counted.
      const leaf = isPlan && h.tone === "count" && /door/i.test(element.cls) ? leafOf(positions) : null;
      const colour = h.tone === "area" ? lookNow.area : lookNow.count;
      // Drawn after the faded building, so the answer is never dimmed by a wall in front of it.
      const material = new THREE.MeshStandardMaterial({
        color: colour, emissive: colour, emissiveIntensity: 0.25, roughness: 0.7, flatShading: true,
        side: THREE.DoubleSide, transparent: true, opacity: h.tone === "area" ? 0.62 : 1, depthWrite: h.tone !== "area",
      });
      const hung = leaf ? leafGeometry(leaf) : null;  // `positions` are about the centre by now, and so is the leaf
      const mesh = new THREE.Mesh(hung ?? geometry, material);
      if (hung) geometry.dispose();
      mesh.renderOrder = 2;
      mesh.castShadow = h.tone !== "area";
      const rest = centre.z + (isPlan && h.tone === "area" ? 0.035 : 0);  // a room plate sits just above the sheet
      const node = new THREE.Group();
      if (leaf) {  // the node is the hinge: the jamb at the low end of the opening
        const half = leaf.width * 0.47;
        node.position.set(centre.x + leaf.x - half * Math.cos(leaf.angle), centre.y + leaf.y - half * Math.sin(leaf.angle), rest);
        node.rotation.z = leaf.angle;
      } else {
        node.position.set(centre.x, centre.y, rest);
      }
      node.add(mesh);
      node.visible = false;
      const outline = new THREE.LineSegments(new THREE.EdgesGeometry(hung ?? geometry, 28),
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
      s.lit.add(node);
      const world = new THREE.Box3(
        new THREE.Vector3(extent.min.x, extent.min.z, -extent.max.y),
        new THREE.Vector3(extent.max.x, extent.max.z, -extent.min.y));
      const swing = leaf ? (h.n % 2 ? 1 : -1) * 0.56 : 0;  // about a third open: clear to see, and clear of its neighbours
      s.items.set(h.n, { node, swing, closed: leaf?.angle ?? 0, mesh, label, tone: h.tone, born: 0, storey: element.storey, rest, box: world });
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
        label.position.set(s.modelBox.max.x + 4, -s.modelBox.min.z + 2, rest);
        label.userData = { storey, rest };
        s.tallies.add(label);
      }
    }

    if (!box.isEmpty()) {
      const whole = lifted.getSize(new THREE.Vector3()).length();
      const part = box.getSize(new THREE.Vector3()).length();
      s.fly(!bare && part < whole * 0.55 ? box.expandByScalar(Math.max(2, part * 0.25)) : lifted);
    }
    s.spin(true);  // a slow turn shows depth; any touch stops it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusKey, status, meshUrl, exploded]);

  // ---- marked one by one, in step with the plan and the counter
  useEffect(() => {
    const s = stage.current;
    if (!s) return;
    const now = performance.now();
    for (const [n, item] of s.items) {
      const show = visible.has(n);
      if (show && !item.node.visible) { item.born = now; item.node.rotation.z = item.closed; }
      item.node.visible = show;
      if (item.label) item.label.visible = show;
    }
    s.redraw();
  }, [visible, focusKey, status, exploded]);

  useEffect(() => {
    const s = stage.current;
    if (!s) return;
    const lookNow = look;
    for (const [n, item] of s.items) {
      const material = item.mesh.material as THREE.MeshStandardMaterial;
      const off = excluded.has(n);
      const hot = hover === n || focus === n;
      const colour = off ? lookNow.off : item.tone === "area" ? lookNow.area : lookNow.count;
      material.color.copy(colour);
      material.emissive.copy(colour);
      material.emissiveIntensity = off ? 0 : hot ? 0.7 : 0.25;
      item.label?.element.classList.toggle("hot", hot);
      item.label?.element.classList.toggle("off", off);
    }
    s.redraw();
  }, [hover, focus, excluded, focusKey, status, exploded]);

  // ---- inspecting one item: come down to it
  useEffect(() => {
    const s = stage.current;
    if (!s || focus === null) return;
    const item = s.items.get(focus);
    if (!item) return;
    const lift = item.storey != null ? s.offsets.get(item.storey)?.target ?? 0 : 0;
    const box = item.box.clone().translate(new THREE.Vector3(0, lift, 0));
    s.spin(false);
    s.fly(box.expandByScalar(Math.max(3.6, box.getSize(new THREE.Vector3()).length() * 1.5)), { low: true, ms: 800 });
  }, [focus]);

  // Many items on one model: smaller tags, so that each still shows.
  const crowded = highlights.length > 18;
  return (
    <div className={`viewer3d${theme === "night" ? " night" : ""}${crowded ? " crowded" : ""}${tags ? "" : " untagged"}`}>
      <div ref={host} className="stage3d" />
      {status === "loading" && !bare && <div className="viewer-wait">Building the model…</div>}
      {status !== "loading" && status !== "ready" && !bare && <div className="viewer-wait">{status}</div>}
      {status === "ready" && !bare && <div className="viewer-info">{info} · drag to orbit, scroll to zoom</div>}
      {status === "ready" && !bare && storeys > 1 && (
        <div className="tools upper">
          <button className={exploded ? "on" : ""} onClick={() => setExploded(!(exploded ?? false))}>
            {exploded ? "Stack storeys" : "Lift storeys apart"}
          </button>
        </div>
      )}
    </div>
  );
}
