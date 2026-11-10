import { watchDetections } from "./detection-feed.js";
import { connectOrientation } from "./gripper-orientation.js";
import { trackGripperEstimate } from "./gripper-measurement.js";
import { imagePixel, api, center, marker, sameInput, readCalibration } from "./robot-camera-frame.js";
import { subscribeParcelBox, connectWaypointSimulation } from "./robot-work-points.js";
import {connectScenario} from './robot-scenario.js';

const axes = ["X", "Y", "Z"];
const blankCorners = () => Array.from({length:4}, () => ({pixel:null, pose:Array(6).fill(null)}));

function connectFloorReference(el, signal, preview) {
  let saved = null, pending = false, conflict = false;
  const draft = () => Number.isFinite(el.floorZ.valueAsNumber) ? el.floorZ.valueAsNumber : null;
  function sync(message) {
    const z = draft(), dirty = saved && (z !== saved.z_mm || el.floorZ.validity.badInput);
    el.floorZ.disabled = el.floorRead.disabled = pending || !saved;
    el.floorSave.disabled = pending || conflict || !saved || z === null || (!dirty && saved.revision !== null);
    el.floorCancel.disabled = pending || !dirty;
    el.floorReload.hidden = !conflict && Boolean(saved || pending);
    el.floorReload.disabled = pending;
    if (message) el.floorStatus.textContent = message;
    else if (!conflict) el.floorStatus.textContent = dirty ? "저장 전 바닥 기준입니다." :
      saved?.revision == null ? "기본 바닥 Z · 0 mm" : "저장된 바닥 Z · "+saved.z_mm+" mm";
    preview(z);
  }
  async function run(action) {
    if (pending) return;
    pending = true; sync("바닥 기준을 "+(action === "save" ? "저장" : "조회")+"하고 있습니다.");
    let message;
    try {
      if (action === "read") {
        const {pose} = await api("work-points/current", "GET", undefined, signal);
        el.floorZ.value = pose[2];
        message = "현재 TCP의 Z를 불러왔습니다. 값을 확인하고 바닥 기준을 저장하세요.";
      } else {
        const value = action === "save"
          ? await api("floor-reference","PUT",{revision:saved.revision, z_mm:draft()},signal)
          : await api("floor-reference","GET",undefined,signal);
        saved = value; conflict = false;
        el.floorZ.value = saved.z_mm;
      }
    } catch (error) {
      if (action === "save" && error.status === 409) conflict = true;
      message = error.message;
    } finally {
      pending = false;
      if (!signal.aborted) sync(message);
    }
  }
  el.floorZ.addEventListener("input", () => sync(), {signal});
  el.floorRead.addEventListener("click", () => run("read"), {signal});
  el.floorSave.addEventListener("click", () => run("save"), {signal});
  el.floorReload.addEventListener("click", () => run("load"), {signal});
  el.floorCancel.addEventListener("click", () => { el.floorZ.value = saved.z_mm; sync(); }, {signal});
  void run("load");
}

