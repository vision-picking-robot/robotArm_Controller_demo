import { describeGripper } from "./gripper-measurement.js";
import { constrainExample, exampleGuide } from "./gripper-example.js";
import { connectRobotArm } from "./robot-arm-view.js";
import { cameraPointToBase } from "./robot-arm.js";

// Preview geometry only. Lengths are millimetres; the TCP is the fixed origin
// of the drawing. Camera range is measured by the operator, never inferred from BASE X.
const dot = (a, b) => a.reduce((sum, v, i) => sum + v * b[i], 0);
const cross = ([x,y,z], [u,v,w]) => [y*w-z*v, z*u-x*w, x*v-y*u];
const sub = (a, b) => a.map((v, i) => v - b[i]);
const scaled = (p, k) => p.map(v => v * k);
const unit = p => scaled(p, 1 / Math.hypot(...p));
const identity = [[1,0,0], [0,1,0], [0,0,1]];
const area = (a, b, c) => (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0]);

function convex(points) {
  const turns = points.map((p, i) => area(p, points[(i+1)%4], points[(i+2)%4]));
  const span = Math.max(...points.flatMap(a => points.map(b => Math.hypot(...sub(a, b)))));
  const epsilon = Math.max(1e-6, span * span * 1e-6);
  return Math.min(...turns) > epsilon || Math.max(...turns) < -epsilon;
}

// For a frontoparallel target in the original camera image, image right × down
// points away from the camera. Using pixel order removes any dependence on
// which corner the operator taught first, or in which direction they went.
export function cameraBasis(corners) {
  if (corners?.length !== 4 || !corners.every(p => p.pixel?.length === 2 && p.pixel.every(Number.isFinite)
      && p.pose?.length >= 3 && p.pose.slice(0, 3).every(Number.isFinite))) return null;
  const pixels = corners.map(p => p.pixel), xyz = corners.map(p => p.pose.slice(0, 3));
  if (!convex(pixels)) return null;
  const ab = sub(xyz[1], xyz[0]), ac = sub(xyz[2], xyz[0]), normal = cross(ab, ac);
  if (Math.hypot(...normal) < 1e-6) return null;
  const det = area(...pixels.slice(0, 3)), forward = scaled(unit(normal), Math.sign(det));
  if (Math.abs(dot(forward, sub(xyz[3], xyz[0]))) > 2) return null;
  const dv1 = pixels[1][1] - pixels[0][1], dv2 = pixels[2][1] - pixels[0][1];
  const left = scaled(unit(ab.map((v, i) => v * dv2 - ac[i] * dv1)), -Math.sign(det));
  const up = cross(forward, left);
  if (!convex(xyz.map(p => [dot(p, left), dot(p, up)]))) return null;
  return [forward, left, up];
}

const metal = ["#83979f","#e8ece6","#a2b3b5","#c3d1d0","#a5b7b9","#e0e6e1"];
const steel = ["#344c57","#829599","#49616b","#617a81","#526b75","#8c9d9e"];
const amber = ["#dcaa74","#91623e","#aa7048","#d38f5c","#bb8052","#ce9668"];
const sides = [[0,2,3,1],[4,5,7,6],[0,1,5,4],[2,6,7,3],[0,4,6,2],[1,3,7,5]];
const defaultGuides = Object.freeze({halfWidth:225,wallHeight:150,floorDepth:500,spacing:50,wallRows:6});
// Dimensions of the approved E0509 UI mockup. The floor is wider than the
// wall and extends 110 mm behind it; teaching still determines their position.
const armGuides = Object.freeze({halfWidth:380,floorHalfWidth:550,wallHeight:760,
  fixedWallHeight:true,floorDepth:1260,floorBehind:110,spacing:100});
const exampleOpeningMm = 65; // Drawing only, while the detection has no measured width.
export const defaultScene = Object.freeze({distance:480, tcpDepth:410, protrusion:50, length:70,
  view:"oblique", panel:"angles"});
export const defaultExample = Object.freeze({sizeX:50, sizeY:58, sizeZ:64, x:45, y:0, z:32, showWhenMissing:true});

