"use strict";

function registryVersion(model) {
  if (model.version_number) return "v" + model.version_number;
  if (model.kind === "builtin") return "기본";
  return (model.kind === "configured" ? "외부 모델" : "버전 미기록") + " · " + model.id.slice(-8);
}

function registryLabel(model) {
  return model.name + " · " + registryVersion(model);
}

function createModelManager({api, activate, reload, message}) {
  const byId = id => document.getElementById(id);
  const dialog = byId("model-dialog");
  const dateFormat = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23"
  });
  const backends = {mps: "MPS", coreml: "Core ML", cpu: "CPU"};
  const kinds = {custom: "사용자 모델", builtin: "기본 모델", configured: "외부 모델"};
  let models = [], selectedId, activeId, backend, editId, mode, submitting = false;
  const find = id => models.find(model => model.id === id);
  function element(tag, text, className) {
    const item = document.createElement(tag);
    item.textContent = text;
    if (className) item.className = className;
    return item;
  }

  function select(id) {
    if (id !== selectedId) {
      selectedId = id;
      byId("class-search").value = "";
      byId("model-history").open = false;
    }
    render();
  }

  function renderClasses() {
    const classes = find(selectedId)?.classes || [];
    const search = byId("class-search");
    search.hidden = classes.length < 10;
    const query = search.hidden ? "" : search.value.trim().toLowerCase();
    const chips = classes.filter(c => c.name.toLowerCase().includes(query) || String(c.id) === query)
      .map(c => {
        const chip = element("span", "", "registry-chip");
        chip.append(element("small", "#" + c.id), document.createTextNode(c.name));
        return chip;
      });
    byId("class-count").textContent = classes.length ? classes.length + "개" : "";
    const container = byId("model-classes");
    container.replaceChildren(...chips);
    if (!chips.length) container.textContent = classes.length ? "일치하는 클래스가 없습니다." : "클래스 미확인";
  }

  function renderFormats(model) {
    const table = byId("model-formats");
    table.replaceChildren();
    const keys = backend === "cpu" ? ["mps", "coreml", "cpu"] : ["mps", "coreml"];
    for (const key of keys) {
      const format = model.formats?.[key];
      // User PT files can run on CPU, but their stored benchmark was measured on MPS.
      const measured = key !== "cpu" || format?.runtime?.backend === "pytorch-cpu" || model.kind === "builtin";
      const map = measured ? format?.metrics?.map50_95 : null;
      const p95 = measured ? format?.latency?.predict_ms?.p95 : null;
      const row = element("tr", "");
      const state = format?.state;
      const stateText = state === "ready" ? "사용 가능" : format?.error ? "검증 실패" : state ? "사용 불가" : "형식 없음";
      const stateCell = element("td", stateText, state === "ready" ? "usable" : "");
      if (format?.error) stateCell.append(element("small", format.error, "registry-error"));
      row.append(element("th", backends[key] + (key === "coreml" ? " · ML Package" : " · PT")), stateCell,
        element("td", Number.isFinite(map) ? (100 * map).toFixed(1) + "%" : "미측정"),
        element("td", Number.isFinite(p95) ? p95.toFixed(1) + " ms" : "미측정"));
      table.append(row);
    }
    const benchmark = Object.values(model.formats || {}).find(f => f.evaluation_dataset);
    const dataset = model.dataset_id ? "사용자 검증 세트 · " + model.dataset_id : benchmark?.evaluation_dataset;
    byId("model-evaluation").textContent = "평가 데이터셋 · " + (dataset || "미기록");
  }

  function renderMetadata(model) {
    const values = byId("model-metadata");
    values.replaceChildren();
    function add(label, value) {
      values.append(element("dt", label), element("dd", value ?? "미기록"));
    }
    add("모델 ID", model.id);
    if (model.kind === "custom") {
      add("학습 작업", model.job_id);
      const counts = model.dataset_counts;
      add("데이터 분할", counts ? "학습 " + counts.train + "장 / 검증 " + counts.val + "장" : null);
      const config = model.training?.actual || model.training?.requested;
      if (config) {
        add("학습 설정", ["입력 " + config.imgsz, "batch " + config.batch, "최대 " + config.epochs + " epochs",
          "patience " + config.patience].join(" · "));
      }
      add("초기 가중치 SHA-256", model.initial_sha256);
    }
    add("PT SHA-256", model.formats?.mps?.sha256 || model.formats?.cpu?.sha256);
    add("Core ML SHA-256", model.formats?.coreml?.sha256);
    if (model.kind === "custom") {
      const raw = element("details", "");
      raw.append(element("summary", "평가·변환·환경 상세"), element("pre", JSON.stringify({
        training: model.training, environment: model.environment, formats: model.formats
      }, null, 2)));
      const extra = element("dd", "");
      extra.append(raw);
      values.append(element("dt", "실행 기록"), extra);
    }
  }

  function render() {
    const list = byId("models-list");
    const focused = document.activeElement?.dataset?.modelId;
    list.replaceChildren();
    for (const m of [...models].reverse()) {
      const button = element("button", "", "registry-item");
      button.dataset.modelId = m.id;
      button.setAttribute("aria-current", String(m.id === selectedId));
      const caption = m.kind === "builtin" ? "YOLOv8n · 기본 모델" : registryVersion(m);
      button.append(element("strong", m.name), element("small", caption));
      if (m.id === activeId) button.append(element("span", "적용 중", "registry-current"));
      button.onclick = () => select(m.id);
      list.append(button);
      if (m.id === focused) button.focus({preventScroll: true});
    }
    byId("registry-count").textContent = models.length + "개";
    const active = find(activeId);
    byId("registry-active").textContent = active ? "현재 적용 · " + registryLabel(active) + " · " + backends[backend] : "적용 모델 확인 중";
    const model = find(selectedId);
    byId("model-profile").hidden = !model;
    if (!model) return;
    byId("model-name").textContent = model.name;
    byId("model-version").textContent = registryVersion(model);
    byId("model-kind").textContent = (kinds[model.kind] || "모델") + (activeId === model.id ? " · 현재 적용 중" : "");
    const date = model.kind === "custom" && model.created_at ? new Date(model.created_at) : null;
    byId("model-date").textContent = date && Number.isFinite(date.valueOf()) ? dateFormat.format(date) : "미기록";
    byId("model-architecture").textContent = ["custom", "builtin"].includes(model.kind) ? "YOLOv8n" : "미확인";
    const parent = find(model.parent_model_id);
    const link = byId("model-parent");
    link.replaceChildren();
    if (parent) {
      const button = element("button", registryLabel(parent) + " ↗", "registry-link");
      button.onclick = () => select(parent.id);
      link.append(button);
    } else {
      link.textContent = model.kind === "builtin" ? "YOLOv8n · COCO 사전 학습" : "미확인";
    }
    renderClasses();
    renderFormats(model);
    renderMetadata(model);
    const apply = byId("use-version");
    apply.disabled = submitting || model.id === activeId || model.formats?.[backend]?.state !== "ready";
    apply.textContent = backends[backend] + (model.id === activeId ? "에 적용 중" : "에 적용");
  }

  function openDialog(action) {
    const model = find(selectedId);
    if (!model || submitting) return;
    editId = model.id;
    mode = action;
    const rename = mode === "rename";
    byId("model-dialog-title").textContent = rename ? "모델 이름 변경" : "실시간 탐지에 적용";
    byId("model-dialog-note").textContent = rename ? registryVersion(model) + "의 표시 이름을 입력하세요." :
      registryLabel(model) + " 모델을 " + backends[backend] + "에 적용합니다. 연결된 모든 화면의 탐지 모델이 바뀝니다.";
    byId("model-name-field").hidden = !rename;
    byId("model-new-name").value = model.name;
    byId("model-form-error").textContent = "";
    byId("submit-model-change").textContent = rename ? "저장" : "적용";
    dialog.showModal();
    if (rename) byId("model-new-name").select();
    else byId("cancel-model-change").focus();
  }
  byId("class-search").addEventListener("input", renderClasses);
  byId("rename-model").addEventListener("click", () => openDialog("rename"));
  byId("use-version").addEventListener("click", () => openDialog("apply"));
  byId("cancel-model-change").addEventListener("click", () => dialog.close());
  dialog.addEventListener("cancel", event => { if (submitting) event.preventDefault(); });
  byId("model-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (submitting) return;
    const name = byId("model-new-name").value.trim();
    if (mode === "rename" && (!name || Array.from(name).length > 80)) {
      byId("model-form-error").textContent = "표시 이름을 1~80자로 입력하세요.";
      byId("model-new-name").focus();
      return;
    }
    submitting = true;
    byId("submit-model-change").disabled = true;
    byId("cancel-model-change").disabled = true;
    try {
      if (mode === "rename") {
        await api("/api/models/" + encodeURIComponent(editId), "PATCH", {name});
        message("표시 이름을 저장했습니다.");
      } else {
        await activate(editId);
      }
      dialog.close();
    } catch (error) {
      byId("model-form-error").textContent = error.message;
    } finally {
      submitting = false;
      byId("submit-model-change").disabled = false;
      byId("cancel-model-change").disabled = false;
      await reload().catch(error => message(error.message, true));
    }
  });

  return function update(snapshot) {
    models = snapshot.models;
    backend = snapshot.backend;
    activeId = snapshot.active_model_id;
    if (!find(selectedId)) selectedId = find(activeId)?.id || models.at(-1)?.id;
    render();
  };
}
