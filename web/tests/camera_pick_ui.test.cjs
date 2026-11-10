const assert = require('node:assert/strict');
const test = require('node:test');

test('resized and letterboxed camera views select the same original pixel', async () => {
  const {imagePixel} = await import('../app/static/camera-pick.js');
  const image = {width: 1280, height: 720};
  const scaled = {left: 20, top: 80, width: 640, height: 360};
  assert.deepEqual(imagePixel(scaled, image, 180, 170), [320, 180]);
  const letterboxed = {...scaled, height: 480};
  assert.deepEqual(imagePixel(letterboxed, image, 180, 230), [320, 180]);
  assert.equal(imagePixel(letterboxed, image, 180, 100), null);
  assert.equal(imagePixel(letterboxed, image, 180, 520), null);
});

test('the preview rotates around the taught Z–Y′–Z″ axes', async () => {
  const {toolAxes} = await import('../app/static/gripper-orientation.js');
  function close(actual, expected) {
    actual.forEach((value, i) => assert.ok(Math.abs(value - expected[i]) < 1e-10));
  }
  close(toolAxes([0, 90, 0])[2], [1, 0, 0]);
  close(toolAxes([90, 90, 0])[2], [0, 1, 0]);
  const [x, y, z] = toolAxes([35, -53, 21]);
  const rotated = toolAxes([35, -53, 111]);
  close(rotated[0], y);
  close(rotated[1], x.map(value => -value));
  close(rotated[2], z);
});

function measured(opening_mm = 65) {
  return {opening_mm, footprint_base_mm:[[0,-20,15],[0,20,15],[0,20,-15],[0,-20,-15]]};
}

test('the floor stays level in BASE and extends from the wall towards the camera', async () => {
  const {workSurfaces} = await import('../app/static/gripper-depth.js');
  const s = Math.SQRT1_2;
  const camera = [[s,0,s],[0,1,0],[-s,0,s]];
  for (const [floorZ,tcpZ,relativeZ] of [[0,null,0],[-120,null,-120],[25,null,25],[-120,30,-150],[30,30,0],[40,15,25]]) {
    for (const wallX of [70,120]) {
      const {floor,wall} = workSurfaces(camera,wallX,floorZ,tcpZ);
      assert.equal(floor.baseZ,floorZ);
      const span = (a,b) => Math.hypot(...a.map((v,i)=>v-b[i]));
      assert.ok(Math.abs(span(floor.points[0],floor.points[1])-450)<1e-9);
      assert.ok(Math.abs(span(floor.points[0],floor.points[3])-500)<1e-9);
      assert.ok(span(wall.points[0],wall.points[3])>=150-1e-9);
      assert.deepEqual(floor.points.slice(0,2),wall.points.slice(0,2));
      assert.ok(wall.points.every(p=>p[0]===wallX));
      assert.ok(floor.points.slice(2).every(p=>p[0]<wallX));
      for (const p of [...floor.points, ...floor.lines.flat()]) {
        const dz = p.reduce((sum,v,i)=>sum+v*camera[i][2],0);
        assert.ok(Math.abs(dz-relativeZ)<1e-9);
      }
    }
  }
  assert.equal(workSurfaces(camera,70,null,null).floor,null);
});

test('parallel and nearly parallel walls give a finite floor at the projection of the wall center', async () => {
  const {workSurfaces} = await import('../app/static/gripper-depth.js');
  for (const pitch of [Math.PI/2,Math.PI/2-1e-5]) {
    const s=Math.sin(pitch), c=Math.cos(pitch);
    const camera=[[c,0,s],[0,1,0],[-s,0,c]];
    const {floor,wall}=workSurfaces(camera,70,-120,30);
    const rear = floor.points[0].map((v,i)=>(v+floor.points[1][i])/2);
    assert.ok(Math.abs(rear[0]*c-rear[2]*s-70*c)<1e-8);
    for (const p of floor.points) {
      assert.ok(Math.abs(p[0]*s+p[2]*c+150)<1e-8);
      assert.ok(Math.hypot(...p)<700);
    }
    assert.ok(wall.points.every(p=>p[0]===70));
  }
});