function box(center, size, transform) {
  const points = [];
  for (const z of [-1,1]) for (const y of [-1,1]) for (const x of [-1,1]) {
    const p = [x,y,z].map((s, i) => center[i] + s * size[i] / 2);
    points.push(transform ? transform(p) : p);
  }
  return points;
}

function gridFromEdge(center, across, extension, rows, {halfWidth:extentY,spacing}) {
  const point = (u,v) => center.map((n,i) => n + u*extentY*across[i] + v*extension[i]);
  const lines = [];
  for (let y=Math.ceil(-extentY/spacing)*spacing; y<extentY; y+=spacing) lines.push([point(y/extentY,0),point(y/extentY,1)]);
  for (let i=1; i<rows; i++) lines.push([point(-1,i/rows),point(1,i/rows)]);
  return {points:[point(-1,0),point(1,0),point(1,1),point(-1,1)], lines};
}

// The wall stays on camera X = wallX and the floor on BASE Z = floorZ.
export function workSurfaces(camera, wallX, floorZ, tcpZ, guides = defaultGuides) {
  const {wallHeight:extentZ,floorDepth,spacing} = guides;
  const vertical = camera.map(axis => axis[2]); // BASE +Z in camera coordinates
  const inWall = Math.hypot(vertical[1],vertical[2]);
  const up = inWall > 0 ? [0,vertical[1]/inWall,vertical[2]/inWall] : [0,0,1];
  const across = [0,up[2],-up[1]];
  const wallPoint = height => [wallX,up[1]*height,up[2]*height];
  function wallFrom(bottom) {
    const height = guides.fixedWallHeight ? extentZ : Math.max(extentZ,extentZ-bottom);
    return gridFromEdge(wallPoint(bottom),across,scaled(up,height),guides.wallRows??Math.ceil(height/spacing),guides);
  }
  if (!Number.isFinite(floorZ)) return {wall:wallFrom(-extentZ), floor:null};

  const relativeZ = floorZ - (Number.isFinite(tcpZ) ? tcpZ : 0);
  const dz = relativeZ - vertical[0]*wallX;
  // Near-parallel planes can intersect far outside the illustrated wall. In that
  // case anchor the floor to the vertical projection of the wall center.
  const reach = Math.abs(relativeZ) + Math.abs(wallX) + 2*extentZ;
  const meets = inWall > 0 && Math.abs(dz) <= inWall*reach;
  const bottom = meets ? dz/inWall : -extentZ;
  const edge = meets ? wallPoint(bottom) : vertical.map((v,i) => (i === 0 ? wallX : 0) + dz*v);
  const towardCamera = cross(vertical,across);
  const floorEdge = edge.map((v,i)=>v-towardCamera[i]*(guides.floorBehind??0));
  const floor = gridFromEdge(floorEdge,across,scaled(towardCamera,floorDepth),Math.ceil(floorDepth/spacing),
    {...guides,halfWidth:guides.floorHalfWidth??guides.halfWidth});
  floor.baseZ = floorZ;
  return {wall:wallFrom(bottom), floor};
}