export function connectWorkPlane(root) {
  const ui = Object.fromEntries([...root.querySelectorAll("[data-plane]")].map(node => [node.dataset.plane,node]));
  const editor = ui.template.content.firstElementChild.cloneNode(true);
  ui.mount.replaceChildren(editor);
  const el = Object.fromEntries([...editor.querySelectorAll("[data-pick]")].map(node => [node.dataset.pick,node]));
  // Move the live controls, retaining their global bindings and unsaved drafts.
  const livePanels = [...document.querySelectorAll("[data-hand-panel], [data-manipulator-panel]")];
  el.calibration.append(...livePanels);
  const lifetime = new AbortController();
  const on = (node, type, handler) => node.addEventListener(type, handler, {signal:lifetime.signal});
  const tabs = [...editor.querySelectorAll('.plane-tabs > [role="tab"]')];
  let visibleTabs = tabs;
  let gripperWorkspace = false;
  let manipulatorWorkspace = false;
  function selectTab(active, navigate = false) {
    orientation.setManipulator(!gripperWorkspace && active.id !== "plane-gripper-tab");
    orientation.setScenarioVisible(active.id==='manipulator-points-tab'&&manipulatorWorkspace);
    if(active.id!=="manipulator-points-tab")orientation.simulationAction("stop");
    for (const tab of tabs) {
      const selected = tab === active;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      editor.querySelector("#" + tab.getAttribute("aria-controls")).hidden = !selected;
    }
    const editingExample = active.id === "plane-example-tab";
    editor.querySelector(".pick-footer").hidden = gripperWorkspace || manipulatorWorkspace || editingExample || active.id === "plane-floor-tab";
    orientation.editExample(editingExample);
    const hash = active.dataset.manipulatorTab || active.dataset.gripperTab;
    if (navigate && hash && location.hash !== hash) history.pushState(history.state, "", hash);
  }
  tabs.forEach(tab => {
    on(tab, "click", () => selectTab(tab,true));
    on(tab, "keydown", event => {
      const index = visibleTabs.indexOf(tab), count = visibleTabs.length;
      const next = {ArrowRight:(index+1)%count, ArrowLeft:(index+count-1)%count,
        Home:0, End:count-1}[event.key];
      if (next === undefined) return;
      event.preventDefault();
      selectTab(visibleTabs[next],true);
      visibleTabs[next].focus();
    });
  });
  let frozen, calibration, corners = blankCorners(), savedCorners, clearance = 5, corner = 0, measureIndex = -1;
  let region=new URLSearchParams(location.search).get('pick_region')||'legacy';
  if(!['legacy','floor','shelf'].includes(region))region='legacy';
  ui.region.value=region;
  let pending = false, freezeNext = false, deviceId, cameraIndex, robotBlock = "로봇 작업 상태를 확인하고 있습니다.";
  const locked = () => pending || Boolean(robotBlock);
  const setState = value => { ui.state.textContent = value; };
  const fields = axes.map((axis, index) => {
    const label = document.createElement("label"), input = document.createElement("input");
    label.textContent = axis + " (mm)";
    input.type = "number"; input.step = "any";
    label.append(input); el.fields.append(label);
    on(input, "input", () => {
      corners[corner].pose[index] = Number.isFinite(input.valueAsNumber) ? input.valueAsNumber : null;
      showPosition(); showOrientation(); updateSave();
    });
    return input;
  });
  const orientation = connectOrientation(el, (index, value, input) => {
    corners[0].pose[3 + index] = value;
    showOrientation(input);
    updateSave();
  }, lifetime.signal);
  let parcelBox = null;
  const unsubscribeBox = subscribeParcelBox(value => {
    parcelBox = value;
    orientation.setParcelBox(manipulatorWorkspace ? value : null);
  });
  lifetime.signal.addEventListener("abort",unsubscribeBox,{once:true});
  let manipulatorConnected = false;
  const regionAngles=['A','B','C'].map((axis,i)=>{
    const label=document.createElement('label'),input=document.createElement('input');label.textContent=axis+' (°)';input.type='number';input.step='any';label.append(input);el.regionAngles.append(label);
    on(input,'input',()=>{corners[0].pose[3+i]=Number.isFinite(input.valueAsNumber)?input.valueAsNumber:null;showOrientation(input);updateSave();});return input;
  });
  on(ui.region,'change',()=>{region=ui.region.value;if(frozen)void load(frozen);});
  const measurement = trackGripperEstimate(
    (body, signal) => api("pick/gripper-preview", "POST", body, signal),
    orientation.setEstimate);
  connectFloorReference(el, lifetime.signal, orientation.setFloor);

  function draftCalibration(preview = false) {
    if (!calibration || !corners.every(p => p.pixel && p.pose.slice(0,3).every(Number.isFinite))) return null;
    const margin = Number.isFinite(clearance) && clearance >= 0 ? clearance : null;
    if (!preview && (margin === null || !corners[0].pose.slice(3).every(Number.isFinite))) return null;
    return {...calibration, clearance_per_side_mm:margin,
      corners:corners.map(p => ({pixel:p.pixel, pose:[...p.pose.slice(0,3),...corners[0].pose.slice(3)]}))};
  }

  function updateMeasurement() {
    const objects = frozen?.packet.objects ?? [];
    const object = objects[measureIndex];
    const draft = draftCalibration(true);
    let reason = null;
    if (!calibration) reason = "카메라 영상과 작업면 보정이 필요합니다.";
    else if (!objects.length) reason = "고정된 영상에 탐지된 객체가 없습니다.";
    else if (!object) reason = "작업 평면 보정 → 그리퍼 설정 → 손가락 치수에서 측정할 객체를 선택하세요.";
    else if (!draft) reason = "네 모서리의 영상·BASE 좌표를 입력하세요.";
    measurement.update(draft, object, measureIndex, reason);
  }

  function showOrientation(editing) {
    const packet = frozen?.packet;
    updateMeasurement();
    orientation.render(corners, corner, locked() || !calibration, packet && {camera_id:packet.camera_id, ...packet.source_size}, editing);
    regionAngles.forEach((input,i)=>{if(input!==editing)input.value=corners[0].pose[3+i]??'';input.disabled=locked()||!calibration;});
    el.regionAngles.hidden=region==='legacy';
  }

  function draw() {
    if (!frozen?.photo) return;
    const {packet, photo} = frozen, canvas = el.photo;
    canvas.width = packet.source_size.width; canvas.height = packet.source_size.height;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(photo, 0, 0, canvas.width, canvas.height);
    const radius = Math.max(5, canvas.width / 150);
    if (corners.every(p => p.pixel)) {
      ctx.beginPath();
      corners.forEach(({pixel}, i) => i ? ctx.lineTo(...pixel) : ctx.moveTo(...pixel));
      ctx.closePath();
      ctx.strokeStyle = "#a0ffcf"; ctx.lineWidth = 2; ctx.stroke();
    }
    corners.forEach((p, i) => {
      if (!p.pixel) return;
      const [x, y] = p.pixel;
      marker(ctx, x, y, radius, i === corner ? "#ffcf61" : "#a0ffcf");
      ctx.font = "bold " + Math.max(16, radius * 3) + "px sans-serif";
      ctx.lineWidth = 4; ctx.strokeStyle = "#18302b"; ctx.fillStyle = "white";
      ctx.strokeText(String(i + 1), x + radius, y); ctx.fillText(String(i + 1), x + radius, y);
    });
    const index = measureIndex;
    const highlight = context => {
      if (index < 0) return;
      const obj = packet.objects[index], [x1,y1,x2,y2] = obj.xyxy;
      context.strokeStyle = "#80ffd0"; context.lineWidth = 4; context.strokeRect(x1,y1,x2-x1,y2-y1);
      marker(context,...center(obj),radius,"#ffcf61");
    };
    highlight(ctx);
    const sample = el.measurePhoto;
    sample.width = canvas.width; sample.height = canvas.height;
    const sampleContext = sample.getContext("2d");
    sampleContext.drawImage(photo,0,0,sample.width,sample.height);
    highlight(sampleContext);
  }

  function updateSave() {
    const dirty = clearance !== (calibration?.clearance_per_side_mm ?? 5) || JSON.stringify(corners) !== JSON.stringify(savedCorners);
    el.discard.disabled = locked() || !calibration || !dirty;
    el.save.disabled = el.discard.disabled || !draftCalibration();
    if (calibration) setState((calibration.revision ? "보정됨" : "미보정") + (dirty ? " · 저장 전" : ""));
    if (!pending && calibration) el.note.textContent = dirty ? "저장 전 변경 사항이 있습니다." :
      calibration.revision ? "저장된 작업 평면입니다." : "영상의 네 모서리를 둘레 순서로 교시하세요.";
  }

  const steps = Array.from({length: 4}, (_, i) => {
    const button = document.createElement("button");
    button.type = "button"; el.steps.append(button);
    on(button, "click", () => selectCorner(i));
    return button;
  });

  function selectCorner(i) {
    corner = i;
    showCorner();
  }

  function showPosition() {
    const point = corners[corner];
    el.positionTitle.textContent = (corner + 1) + "번 모서리 위치";
    el.pixel.textContent = point.pixel ? "영상 위치　" + point.pixel.map(n => n.toFixed(1)).join(", ") + " px" :
      (corner + 1) + "번 모서리를 영상에서 짚으세요.";
  }

  function showCorner() {
    steps.forEach((step, i) => {
      step.textContent = (i + 1) + "번 모서리" + (corners[i].pixel ? " ✓" : "");
      step.setAttribute("aria-pressed", String(i === corner));
      step.disabled = locked();
    });
    fields.forEach((field, i) => {
      field.value = corners[corner].pose[i] ?? "";
      field.disabled = locked() || !calibration;
    });
    showPosition();
    showOrientation();
    el.clearance.disabled = locked() || !calibration;
    el.read.disabled = !calibration || locked();
    draw(); updateSave(); availability();
  }

  on(el.measureObject, "change", () => {
    measureIndex = Number(el.measureObject.value); updateMeasurement(); draw();
  });
  on(el.clearance, "input", () => {
    clearance = Number.isFinite(el.clearance.valueAsNumber) ? el.clearance.valueAsNumber : null;
    updateMeasurement(); updateSave();
  });
  on(el.photo, "click", event => {
    if (!calibration || locked()) return;
    const pixel = imagePixel(el.photo.getBoundingClientRect(), frozen.packet.source_size, event.clientX, event.clientY);
    if (pixel) { corners[corner].pixel = pixel; showCorner(); }
  });
  on(el.read, "click", async () => {
    const snapshot = frozen, index = corner;
    pending = true; showCorner();
    el.note.textContent = "현재 TCP 위치를 읽는 중입니다.";
    try {
      const data = await api("work-points/current");
      if (frozen === snapshot) {
        if (index === 0) corners[index].pose = data.pose;
        else corners[index].pose.splice(0, 3, ...data.pose.slice(0, 3));
      }
    } catch (error) {
      if (frozen === snapshot) { pending = false; showCorner(); el.note.textContent = error.message; }
      return;
    }
    if (frozen === snapshot) { pending = false; showCorner(); }
  });
  on(el.save, "click", async () => {
    if (el.save.disabled) return;
    const snapshot = frozen;
    pending = true; showCorner();
    el.note.textContent = "작업 평면을 저장하고 있습니다.";
    try {
      const saved = await api("pick/calibration", "PUT", draftCalibration());
      if (frozen !== snapshot) return;
      calibration = saved; savedCorners = saved.corners;
      clearance = saved.clearance_per_side_mm; el.clearance.value = clearance;
      corners = structuredClone(savedCorners);
      pending = false; showCorner();
      el.note.textContent = "작업 평면을 저장했습니다.";
    } catch (error) {
      if (frozen === snapshot) {
        pending = false; showCorner(); el.note.textContent = error.message;
        if (error.status === 409) ui.reload.hidden = false;
      }
    }
  });
  on(el.discard, "click", () => {
    corners = structuredClone(savedCorners);
    clearance = calibration.clearance_per_side_mm ?? 5; el.clearance.value = clearance;
    showCorner();
    el.note.textContent = "변경 사항을 취소했습니다.";
  });

  function cameraLabel(id) {
    if (deviceId === id) return;
    deviceId = id;
    ui.camera.textContent = id || (Number.isInteger(cameraIndex) ? "입력 카메라 "+cameraIndex : "—");
    if (!id) return;
    fetch("/api/camera/devices",{cache:"no-store",signal:lifetime.signal})
      .then(response => response.ok ? response.json() : [])
      .then(devices => {
        if (deviceId === id && !lifetime.signal.aborted)
          ui.camera.textContent = devices.find(d => d.id === id)?.name || id;
      }).catch(() => { /* Keep the device identifier if enumeration is unavailable. */ });
  }

  function availability() {
    ui.refresh.disabled = !frozen || pending;
    ui.reload.disabled = pending || Boolean(robotBlock);
    const cameraLocked = !calibration || locked();
    editor.querySelector("#plane-camera-settings").inert = cameraLocked;
    editor.querySelector("#plane-gripper-settings").inert = cameraLocked;
    el.surfaceGeometry.inert = cameraLocked;
    if (calibration) {
      ui.message.textContent = robotBlock || "고정한 탐지 영상에서 네 모서리를 둘레 순서로 교시하세요.";
    } else if (!pending) {
      el.note.textContent = "카메라 없이도 바닥 기준 탭에서 바닥 Z를 교시할 수 있습니다.";
    }
  }

  function clearTeaching() {
    calibration = savedCorners = null; corners = blankCorners(); corner = 0;
    measurement.reset();
    for (const canvas of [el.photo, el.measurePhoto]) canvas.getContext("2d").clearRect(0,0,canvas.width,canvas.height);
    el.frameSize.textContent = "탐지 영상 대기";
    el.measureObject.replaceChildren(); el.measureObject.disabled = true;
    el.clearance.value = "";
    showCorner();
  }

  function unavailable(message, state, camera = null) {
    frozen = null; pending = freezeNext = false;
    clearTeaching();
    ui.reload.hidden = true;
    ui.resolution.textContent = "—";
    cameraLabel(camera);
    setState(state);
    ui.message.textContent = message;
    availability();
  }

  async function load(snapshot) {
    const requestedRegion=region;
    pending = true; ui.reload.hidden = true;
    // Clear the previous scene while preserving the frozen image and its object choices.
    calibration = null; corners = blankCorners(); corner = 0;
    measurement.reset(); showCorner();
    ui.message.textContent = "현재 입력의 보정값을 불러오고 있습니다."; setState("조회 중");
    availability();
    try {
      const data = await readCalibration(snapshot.packet, lifetime.signal,requestedRegion);
      if (frozen !== snapshot||requestedRegion!==region) return;
      calibration = data;
      savedCorners = data.corners.length ? data.corners : blankCorners();
      corners = structuredClone(savedCorners);
      corner = 0;
      clearance = data.clearance_per_side_mm ?? 5;
      el.clearance.value = clearance;
      pending = false;
      showCorner();
    } catch (error) {
      if (frozen !== snapshot||requestedRegion!==region) return;
      calibration = null;
      pending = false;
      setState("조회 실패"); ui.message.textContent = error.message + " 새 영상을 고정해 다시 시도하세요.";
    }
    availability();
  }

  function capture(packet, image) {
    if (!packet.camera_id) {
      unavailable("입력 장치를 식별할 수 없습니다. 탐지 대시보드에서 카메라를 선택해 적용하세요.", "확인 불가");
      return;
    }
    const same = sameInput(packet, frozen?.packet);
    if (same && (!freezeNext || pending)) return;
    // This callback receives the decoded image for this exact frame. Copy it
    // synchronously before watchDetections installs the next JPEG.
    const {width,height} = packet.source_size;
    const photo = document.createElement("canvas");
    photo.width = width; photo.height = height;
    photo.getContext("2d").drawImage(image,0,0,width,height);
    frozen = {packet,photo};
    freezeNext = false;
    ui.resolution.textContent = width+" × "+height+" px";
    cameraLabel(packet.camera_id);
    el.frameSize.textContent = "탐지 영상 #"+packet.frame_id+" · "+width+" × "+height+" px";
    const objects = packet.objects;
    measureIndex = objects.length === 1 ? 0 : -1;
    el.measureObject.replaceChildren(new Option(objects.length ? "객체 선택" : "탐지된 객체 없음","-1"),
      ...objects.map((obj,i) => new Option((i+1)+"번 · "+obj.label+" · 중심 "+center(obj).map(Math.round).join(", ")+" px",String(i))));
    el.measureObject.value = String(measureIndex);
    el.measureObject.disabled = !objects.length;
    if (same && calibration) {
      showCorner(); availability(); // A new frame does not erase unsaved teaching.
    } else {
      calibration = null;
      measurement.reset();
      void load(frozen);
    }
  }

  on(ui.refresh,"click",() => { freezeNext = true; ui.message.textContent = "다음 탐지 영상을 기다립니다."; });
  on(ui.reload,"click",() => { if (frozen) void load(frozen); });
  const image = new Image();
  const stop = watchDetections({image, matchedPreview:true, displayMode:"detections",
    onStatus(status,label,reset) {
      cameraIndex = status.camera?.index;
      if (reset) unavailable(status.ready ? "첫 탐지 영상을 기다립니다." :
        label + " · 탐지 대시보드에서 카메라를 연결하고 탐지를 시작하세요.", "영상 대기", status.camera?.id);
    },
    onDetections(packet) { capture(packet,image); },
    onPreview(visible) { if (!visible) unavailable("탐지 영상 연결을 다시 시도합니다.", "영상 대기"); }
  });
  return {
    showWorkspace(id) {
      gripperWorkspace = id === "gripper";
      manipulatorWorkspace = id === "manipulator";
      if (manipulatorWorkspace && !manipulatorConnected) {
        connectWaypointSimulation(editor.querySelector("#manipulator-work-points"),orientation,lifetime.signal);
        connectScenario(editor.querySelector("#scenario-editor"),orientation,lifetime.signal);
        manipulatorConnected = true;
      }
      orientation.setParcelBox(manipulatorWorkspace ? parcelBox : null);
      orientation.setGripperAttached(manipulatorWorkspace);
      visibleTabs = tabs.filter(tab => manipulatorWorkspace ? tab.hasAttribute("data-manipulator-tab") :
        gripperWorkspace ? tab.hasAttribute("data-gripper-tab") : !tab.hasAttribute("data-manipulator-tab") && !tab.hasAttribute("data-gripper-tab"));
      for (const tab of tabs) tab.hidden = !visibleTabs.includes(tab);
      const current = visibleTabs.find(tab => (tab.dataset.manipulatorTab || tab.dataset.gripperTab) === location.hash);
      selectTab(current ?? visibleTabs[0]);
      editor.querySelector('[role="tablist"]').setAttribute("aria-label", gripperWorkspace ? "그리퍼" : manipulatorWorkspace ? "매니퓰레이터 설정" : "작업 평면 보정 설정");
      root.querySelector(".plane-summary").hidden = id !== "work-plane";
      availability();
    },
    robotStatus(data) {
      orientation.robotStatus(data);
      const block = !data ? "로봇 상태를 확인해야 교시할 수 있습니다." :
        data.pick_active || data.pose_tests?.busy || data.hand_busy ? "로봇 작업이 끝나면 교시할 수 있습니다." : "";
      if (robotBlock === block) return;
      robotBlock = block;
      if (calibration) showCorner();
      availability();
    },
    gripperStatus:orientation.gripperStatus,
    dispose() {
      frozen = calibration = null;
      for (const panel of livePanels) { panel.hidden = true; root.append(panel); }
      measurement.reset(); lifetime.abort(); stop(); ui.mount.replaceChildren();
    }
  };
}
