import { watchDetections } from "./detection-feed.js";
import { connectCameraPick } from "./camera-pick.js";

function createClassSummary(fields, getStation) {
  const buttons = new Map();
  let groups = new Map();
  let selected = null;
  let frameSize = null;
  let waiting = true;
  let cachedStation;
  let productNames = new Map();

  function displayName(packet, object) {
    const station = getStation();
    if (!station || station.model.id !== packet.model_version) return object.label;
    if (station !== cachedStation) {
      cachedStation = station;
      const products = new Map(station.products.map(p => [p.sku, p.name]));
      productNames = new Map(station.model.classes.filter(c => products.has(c.sku))
        .map(c => [c.id, {label: c.name, product: products.get(c.sku)}]));
    }
    const match = productNames.get(object.class_id);
    return match?.label === object.label ? match.product + " (" + object.label + ")" : object.label;
  }

  function drawBoxes(layer, objects) {
    while (layer.children.length > objects.length) layer.lastElementChild.remove();
    objects.forEach(({ xyxy }, i) => {
      let rect = layer.children[i];
      if (!rect) {
        rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        rect.setAttribute("vector-effect", "non-scaling-stroke");
        layer.append(rect);
      }
      const [left, top, right, bottom] = xyxy;
      rect.setAttribute("x", left);
      rect.setAttribute("y", top);
      rect.setAttribute("width", right - left);
      rect.setAttribute("height", bottom - top);
    });
  }

  function showSelection() {
    const group = groups.get(selected);
    const objects = group?.objects || [];
    for (const [label, button] of buttons) {
      const pressed = String(label === selected);
      if (button.getAttribute("aria-pressed") !== pressed) button.setAttribute("aria-pressed", pressed);
    }
    fields.overlay.toggleAttribute("hidden", !objects.length || !frameSize);
    if (objects.length && frameSize) {
      fields.overlay.setAttribute("viewBox", `0 0 ${frameSize.width} ${frameSize.height}`);
    }
    drawBoxes(fields.cutouts, objects);
    drawBoxes(fields.outlines, objects);
    let detail = "";
    if (objects.length) {
      const scores = objects.map(o => Math.round(o.confidence * 100) + "%");
      detail = group.name + " · 탐지 신뢰도 " + scores.join(", ");
    } else if (!groups.size) {
      detail = waiting ? "탐지 결과 대기 중" : "탐지된 객체가 없습니다.";
    }
    if (fields.detail.textContent !== detail) fields.detail.textContent = detail;
    fields.detail.hidden = !detail;
  }

  function selectClass(event) {
    const chip = event.target.closest("button[data-label]");
    if (!chip) return;
    selected = selected === chip.dataset.classId ? null : chip.dataset.classId;
    showSelection();
  }
  fields.chips.addEventListener("click", selectClass);

  function update(packet) {
    waiting = !packet;
    frameSize = ["detections", "rgbd"].includes(packet?.display_mode || "detections") ? packet?.source_size : null;
    groups = new Map();
    for (const object of packet?.objects || []) {
      const key = String(object.class_id);
      if (!groups.has(key)) groups.set(key, {label: object.label, name: displayName(packet, object), objects: []});
      groups.get(key).objects.push(object);
    }
    for (const [name, button] of buttons) {
      if (!groups.has(name)) { button.remove(); buttons.delete(name); }
    }
    if (!groups.has(selected)) selected = null;
    // Stable buttons keep pointer and keyboard targets in place when confidence order changes.
    const ordered = [...groups].sort((a, b) => a[1].label.localeCompare(b[1].label) || Number(a[0]) - Number(b[0]));
    ordered.forEach(([key, group], index) => {
      let button = buttons.get(key);
      if (!button) {
        button = document.createElement("button");
        button.type = "button";
        button.dataset.classId = key;
        button.dataset.label = group.label;
        button.append(document.createElement("span"), document.createElement("small"));
        buttons.set(key, button);
      }
      const quantity = group.objects.length;
      const text = "×" + quantity;
      if (button.firstElementChild.textContent !== group.name || button.lastElementChild.textContent !== text) {
        button.firstElementChild.textContent = group.name;
        button.lastElementChild.textContent = text;
        button.setAttribute("aria-label", group.name + " " + quantity + "개 강조");
      }
      const atIndex = fields.chips.children[index];
      if (atIndex !== button) fields.chips.insertBefore(button, atIndex || null);
    });
    fields.classCount.textContent = waiting ? "—" : groups.size + "종";
    showSelection();
  }
  return { update, dispose: () => fields.chips.removeEventListener("click", selectClass) };
}