export function depthModel(settings, toolAxes, camera = identity, estimate = null, anchor = null) {
  const {distance, tcpDepth, protrusion, length, example = defaultExample, editingExample = false} = settings;
  const opening = Number.isFinite(estimate?.opening_mm) ? estimate.opening_mm : null;
  const drawnOpening = opening ?? exampleOpeningMm;
  const center = estimate?.center_base_mm;
  const projectedCenter = !editingExample && anchor?.every(Number.isFinite) && center?.length === 3 && center.every(Number.isFinite)
    ? camera.map(axis => dot(sub(center,anchor),axis)) : null;
  const worldTool = toolAxes.map(axis => camera.map(direction => dot(axis, direction)));
  const transform = p => [0,1,2].map(i => p.reduce((n, v, j) => n + v * worldTool[j][i], 0));
  // Preview coordinates follow the camera's sight axis through the fixed TCP.
  const lens = [-tcpDepth, 0, 0];
  const wallX = distance - tcpDepth;
  const parts = [], pads = [];
  function part(center, size, palette, pad = false) {
    const points = box(center, size, transform);
    parts.push({points, palette});
    if (pad) pads.push(...points);
  }
  part([0,0,-length-26], [29,30,18], steel);
  part([0,0,-length-9], [drawnOpening+30,32,24], metal);
  for (const sign of [-1,1]) {
    part([sign*(drawnOpening/2+9),0,(-length-5)/2], [10,15,length-5], metal);
    part([sign*(drawnOpening/2+6),0,-5], [12,19,10], steel, true);
  }
  const footprint = estimate?.footprint_base_mm;
  const hasFootprint = Array.isArray(footprint) && footprint.length === 4 &&
    footprint.every(p => Array.isArray(p) && p.length === 3 && p.every(Number.isFinite));
  const exampleObject = example !== null && (editingExample || (!hasFootprint && example.showWhenMissing));
  let face = [], objectPoints = [], frontX = wallX;
  if (exampleObject) {
    const origin = [0,1,2].map(i => anchor?.[i] ?? 0);
    objectPoints = box([example.x,example.y,example.z], [example.sizeX,example.sizeY,example.sizeZ],
      p => camera.map(axis => dot(sub(p,origin),axis)));
    frontX = Math.min(...objectPoints.map(p => p[0]));
  } else if (hasFootprint && !editingExample) {
    frontX = wallX-protrusion;
    face = footprint.map(p => [frontX, dot(p,camera[1])+(projectedCenter?.[1] ?? 0),
      dot(p,camera[2])+(projectedCenter?.[2] ?? 0)]);
    // Front/back pairs, in the vertex order used by box().
    objectPoints = [0,1,3,2].flatMap(i => [face[i], [wallX,face[i][1],face[i][2]]]);
  }
  if (objectPoints.length) parts.push({points:objectPoints, palette:amber});
  const tip = exampleObject || editingExample || opening === null ? null : pads.reduce((best, p) => p[0] > best[0] ? p : best);
  return {parts, face, objectPoints, exampleObject, projectedCenter, opening, drawnOpening, frontDepth:frontX+tcpDepth, wallX, frontX, lens, tip,
    gap:tip ? frontX-tip[0] : null, toolZ:worldTool[2], camera};
}

function svgNode(tag, attrs, value) {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, val] of Object.entries(attrs)) n.setAttribute(key, val);
  if (value !== undefined) n.textContent = value;
  return n;
}
const pointsAttr = ps => ps.map(p => p.join(",")).join(" ");
const line = ([x1,y1], [x2,y2], stroke, width = 1, more = {}) =>
  svgNode("line", {x1,y1,x2,y2,stroke,"stroke-width":width,...more});
const label = (x,y,value,fill,more={}) => svgNode("text", {x,y,fill,"font-size":12,...more}, value);
const cameraColor = "#9ad7ff";

function cameraMarker([x,y], wall, tcpDepth) {
  const angle = Math.atan2(wall[1]-y, wall[0]-x) * 180 / Math.PI;
  const marker = svgNode("g", {transform:`translate(${x} ${y})`});
  const icon = svgNode("g", {transform:`rotate(${angle})`, stroke:cameraColor, "stroke-width":1.5, "stroke-linejoin":"round"});
  icon.append(svgNode("rect", {x:-28,y:-8,width:17,height:16,rx:3,fill:"#284857"}),
    svgNode("path", {d:"M-11 -3 L-3 -7 V7 L-11 3 Z",fill:"#426d80"}),
    svgNode("circle", {cx:0,cy:0,r:2.5,fill:cameraColor}),
    svgNode("path", {d:"M5 0 H21 M17 -4 L21 0 L17 4",fill:"none"}));
  marker.append(svgNode("title", {}, "카메라 위치 · TCP까지 "+tcpDepth+" mm"), icon,
    label(-9,-14,"카메라",cameraColor,{"text-anchor":"middle"}));
  return marker;
}