test('BASE sliders respect the full box at every endpoint of the floor and wall guides', async () => {
  const {defaultExample,workSurfaces} = await import('../app/static/gripper-depth.js');
  const {exampleGuide,constrainExample} = await import('../app/static/gripper-example.js');
  const s = Math.SQRT1_2;
  for (const camera of [[[1,0,0],[0,1,0],[0,0,1]], [[s,s,0],[-s,s,0],[0,0,1]], [[s,0,s],[0,1,0],[-s,0,s]]]) {
    const anchor = [300,-80,120];
    const surfaces = workSurfaces(camera,70,0,anchor[2]);
    const guide = exampleGuide(camera,anchor,surfaces);
    for (const preferred of [defaultExample,{...defaultExample,x:10000,sizeX:1000,sizeZ:1000}]) {
      const fit = constrainExample(guide,preferred);
      for (const [name,range] of Object.entries(fit.ranges)) {
        for (const endpoint of [range.min,range.max]) {
          const value = {...fit.value,[name]:endpoint};
          for (const sx of [-1,1]) for (const sy of [-1,1]) for (const sz of [-1,1]) {
            const p = [value.x+sx*value.sizeX/2,value.y+sy*value.sizeY/2,value.z+sz*value.sizeZ/2];
            assert.ok(guide.every(({n,b})=>p.reduce((sum,v,i)=>sum+v*n[i],0)<=b+1e-5),name);
          }
        }
      }
    }
  }
  const upright=[[1,0,0],[0,1,0],[0,0,1]];
  const standard=exampleGuide(upright,[0,0,0],workSurfaces(upright,70,0,0));
  const {ranges}=constrainExample(standard,defaultExample);
  assert.deepEqual(ranges.x,{min:-405,max:45});
  assert.deepEqual(ranges.y,{min:-196,max:196});
  assert.deepEqual(ranges.z,{min:32,max:118});
  assert.equal(ranges.sizeX.max,50);
  assert.equal(constrainExample(null,defaultExample),null);
  const parallel=[[0,0,-1],[0,1,0],[1,0,0]];
  const empty=exampleGuide(parallel,[0,0,0],workSurfaces(parallel,70,0,0));
  assert.equal(constrainExample(empty,defaultExample),null);
});

test('camera depth uses the taught face normal, independent of BASE origin and teaching order', async () => {
  const {cameraBasis, depthModel, defaultScene} = await import('../app/static/gripper-depth.js');
  const {toolAxes} = await import('../app/static/gripper-orientation.js');
  // The camera sees along +BASE Y; pixel right is +X and pixel down is -Z.
  const points = [[20,40],[140,25],[155,130],[40,120]].map(([u,v]) =>
    ({pixel:[u,v],pose:[680+u, -235, 700-v, 0,0,0]}));
  for (const ordered of [points,[...points].reverse(),[...points.slice(2),...points.slice(0,2)]]) {
    const basis = cameraBasis(ordered);
    basis[0].forEach((v,i) => assert.ok(Math.abs(v-[0,1,0][i])<1e-10));
    const result = depthModel(defaultScene, toolAxes([90,90,0]), basis, measured());
    assert.ok(Math.abs(result.gap-20)<1e-10);
    assert.deepEqual(result.lens, [-defaultScene.tcpDepth,0,0]);
    assert.equal(result.wallX-result.lens[0], defaultScene.distance);
  }
  // Invalid/incomplete teaching must not masquerade as a measured camera direction.
  assert.equal(cameraBasis(points.slice(1)),null);
  assert.equal(cameraBasis([points[0],points[2],points[1],points[3]]),null);
  const bad = structuredClone(points);
  bad[3].pose[1] += 5;
  assert.equal(cameraBasis(bad),null);
  const degenerate = points.map(p => ({...p,pose:[10,20,30,0,0,0]}));
  assert.equal(cameraBasis(degenerate),null);
});

test('the lens stays on the sight axis through the TCP, including when the TCP reaches the lens or passes the wall', async () => {
  const {depthModel,defaultScene} = await import('../app/static/gripper-depth.js');
  const {toolAxes} = await import('../app/static/gripper-orientation.js');
  for (const tcpDepth of [0,410,1000]) {
    const settings = {...defaultScene, tcpDepth};
    const model = depthModel(settings,toolAxes([10,120,5]),undefined,measured());
    assert.equal(Math.hypot(...model.lens), tcpDepth);
    assert.equal(model.wallX-model.lens[0], settings.distance);
    assert.equal(model.frontX-model.lens[0], settings.distance-settings.protrusion);
  }
});

test('signed depth is measured to the foremost finger pad, not to the TCP or the wrist', async () => {
  const {depthModel,defaultScene} = await import('../app/static/gripper-depth.js');
  const {toolAxes} = await import('../app/static/gripper-orientation.js');
  const model = (overrides={}, angles=[0,90,0], opening=65) => depthModel({...defaultScene,...overrides},toolAxes(angles),undefined,measured(opening));
  assert.equal(model().frontDepth,430);
  const near = (value, expected) => assert.ok(Math.abs(value-expected)<1e-9, value+' should equal '+expected);
  near(model().gap,20);
  near(model({distance:510}).gap,50);
  near(model({tcpDepth:432}).gap,-2);
  near(model({protrusion:90}).gap,-20);
  // With local X along sight, the positive pad ends at opening/2 + 12 mm.
  near(model({},[0,0,0]).gap,20-65/2-12);
  near(model({},[0,0,0],0).gap,20-12);
  near(model({length:100}).gap,20); // Extending the finger moves the wrist, not the defined TCP.
  assert.equal(model({},[9,103,33]).gap.toFixed(1),'7.8');
});

