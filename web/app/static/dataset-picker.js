"use strict";

function datasetIsTrainable(dataset) {
  return Boolean(dataset && (dataset.status ?? "ready") === "ready" &&
    dataset.counts.train > 0 && dataset.counts.val > 0);
}

function createDatasetPicker({api, node, onChange}) {
  const el = id => document.getElementById(id);
  const dialog = el("dataset-dialog");
  const dateFormat = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"
  });
  const stamp = dataset => dateFormat.format(new Date(dataset.created_at)) + " KST";
  const status = dataset => datasetIsTrainable(dataset) ? "학습 가능" : "검증 자료 대기";
  const warning = "검증 자료 대기 · 사진과 라벨은 저장되었습니다. 같은 클래스를 다른 환경에서 추가 촬영·승인한 뒤 새 데이터셋을 고정하세요.";
  const details = new Map();
  let datasets = [], selected = null, candidateId = null, preview = null;
  let revision = 0, retry = null;

  function title(dataset) {
    const labels = dataset.classes.slice(0, 3).map(c => c.name).join(" · ");
    return labels + (dataset.classes.length > 3 ? " 외 " + (dataset.classes.length - 3) + "개" : "");
  }

  function summarize(dataset) {
    const classes = dataset.classes.map(c => ({...c, train: 0, val: 0}));
    const rows = new Map(classes.map(c => [c.uuid, c]));
    const groups = new Set();
    for (const item of dataset.items) {
      const row = rows.get(item.class_uuid);
      if (!row || !["train", "val"].includes(item.split)) throw Error("사진의 클래스 또는 분할 정보를 확인할 수 없습니다.");
      row[item.split]++;
      groups.add(item.group_id);
    }
    return {id: dataset.id, created_at: dataset.created_at, status: dataset.status,
      counts: dataset.counts, classes, groups: groups.size};
  }

  function detail(id) {
    // Snapshots are immutable. Share in-flight reads and retain just their summary.
    if (!details.has(id)) {
      const request = api("/api/datasets/" + encodeURIComponent(id)).then(summarize).catch(error => {
        details.delete(id);
        throw error;
      });
      details.set(id, request);
    }
    return details.get(id);
  }

  function select(snapshot) {
    selected = snapshot;
    el("dataset-open").textContent = "데이터셋 변경";
    el("dataset-selection-info").hidden = false;
    el("dataset-selected-title").textContent = title(snapshot);
    el("dataset-selected-meta").textContent = "고정일 " + stamp(snapshot) + " · 학습 " +
      snapshot.counts.train + "장 / 검증 " + snapshot.counts.val + "장";
    el("dataset-selected-count").textContent = snapshot.classes.length + "개";
    el("dataset-selected-classes").replaceChildren(...snapshot.classes.map(c => {
      const chip = node("span", undefined, "ds-chip");
      chip.append(node("small", "#" + c.id), document.createTextNode(c.name));
      return chip;
    }));
    el("dataset-note").textContent = datasetIsTrainable(snapshot) ? "학습 가능 · 독립 검증 자료가 있습니다." : warning;
    el("dataset-note").classList.toggle("ds-warning", !datasetIsTrainable(snapshot));
    onChange();
  }

  function feedback(text, retryAction = null) {
    preview = null;
    retry = retryAction;
    el("ds-profile").hidden = true;
    el("ds-confirm").disabled = true;
    el("ds-feedback").textContent = text;
    el("ds-retry").hidden = !retry;
  }

  function showDetail(snapshot) {
    preview = snapshot;
    const {train, val} = snapshot.counts;
    const total = train + val;
    const trainingPercent = total ? Math.round(train * 100 / total) : 0;
    el("ds-feedback").textContent = "";
    el("ds-profile").hidden = false;
    el("ds-confirm").disabled = false;
    el("ds-name").textContent = title(snapshot);
    el("ds-state").textContent = status(snapshot);
    el("ds-state").className = datasetIsTrainable(snapshot) ? "ds-ready" : "ds-wait";
    el("ds-date").textContent = "고정일 " + stamp(snapshot);
    el("ds-total").textContent = total + "장";
    el("ds-groups").textContent = snapshot.groups + "개";
    el("ds-class-count").textContent = snapshot.classes.length + "개";
    el("ds-train-bar").style.width = trainingPercent + "%";
    el("ds-train").textContent = "학습 " + train + "장 (" + trainingPercent + "%)";
    el("ds-val").textContent = "검증 " + val + "장 (" + (total ? 100 - trainingPercent : 0) + "%)";
    el("ds-rows").replaceChildren(...snapshot.classes.map(c => {
      const row = node("tr");
      const name = node("th", c.name);
      name.scope = "row";
      row.append(node("td", "#" + c.id), name, node("td", c.train + "장"), node("td", c.val + "장"));
      return row;
    }));
    el("ds-warning").hidden = datasetIsTrainable(snapshot);
    el("ds-warning").textContent = warning;
    el("ds-id").textContent = snapshot.id;
  }

  async function review(candidate) {
    const request = ++revision;
    candidateId = candidate?.id ?? null;
    for (const item of el("ds-list").children) {
      item.setAttribute("aria-current", String(item.dataset.id === candidateId));
    }
    el("ds-id-details").open = false;
    el("ds-candidate").textContent = candidate ? "검토 중　" + title(candidate) : "";
    if (!candidate) {
      feedback(datasets.length ? "일치하는 데이터셋이 없습니다." : "저장된 데이터셋이 없습니다. 데이터 검토에서 승인 자료를 먼저 고정하세요.");
      return;
    }
    feedback("상세 정보를 불러오는 중입니다.");
    try {
      const snapshot = await detail(candidate.id);
      if (dialog.open && request === revision) showDetail(snapshot);
    } catch (error) {
      if (dialog.open && request === revision) {
        feedback("상세 정보를 불러오지 못했습니다. " + error.message, () => review(candidate));
      }
    }
  }

  function filterList() {
    const q = el("ds-search").value.trim().toLowerCase();
    const matches = datasets.filter(d => d.id.toLowerCase().includes(q) ||
      d.classes.some(c => c.name.toLowerCase().includes(q)));
    el("ds-count").textContent = matches.length + "개";
    el("ds-list").replaceChildren(...matches.map(d => {
      const item = node("button", undefined, "ds-item");
      item.dataset.id = d.id;
      item.append(node("strong", title(d)), node("small", stamp(d) + " · " + (d.counts.train + d.counts.val) + "장"),
        node("span", status(d), datasetIsTrainable(d) ? "ds-ready" : "ds-wait"));
      item.addEventListener("click", () => review(d));
      return item;
    }));
    return review(matches.find(d => d.id === candidateId) || matches[0]);
  }

  async function loadList() {
    const request = ++revision;
    el("ds-search").disabled = true;
    el("ds-count").textContent = "";
    el("ds-list").replaceChildren();
    el("ds-candidate").textContent = "";
    feedback("데이터셋 목록을 불러오는 중입니다.");
    try {
      const list = await api("/api/datasets");
      if (!dialog.open || request !== revision) return;
      datasets = list.sort((a,b) => Date.parse(b.created_at) - Date.parse(a.created_at));
      el("ds-search").disabled = false;
      return filterList();
    } catch (error) {
      if (dialog.open && request === revision) feedback("목록을 불러오지 못했습니다. " + error.message, loadList);
    }
  }

  el("dataset-open").addEventListener("click", () => {
    candidateId = selected?.id ?? null;
    el("ds-search").value = "";
    dialog.showModal();
    return loadList();
  });
  el("ds-search").addEventListener("input", filterList);
  el("ds-retry").addEventListener("click", () => retry?.());
  el("ds-confirm").addEventListener("click", () => {
    if (!preview || !dialog.open) return;
    select(preview);
    dialog.close();
  });
  for (const id of ["ds-cancel", "ds-close"]) el(id).addEventListener("click", () => dialog.close());

  return {
    get selected() { return selected; },
    selectCreated(dataset) {
      const snapshot = summarize(dataset);
      details.set(dataset.id, Promise.resolve(snapshot));
      select(snapshot);
    }
  };
}