function drawRuler(svg, {distance,tcpDepth}, front, hasObject) {
  const w = svg.clientWidth, h = 66, y = 32;
  if (!w) return;
  const min = Math.floor(Math.min(0,front)/50)*50;
  const max = Math.ceil((Math.max(distance,tcpDepth,front)+25)/50)*50;
  const pos = v => 12 + (w-24) * (v-min) / (max-min);
  const nodes = [line([pos(min),y],[pos(max),y],"var(--line)",2)];
  const step = Math.max(50, Math.ceil((max-min)/1000)*50);
  for (let x=min; x<=max; x+=step) nodes.push(line([pos(x),y-2],[pos(x),y+3],"var(--sub)",.7));
  function mark(depth, text, labelX, above, anchor, color) {
    const x = pos(depth), dy = above ? -1 : 1;
    nodes.push(svgNode("polyline",{points:pointsAttr([[x,y],[x,y+dy*6],[labelX,y+dy*12]]),fill:"none",stroke:color}),
      label(labelX,y+(above?-16:25),text,color,{"text-anchor":anchor}));
  }
  mark(0,"카메라 0 mm",7,false,"start","var(--sub)");
  mark(tcpDepth,"TCP "+tcpDepth+" mm",w*.47,true,"end","var(--ink)");
  mark(distance,"벽 "+distance+" mm",w-8,true,"end","var(--mint)");
  const frontLabel = +front.toFixed(2);
  if (hasObject) mark(front,"물체 앞면 "+frontLabel+" mm",Math.max(117,Math.min(w-62,pos(front))),false,"middle","var(--ink)");
  svg.setAttribute("viewBox", "0 0 "+w+" "+h);
  svg.setAttribute("aria-label", "카메라 기준 TCP "+tcpDepth+" mm, 벽 "+distance+" mm, 대상 앞면 "+frontLabel+" mm");
  svg.replaceChildren(...nodes);
}