test('the detection footprint sizes the object and sets the opening between both finger pads', async () => {
  const {depthModel,defaultScene} = await import('../app/static/gripper-depth.js');
  const {toolAxes} = await import('../app/static/gripper-orientation.js');
  const axes = toolAxes([0,0,0]);
  const drawn = depthModel(defaultScene,axes,undefined,measured(50));
  assert.deepEqual(drawn.face.map(p=>p.slice(1)),[[-20,15],[20,15],[20,-15],[-20,-15]]);
  assert.equal(drawn.exampleObject,false);
  assert.equal(drawn.tip[0],37); // 25 mm inner half-opening plus the 12 mm pad
});

test('without a width estimate the whole gripper still rotates, with illustrative spacing only', async () => {
  const {depthModel,defaultScene,defaultExample} = await import('../app/static/gripper-depth.js');
  const settings = {...defaultScene,example:{...defaultExample,showWhenMissing:false}};
  const {toolAxes} = await import('../app/static/gripper-orientation.js');
  for (const angles of [[0,0,0], [9,103,33]]) {
    const axes = toolAxes(angles);
    const reference = depthModel(settings,axes,undefined,{opening_mm:65});
    assert.equal(reference.parts.length,6); // Wrist, palm, two fingers and two pads.
    for (const estimate of [null, {reason:'unmeasurable',opening_mm:null}]) {
      const missing = depthModel({...settings,opening:100},axes,undefined,estimate);
      assert.deepEqual(missing.parts,reference.parts);
      assert.equal(missing.opening,null);
      assert.equal(missing.tip,null);
      assert.equal(missing.gap,null);
      assert.deepEqual(missing.face,[]);
    }
  }
});

test('example vertices use absolute BASE center and dimensions in both taught and untaught frames', async () => {
  const {depthModel,defaultScene,defaultExample} = await import('../app/static/gripper-depth.js');
  const {toolAxes} = await import('../app/static/gripper-orientation.js');
  const example = {...defaultExample,sizeX:30,sizeY:40,sizeZ:50,x:-10,y:200,z:0};
  const axes = toolAxes([9,103,33]), camera = [[0,1,0],[0,0,1],[1,0,0]];
  for (const anchor of [null,[null,100,0],[25,100,-10]]) {
    const render = settings => depthModel({...defaultScene,example,...settings},axes,camera,null,anchor);
    const object = render({});
    assert.equal(object.exampleObject,true);
    assert.equal(object.objectPoints.length,8);
    assert.deepEqual(render({distance:560}).objectPoints,object.objectPoints);
    for (const [axis,center,size] of [[0,example.x,example.sizeX],[1,example.y,example.sizeY],[2,example.z,example.sizeZ]]) {
      const coords = object.objectPoints.map(p=>p.reduce((n,v,i)=>n+v*camera[i][axis],anchor?.[axis] ?? 0));
      assert.equal(Math.min(...coords),center-size/2);
      assert.equal(Math.max(...coords),center+size/2);
    }
    assert.equal(object.frontX,Math.min(...object.objectPoints.map(p=>p[0])));
    assert.equal(object.gap,null);
    assert.equal(object.opening,null);
  }
});

test('editing the example leaves the measured outline and recommended opening intact', async () => {
  const {depthModel,defaultScene,defaultExample} = await import('../app/static/gripper-depth.js');
  const {toolAxes} = await import('../app/static/gripper-orientation.js');
  const axes = toolAxes([0,0,0]);
  const example = {...defaultExample,sizeX:100,x:-30,showWhenMissing:false};
  const scene = {...defaultScene,example};
  const render = (editingExample,estimate)=>depthModel({...scene,editingExample},axes,undefined,estimate);
  for (const estimate of [null,{reason:'unmeasurable'},{footprint_base_mm:[]}]) {
    assert.equal(render(false,estimate).objectPoints.length,0);
    assert.equal(render(true,estimate).objectPoints.length,8);
  }
  const detection = measured(50);
  assert.deepEqual(render(false,detection),depthModel(defaultScene,axes,undefined,detection));
  const editing = render(true,detection);
  assert.equal(editing.exampleObject,true);
  assert.notDeepEqual(editing.objectPoints,render(false,detection).objectPoints);
  assert.equal(editing.opening,50);
  assert.equal(editing.gap,null);
  delete detection.opening_mm;
  assert.deepEqual(render(false,detection).face.map(p=>p.slice(1)),detection.footprint_base_mm.map(p=>p.slice(1)));
});

test('a late measurement cannot restore an old selection after its replacement is unavailable', {timeout:1500}, async () => {
  const {trackGripperEstimate} = await import('../app/static/gripper-measurement.js');
  let finish, started;
  const waiting = new Promise(resolve => { started = resolve; });
  const results = [];
  const tracker = trackGripperEstimate(() => new Promise(resolve => {finish=resolve; started();}), result=>results.push(result));
  tracker.update({}, {xyxy:[10,10,30,50]}, 0, null);
  await waiting;
  tracker.update(null, null, -1, '객체를 선택하세요.');
  finish(measured(50));
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(results.at(-1),{reason:'객체를 선택하세요.'});
  assert.ok(results.every(result=>result.opening_mm === undefined));
  tracker.reset();
});
