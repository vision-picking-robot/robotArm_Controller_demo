// Each guide face bounds a half-space in robot BASE millimetres.
const dot = (a,b) => a.reduce((sum,v,i) => sum+v*b[i],0);
const sub = (a,b) => a.map((v,i) => v-b[i]);
const along = (p,n,t) => p.map((v,i) => v+t*n[i]);
const cross = ([x,y,z],[u,v,w]) => [y*w-z*v,z*u-x*w,x*v-y*u];
const tolerance = 1e-7;
const minSize = 0.1;
const centers = ["x","y","z"], sizes = ["sizeX","sizeY","sizeZ"];

export function exampleGuide(camera, anchor, {floor,wall}) {
  if (!floor) return null;
  const toBase = point => centers.map((_,i) =>
    (anchor[i] ?? 0) + point.reduce((sum,v,j) => sum+v*camera[j][i],0));
  const baseFloor = floor.points.map(toBase), baseWall = wall.points.map(toBase);
  const planes = [];
  function cap(n, point) { planes.push({n,b:dot(n,point)}); }
  function span(a,b) {
    const delta = sub(b,a), length = Math.hypot(...delta);
    const n = delta.map(v => v/length);
    cap(n,b); cap(n.map(v => -v),a);
  }
  span(baseFloor[0],baseFloor[1]);
  // A wider floor does not permit the example to extend past the wall sides.
  if (Math.abs(Math.hypot(...sub(baseFloor[1],baseFloor[0]))-Math.hypot(...sub(baseWall[1],baseWall[0])))>tolerance)
    span(baseWall[0],baseWall[1]);
  span(baseFloor[0],baseFloor[3]); // Wall edge to front edge
  span(baseWall[0],baseWall[3]);   // Bottom to top projected onto the wall
  cap([0,0,-1],baseFloor[0]);
  cap([0,0,1],baseWall[3]);
  cap(camera[0],baseWall[0]);      // Camera-facing side of the wall
  return planes;
}

// Move the planes inward by the box's projected half-size. A center inside
// all those planes puts every corner of the BASE-aligned box inside the guides.
function centerPlanes(guide,size) {
  return guide.map(({n,b}) => ({n,b:b-n.reduce((sum,v,i) => sum+Math.abs(v)*size[i]/2,0)}));
}

function nearestCenter(planes, desired) {
  const inside = p => planes.every(({n,b}) => dot(p,n) <= b+tolerance);
  if (inside(desired)) return desired;
  let nearest = null, best = Infinity;
  function consider(p) {
    const distance = Math.hypot(...sub(p,desired));
    if (distance < best && inside(p)) { nearest = p; best = distance; }
  }
  // The closest point on a convex polyhedron lies on a face, edge or vertex.
  for (const [i,a] of planes.entries()) {
    const face = along(desired,a.n,a.b-dot(desired,a.n));
    consider(face);
    for (let j=i+1; j<planes.length; j++) {
      const b = planes[j], tangent = along(b.n,a.n,-dot(a.n,b.n));
      const length2 = dot(tangent,tangent);
      if (length2 < 1e-14) continue;
      const edge = along(face,tangent,(b.b-dot(b.n,face))/length2);
      consider(edge);
      const direction = cross(a.n,b.n);
      for (let k=j+1; k<planes.length; k++) {
        const c = planes[k], denominator = dot(direction,c.n);
        if (Math.abs(denominator)>1e-9) consider(along(edge,direction,(c.b-dot(c.n,edge))/denominator));
      }
    }
  }
  return nearest;
}

export function constrainExample(guide, preferred) {
  if (!guide) return null;
  const desired = centers.map(name => preferred[name]);
  const requested = sizes.map(name => Math.max(minSize,preferred[name]));
  let size = requested, planes = centerPlanes(guide,size), center = nearestCenter(planes,desired);
  if (!center) {
    // An older example or a changed guide can be too large to fit anywhere.
    size = sizes.map(() => minSize);
    planes = centerPlanes(guide,size);
    center = nearestCenter(planes,desired);
    if (!center) return null;
    let low = 0, high = 1;
    for (let i=0; i<32; i++) {
      const fraction = (low+high)/2;
      const trial = requested.map(v => minSize+(v-minSize)*fraction);
      const bounds = centerPlanes(guide,trial), position = nearestCenter(bounds,desired);
      if (position) { low = fraction; size = trial; planes = bounds; center = position; }
      else high = fraction;
    }
  }
  const value = {...preferred}, ranges = {};
  centers.forEach((name,i) => {
    value[name] = center[i]; value[sizes[i]] = size[i];
    let min = -Infinity, max = Infinity, maxSize = Infinity;
    for (const {n,b} of planes) {
      const coefficient = n[i];
      if (Math.abs(coefficient)<1e-12) continue;
      const margin = Math.max(0,b-dot(n,center));
      const bound = center[i]+margin/coefficient;
      if (coefficient>0) max = Math.min(max,bound);
      else min = Math.max(min,bound);
      maxSize = Math.min(maxSize,size[i]+2*margin/Math.abs(coefficient));
    }
    ranges[name] = {min,max};
    ranges[sizes[i]] = {min:minSize,max:maxSize};
  });
  return {value,ranges};
}