function drawScene(el, settings, model, anglesText, {wall,floor}) {
  const {scene, world} = el, w = scene.clientWidth, h = scene.clientHeight;
  if (!w || !h) return;
  const {parts, face, objectPoints, opening, drawnOpening, tip, wallX, frontX, frontDepth, gap, toolZ, camera, lens} = model;
  const side = settings.view === "side";
  const xy = ([x,y,z]) => side ? [x,-z] : [.866*x-.5*y, -.17*x-.294*y-.94*z];
  const depth = ([x,y,z]) => side ? -y : -.47*x-.814*y+.34*z;
  const bounds = [lens, ...wall.points, ...objectPoints, ...(floor?.points ?? [])];
  if (model.projectedCenter) bounds.push(model.projectedCenter);
  const projected = bounds.map(xy);
  const radius = Math.hypot(settings.length + 35, drawnOpening/2+15, 16);
  const minX = Math.min(-radius, ...projected.map(([x]) => x));
  const maxX = Math.max(radius, ...projected.map(([x]) => x));
  const minY = Math.min(-radius, ...projected.map(([,y]) => y));
  const maxY = Math.max(radius, ...projected.map(([,y]) => y));
  // Keep the TCP centered where possible, leaving room for the camera icon on narrow screens.
  const left = 36, right = 24;
  const scale = Math.min((w-left-right)/(maxX-minX), (h-28)/(maxY-minY));
  const tcpX = Math.max(left-minX*scale, Math.min(w/2, w-right-maxX*scale));
  const tcpY = Math.max(14-minY*scale, Math.min(h/2, h-14-maxY*scale));
  function project(p) {const [x,y] = xy(p); return [tcpX+x*scale,tcpY+y*scale];}
  const origin = project([0,0,0]);
  const lensPoint = project(lens), wallCenter = project([wallX,0,0]);
  scene.setAttribute("viewBox","0 0 "+w+" "+h);
  const nodes = [];
  if (floor) {
    nodes.push(svgNode("polygon",{points:pointsAttr(floor.points.map(project)),fill:"#25463c",stroke:"#81c5a5"}));
    for (const [a,b] of floor.lines) nodes.push(line(project(a),project(b),"#629d85",.8));
  }
  nodes.push(svgNode("polygon",{points:pointsAttr(wall.points.map(project)),fill:"#364a51",
    stroke:objectPoints.length ? "#90a7a9" : "#ffc08b","stroke-width":1.3}));
  if (!side) {
    for (const [a,b] of wall.lines) nodes.push(line(project(a),project(b),"#587074",.7));
  }
  nodes.push(line(lensPoint,wallCenter,cameraColor,1,{"stroke-dasharray":"4 5"}));
  const faces = parts.flatMap(({points,palette,faces = sides,id}) => faces.map((indices,i) => {
    const ps = indices.map(j => points[j]);
    return {points:ps, color:palette[i%palette.length], id, example:model.exampleObject && points === objectPoints,
      depth:ps.reduce((n,p) => n+depth(p),0)/4};
  })).sort((a,b) => a.depth-b.depth);
  for (const f of faces) nodes.push(svgNode("polygon",{points:pointsAttr(f.points.map(project)),fill:f.color,
    ...(f.id ? {"data-arm-part":f.id} : {}),
    stroke:f.example ? "#ffc08b" : "#24383d","stroke-dasharray":f.example ? "4 2" : "none","stroke-width":.85,"stroke-linejoin":"round"}));
  const objectLabel = model.exampleObject ? "예시 객체" : "탐지 상자 기준 물체";
  if (face.length) {
    nodes.push(svgNode("polygon",{points:pointsAttr(face.map(project)),fill:"none",stroke:"#ffc08b","stroke-width":1.5}));
  }
  if (objectPoints.length) nodes.push(label(w-14,21,objectLabel,"#ffc08b",{"text-anchor":"end"}));
  nodes.push(cameraMarker(lensPoint,wallCenter,settings.tcpDepth));
  const end = project(scaled(toolZ, 30));
  if (Math.hypot(...sub(end,origin)) > 1) nodes.push(line(origin,end,"#83ead3",2.8,{"marker-end":"url(#pick-z-arrow)"}));
  nodes.push(svgNode("circle",{cx:origin[0],cy:origin[1],r:4.6,fill:"#27363a",stroke:"#ffe4a2","stroke-width":2}));
  if (tip) {
    const a = project(tip), b = project([frontX,tip[1],tip[2]]);
    nodes.push(line(a,b,"#ffba81",2,{"stroke-dasharray":"3 2"}));
    for (const [x,y] of [a,b]) nodes.push(line([x,y-4],[x,y+4],"#ffba81",1.5));
  }
  const [wx,wy] = project(wall.points[3]);
  nodes.push(label(Math.min(w-22,wx+5),Math.max(14,wy-4),"벽","#d3e5e5"));
  if (floor) {
    nodes.push(label(w-10,h-6,"바닥 0 mm · BASE Z "+floor.baseZ+" mm","#ade4c6",{"text-anchor":"end"}));
  }
  if (model.projectedCenter) {
    const [x,y] = project(model.projectedCenter);
    nodes.push(svgNode("circle",{cx:x,cy:y,r:3.5,fill:"#ffe3bd",stroke:"#563e2e"}),
      label(Math.max(5,Math.min(w-54,x+5)),Math.max(14,y-5),"탐지 중심","#ffe3bd"));
  }
  // Small BASE triad keeps the camera-relative view tied to the robot frame.
  const baseX = lensPoint[0] < 100 && lensPoint[1] > h-83 ? lensPoint[0]+65 : 41;
  const base = [Math.max(41,Math.min(baseX,w-178)),h-33];
  for (const [i,name] of ["X","Y","Z"].entries()) {
    const [dx,dy] = xy(camera.map(axis => axis[i])), to = [base[0]+dx*21,base[1]+dy*21];
    if (Math.hypot(dx,dy) > .1) nodes.push(line(base,to,"#a0b3ba",1.2),label(to[0]+dx*6,to[1]+dy*5+3,name,"#c0d2d8",{"font-size":10,"text-anchor":"middle"}));
  }
  nodes.push(label(base[0]-1,h-6,"BASE","#a0b3ba",{"font-size":10,"text-anchor":"middle"}));
  const openingHint = opening === null ? "손가락 간격은 예시입니다." : "";
  if (openingHint) nodes.push(label(14,21,openingHint,"#d3e5e5"));
  world.replaceChildren(...nodes);
  const past = gap < -.05;
  el.gapLabel.textContent = past ? "앞면을 지난 깊이" : "손끝 → 앞면";
  el.gap.textContent = gap === null ? "—" : Math.abs(gap).toFixed(1)+" mm";
  scene.dataset.previewModel = "gripper";
  scene.setAttribute("aria-label", "고정 TCP의 그리퍼 방향: "+anglesText+". "+(objectPoints.length ? "벽과 "+objectLabel : "벽")+
    ". 카메라에서 시선축을 따라 TCP까지 "+settings.tcpDepth+" mm, 벽까지 "+settings.distance+" mm. 대상 앞면 "+
    (+frontDepth.toFixed(2))+" mm. "+el.gapLabel.textContent+" "+el.gap.textContent+" (시선축 기준). "+
    (floor ? "바닥 BASE Z "+floor.baseZ+" mm. " : "")+el.tcpHeight.textContent+" "+el.objectHeight.textContent+" "+
    openingHint);
  drawRuler(el.ruler,settings,frontDepth,Boolean(objectPoints.length));
}