function cameraSource(fields) {
  const lifetime = new AbortController();
  const { signal } = lifetime;
  let devices = [], status, draft = "", error = "", loading = false, applying = false;

  function render() {
    const current = status?.camera;
    // At startup the server uses the configured OpenCV index until a device is selected.
    const activeId = current?.id ?? devices.find(d => d.index === current?.index)?.id;
    const selected = draft || activeId;
    fields.device.value = devices.some(d => d.id === selected) ? selected : "";
    const locked = ["training", "switching_model", "switching_camera", "stopped"].includes(status?.state)
      || Boolean(status?.collection?.id);
    const offline = !current || status.state === "offline";
    fields.device.disabled = loading || applying || locked || offline || !devices.length;
    fields.refresh.disabled = loading || applying;
    fields.apply.textContent = applying ? "적용 중…" : status?.camera_enabled === false ? "탐지 시작" : "적용";
    fields.apply.disabled = fields.device.disabled || !fields.device.value ||
      (fields.device.value === activeId && status.camera_enabled && status.state !== "error");
    const note = error || (loading ? "카메라 목록 조회 중…" :
      locked ? "현재 작업이 끝나면 카메라를 변경할 수 있습니다." :
      !devices.length ? "연결된 카메라가 없습니다. 장치를 연결한 뒤 새로고침하세요." :
      draft && draft !== activeId && status?.camera_enabled ? "적용하면 선택한 카메라로 탐지를 전환합니다." : "");
    fields.notice.textContent = note;
    fields.notice.classList.toggle("error", Boolean(error));
    fields.notice.hidden = !note;
  }

  async function refresh() {
    loading = true;
    error = "";
    render();
    try {
      const response = await fetch("/api/camera/devices", {cache: "no-store", signal});
      if (!response.ok) throw new Error(response.statusText);
      devices = await response.json();
      if (!devices.some(d => d.id === draft)) draft = "";
      fields.device.replaceChildren(new Option(devices.length ? "카메라 선택" : "카메라 없음", ""),
        ...devices.map(d => new Option(d.name + " · " + d.index, d.id)));
    } catch {
      if (!signal.aborted) {
        error = "카메라 목록을 불러올 수 없습니다. 다시 조회하세요.";
        if (!devices.length) fields.device.replaceChildren(new Option("목록 조회 실패", ""));
      }
    } finally {
      loading = false;
      if (!signal.aborted) render();
    }
  }

  async function submit(event) {
    event.preventDefault();
    if (fields.apply.disabled) return;
    const id = fields.device.value;
    applying = true;
    error = "";
    render();
    try {
      const response = await fetch("/api/camera/select", {method: "POST", signal,
        headers: {"Content-Type": "application/json"}, body: JSON.stringify({device_id: id})});
      const result = await response.json();
      if (!response.ok) {
        error = result.detail || "카메라를 변경할 수 없습니다.";
        return;
      }
      status = result;
      draft = "";
    } catch {
      if (!signal.aborted) error = "카메라 변경 응답을 받지 못했습니다. 서버 연결을 확인하세요.";
    } finally {
      applying = false;
      if (!signal.aborted) render();
    }
  }

  fields.source.addEventListener("submit", submit, {signal});
  fields.device.addEventListener("change", () => { draft = fields.device.value; error = ""; render(); }, {signal});
  fields.refresh.addEventListener("click", refresh, {signal});
  refresh();
  return { update(next) { status = next; render(); }, dispose() { lifetime.abort(); } };
}

export function connectDashboardCamera(panel, getStation) {
  const fields = Object.fromEntries([...panel.querySelectorAll("[data-camera]")].map(node => [node.dataset.camera, node]));
  const classes = createClassSummary(fields, getStation);
  const source = cameraSource(fields);
  const pick = connectCameraPick(panel);
  let stop, disposed = false;
  let displayMode = "detections";

  function changeMode(next) {
    if (disposed || next === displayMode) return;
    displayMode = next;
    fields.mode.value = next;
    stop();
    stop = connect();
  }

  function modeChanged() { changeMode(fields.mode.value); }
  fields.mode.addEventListener("change", modeChanged);

  const feed = {
    image: fields.image,
    matchedPreview: true,
    onStatus(status, label, reset) {
      source.update(status);
      fields.mode.disabled = !status.ready;
      if (status.ready) {
        const modes = status.camera?.display_modes || ["detections"];
        for (const option of fields.mode.options) {
          option.disabled = !modes.includes(option.value);
          option.hidden = option.disabled && !status.camera?.mode_note && modes.length <= 2;
        }
        if (!modes.includes(displayMode)) queueMicrotask(() => changeMode("detections"));
      }
      fields.modeNote.textContent = status.camera?.mode_note || "";
      fields.modeNote.hidden = !fields.modeNote.textContent;
      if (fields.state.textContent !== label) fields.state.textContent = label;
      fields.state.classList.toggle("ready", status.ready);
      fields.fps.textContent = status.ready ? status.fps?.detected?.toFixed(1) ?? "—" : "—";
      fields.latency.textContent = status.ready ? status.capture_to_result_ms?.p95?.toFixed(0) ?? "—" : "—";
      fields.settings.hidden = !["camera_off", "camera_unavailable", "error"].includes(status.state);
      const size = status.camera?.actual_size;
      if (fields.image.hidden && size?.width && size?.height) {
        fields.image.parentElement.style.aspectRatio = size.width + "/" + size.height;
      }
      if (reset) {
        pick.update(null);
        classes.update(null);
        fields.empty.hidden = false;
        fields.message.textContent = status.last_error || (status.ready ? "첫 영상 프레임 수신 대기" : label);
        fields.count.textContent = "—";
        fields.caption.textContent = "";
        fields.caption.hidden = true;
      }
    },
    onDetections(packet, jpeg) {
      classes.update(packet);
      pick.update(packet, jpeg);
      fields.image.parentElement.style.aspectRatio = packet.source_size.width + "/" + packet.source_size.height;
      fields.count.textContent = String(packet.objects.length);
      fields.caption.textContent = [packet.model_version || packet.model, "탐지 #" + packet.frame_id].filter(Boolean).join(" · ");
      fields.caption.hidden = fields.image.hidden;
    },
    onPreview(visible) {
      fields.empty.hidden = visible;
      fields.caption.hidden = !visible || !fields.caption.textContent;
      if (!visible) {
        pick.update(null);
        fields.message.textContent = "영상 연결을 다시 시도합니다.";
        fields.count.textContent = "—";
        classes.update(null);
      }
    }
  };
  function connect() { return watchDetections({...feed, displayMode}); }
  stop = connect();
  return () => {
    disposed = true;
    fields.mode.removeEventListener("change", modeChanged);
    source.dispose();
    stop();
    pick.dispose();
    classes.dispose();
  };
}