const dimensions = [
  ["distance","카메라 → 벽",300,700,"depthFields"],
  ["protrusion","탐지 객체 돌출 깊이",10,120,"jawPanel"],
  ["tcpDepth","카메라 → TCP",0,1000,"depthFields"],
  ["length","손가락 길이",25,110,"jawPanel"]
];
const choices = {view:["oblique","side"],panel:["angles","jaws"]};
const exampleKey = "vision-work-plane-example-v1";
const exampleLimit = 1e6; // Same mm range as BASE teaching coordinates.

function validExampleValue(name, value) {
  return Number.isFinite(value) && Math.abs(value) <= exampleLimit && (!name.startsWith("size") || value > 0);
}

function restoredExample() {
  const example = {...defaultExample};
  try {
    const saved = JSON.parse(localStorage.getItem(exampleKey));
    for (const name of Object.keys(example)) {
      if (typeof example[name] === "number" && validExampleValue(name,saved?.[name])) example[name] = saved[name];
    }
    if (typeof saved?.showWhenMissing === "boolean") example.showWhenMissing = saved.showWhenMissing;
  } catch { /* Storage is optional. */ }
  return example;
}

function restoredSettings(key) {
  const result = {...defaultScene};
  if (!key) return result;
  try {
    const saved = JSON.parse(localStorage.getItem(key));
    for (const [name,,min,max] of dimensions) {
      if (Number.isFinite(saved?.[name]) && saved[name] >= min && saved[name] <= max) result[name] = saved[name];
    }
    for (const [name, allowed] of Object.entries(choices)) if (allowed.includes(saved?.[name])) result[name] = saved[name];
  } catch { /* Storage is optional. */ }
  return result;
}

export function connectDepthPreview(el, signal) {
  let key, settings = {...defaultScene}, pose, estimate = null, floorZ = null;
  const example = restoredExample();
  let editingExample = false, manipulator = false;
  let fit;
  const controls = [...el.calibration.querySelectorAll("[data-example]")].map(number => {
    const name = number.dataset.example;
    const slider = el.calibration.querySelector('[data-example-range="'+name+'"]');
    return {name,number,slider,limits:slider.nextElementSibling};
  });
  const fields = {};
  const arm = connectRobotArm(el,signal,() => syncView());
  const on = (node, type, fn) => node.addEventListener(type, fn, {signal});
  function syncView() {
    for (const button of el.calibration.querySelectorAll("[data-depth-view]"))
      button.setAttribute("aria-pressed",String(button.dataset.depthView===(manipulator?arm.getView():settings.view)));
  }
  function remember() {
    try { if (key) localStorage.setItem(key,JSON.stringify(settings)); } catch { /* Private browsing. */ }
  }
  function draw(typing) {
    if (!pose) return;
    const tcpZ = pose.xyz[2];
    const height = Number.isFinite(floorZ) && Number.isFinite(tcpZ) ? tcpZ-floorZ : null;
    const describe = h => h === 0 ? "바닥면 0 mm" :
      (h < 0 ? "바닥 아래 " : "바닥 위 ") + Number(h.toFixed(2)) + " mm";
    const base = pose.basis ?? identity;
    const surfaces = workSurfaces(base,settings.distance-settings.tcpDepth,floorZ,tcpZ,manipulator?armGuides:defaultGuides);
    fit = constrainExample(exampleGuide(base,pose.xyz,surfaces),example);
    const shown = fit?.value;
    for (const {name,number,slider,limits} of controls) {
      number.disabled = slider.disabled = !fit;
      if (!fit) { number.value = ""; limits.textContent = "—"; continue; }
      const {min,max} = fit.ranges[name];
      number.min = slider.min = min; number.max = slider.max = max;
      slider.value = shown[name];
      slider.disabled = max-min < 1e-7;
      if (number !== typing) {
        number.value = Math.max(min,Math.min(max,Number(shown[name].toFixed(6))));
        number.removeAttribute("aria-invalid");
      }
      limits.textContent = Number(min.toFixed(1))+" ~ "+Number(max.toFixed(1))+" mm";
    }
    const adjusted = fit && controls.some(({name}) => Math.abs(shown[name]-example[name])>1e-6);
    el.exampleError.textContent = !fit ? (surfaces.floor ? "현재 벽과 바닥 사이에 객체를 놓을 공간이 없습니다." : "바닥 Z를 입력하면 예시 객체를 조절할 수 있습니다.") :
      adjusted ? "현재 가이드 범위에 맞춰 예시 객체를 표시합니다." : "";
    el.tcpHeight.textContent = floorZ === null ? "바닥 Z를 입력하세요." :
      (pose.corner+1)+"번 모서리 TCP · "+(height === null ? "Z 미교시 · 높이는 BASE 원점 기준입니다." : describe(height));
    const z = estimate?.center_base_mm?.[2];
    if (editingExample) {
      el.objectHeight.textContent = shown ? "예시 중심 · BASE ("+[shown.x,shown.y,shown.z].map(v=>Number(v.toFixed(2))).join(", ")+") mm" : "";
    } else {
      el.objectHeight.textContent = floorZ === null ? "" : "탐지 물체 중심 · "+
        (Number.isFinite(z) ? describe(z-floorZ) : estimate?.center_reason || estimate?.reason || "탐지 객체를 선택하세요.");
    }
    const model = depthModel({...settings,example:shown ?? null,editingExample}, pose.axes, base, estimate, pose.xyz);
    const sizing = editingExample && shown ? "예시 크기 (X × Y × Z) · "+[shown.sizeX,shown.sizeY,shown.sizeZ].map(v=>Number(v.toFixed(2))).join(" × ")+" mm"+
      (model.opening === null ? "" : " · 탐지 기준 손가락 벌림 "+model.opening.toFixed(1)+" mm") :
      editingExample ? el.exampleError.textContent : describeGripper(estimate);
    if (el.sizing.textContent !== sizing) el.sizing.textContent = sizing;
    if (manipulator) {
      const convert = p => cameraPointToBase(p,base,pose.xyz);
      const surface = s => s && {...s,points:s.points.map(convert),lines:s.lines.map(points=>points.map(convert))};
      arm.setGuides({wall:surface(surfaces.wall),floor:surface(surfaces.floor),lens:convert(model.lens),camera:base,
        target:pose.xyz.every(Number.isFinite)?pose.xyz:null,objectPoints:model.objectPoints.map(convert),exampleObject:model.exampleObject});
      drawRuler(el.ruler,settings,model.frontDepth,Boolean(model.objectPoints.length));
    } else drawScene(el, settings, model, pose.anglesText, surfaces);
  }
  function sync() {
    for (const [name,buttons] of Object.entries(groups)) {
      for (const button of buttons) button.setAttribute("aria-pressed",String(button.getAttribute("data-depth-"+name) === settings[name]));
    }
    el.anglePanel.hidden = settings.panel !== "angles";
    el.jawPanel.hidden = settings.panel !== "jaws";
    for (const [name,{range,number}] of Object.entries(fields)) range.value = number.value = settings[name];
    syncView();
    draw();
  }
  function rememberExample(typing) {
    try { localStorage.setItem(exampleKey,JSON.stringify(example)); } catch { /* Storage is optional. */ }
    draw(typing);
  }
  for (const {name,number,slider,limits} of controls) {
    function apply(value, typing) {
      if (!fit || !Number.isFinite(value)) return;
      const {min,max} = fit.ranges[name];
      Object.assign(example,fit.value,{[name]:Math.max(min,Math.min(max,value))});
      number.removeAttribute("aria-invalid");
      rememberExample(typing);
    }
    on(slider,"input",() => {
      const value = slider.valueAsNumber;
      apply(value === Number(slider.min) || value === Number(slider.max) ? value : Math.round(value*10)/10);
    });
    on(number,"input",() => {
      if (number.validity.valid) apply(number.valueAsNumber,number);
      else {
        number.setAttribute("aria-invalid","true");
        el.exampleError.textContent = "입력 범위 · "+limits.textContent;
      }
    });
    on(number,"blur",() => {
      if (number.getAttribute("aria-invalid") === "true" && Number.isFinite(number.valueAsNumber)) apply(number.valueAsNumber);
      else draw();
    });
  }
  el.showExample.checked = example.showWhenMissing;
  on(el.showExample,"change",() => { example.showWhenMissing = el.showExample.checked; rememberExample(); });
  const groups = {};
  for (const name of Object.keys(choices)) {
    groups[name] = el.calibration.querySelectorAll("[data-depth-"+name+"]");
    for (const button of groups[name]) on(button,"click",() => {
      if (name==="view" && manipulator) {arm.setView(button.dataset.depthView);syncView();return;}
      settings[name] = button.getAttribute("data-depth-"+name);
      sync(); remember();
    });
  }
  for (const [name,caption,min,max,group] of dimensions) {
    const row = document.createElement("div"), head = document.createElement("div"), lab = document.createElement("label");
    const value = document.createElement("span"), number = document.createElement("input"), range = document.createElement("input");
    row.className = "pick-angle-row"; head.className = "pick-angle-label"; value.className = "pick-angle-value";
    range.type = "range"; range.id = "pick-depth-"+name; number.type = "number";
    range.step = ".1"; number.step = "any"; number.required = true;
    range.min = number.min = min; range.max = number.max = max;
    number.title = min+"–"+max+" mm";
    lab.htmlFor = range.id; lab.textContent = caption; number.setAttribute("aria-label", caption+" 직접 입력 (mm)");
    value.append(number,"mm"); head.append(lab,value); row.append(head,range); el[group].append(row);
    fields[name] = {row,number,range};
    on(range,"input",() => {settings[name] = range.valueAsNumber; number.value = settings[name]; draw();});
    on(range,"change",remember);
    on(number,"input",() => {
      if (number.validity.valid) {settings[name] = number.valueAsNumber; range.value = settings[name]; draw();}
    });
    on(number,"change",() => {number.value = settings[name]; remember();});
    on(number,"blur",() => {number.value = settings[name];});
  }
  const observer = new ResizeObserver(draw);
  observer.observe(el.scene);
  observer.observe(el.ruler);
  signal.addEventListener("abort",() => observer.disconnect(),{once:true});
  function render(corners, axes, anglesText, camera, corner) {
    const next = camera?.camera_id ? "vision-gripper-preview-v1:"+JSON.stringify([camera.camera_id,camera.width,camera.height]) : null;
    pose = {basis:cameraBasis(corners), axes, anglesText, corner, xyz:corners[corner].pose.slice(0,3)};
    el.basisHint.textContent = pose.basis ? "시선: 네 모서리의 작업면에 수직." : "예시 시선: BASE +X (유효한 네 점을 교시하기 전).";
    if (next !== key) {
      key = next; settings = restoredSettings(key); sync();
    } else draw();
  }
  return {render, setEstimate(result) { estimate = result; draw(); }, setFloor(z) { floorZ = z; draw(); },
    editExample(active) { editingExample = active; draw(); },
    robotStatus:arm.robotStatus,
    setGripperAttached:arm.setAttached,gripperStatus:arm.gripperStatus,setParcelBox:arm.setParcelBox,
    simulate:arm.simulate,simulationAction:arm.simulationAction,observeSimulation:arm.observeSimulation,
    simulateRecipe:arm.simulateRecipe,setScenarioPreview:arm.setScenarioPreview,setScenarioVisible:arm.setScenarioVisible,
    setManipulator(active) {
      manipulator = active;
      el.modelTitle.textContent = active ? "E0509 자세" : "그리퍼 방향";
      el.modelHint.hidden = !active;
      el.scene.hidden = el.gripperFoot.hidden = active;
      el.calibration.closest(".plane-editor").dataset.armActive = active;
      for (const node of el.calibration.querySelectorAll("[data-arm-only]")) node.hidden=!active;
      arm.active(active);syncView();
      el.surfaceNote.textContent = (active ? "TCP" : "손끝")+"과 앞면의 깊이 차이는 충돌 판정이 아닙니다.";
      draw();
    }};
}
