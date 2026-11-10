"use strict";
(() => {
  const el=id=>document.getElementById(id);
  let tab="live", ownedCollection=null, ownedObserved=false, records={models:[],jobs:[]}, backend="mps", samples=[], eventSocket;
  let refreshPending=false, dataKey="", pendingCollectionStart=null;
  const targetSku = location.search && new URLSearchParams(location.search).get("sku");
  let productLoaded = false;
  const reasons={saving:"사진 저장 중",interval:"저장 간격 대기",stabilizing:"대상 확인 중 (3프레임)",no_detection:"탐지된 물체 없음",outside_center:"물체를 중앙에 놓아주세요",ambiguous:"중앙에 물체 하나만 놓아주세요",low_confidence:"낮은 confidence",invalid_box:"잘못된 좌표",stale:"오래된 결과 · 저장 대기",queue_full:"저장 큐 포화 · 요청 생략",heartbeat_expired:"연결 유지 신호 만료 · 저장 중지",disk_error:"디스크 오류 · 저장 중지",user_stopped:"사진 저장 중지",camera_off:"카메라 꺼짐",stopped:"사진 저장 중지",server_shutdown:"서버 종료"};
  const stages={queued:"대기",preparing:"준비",training:"학습",evaluating:"평가",exporting:"Core ML 변환·비교",completed:"완료",completed_with_warnings:"PT 완료 · Core ML 확인 필요",failed:"실패",cancelled:"취소됨",cancelling:"취소 중",interrupted:"서버 재시작으로 중단"};
  const terminal=new Set(["completed","completed_with_warnings","failed","cancelled","interrupted"]);
  function message(text,error=false){el("action-message").hidden=false;el("action-message").textContent=text;el("action-message").classList.toggle("failure",error);}
  async function api(path, method = "GET", body) {
    const response = await fetch(path, {
      method, headers: body ? {"Content-Type": "application/json"} : {},
      body: body ? JSON.stringify(body) : undefined
    });
    if (!response.ok) {
      const error = await response.json().catch(() => null);
      const detail = error?.detail;
      throw Error(typeof detail === "string" ? detail : detail ? JSON.stringify(detail) :
        "서버 요청에 실패했습니다 (HTTP " + response.status + ").");
    }
    return response.json();
  }
  function action(id,handler){el(id).addEventListener("click",async()=>{el(id).disabled=true;el(id).dataset.pending="1";try{await handler();}catch(error){message(error.message,true);}finally{delete el(id).dataset.pending;el(id).disabled=id==="collect-start"&&Boolean(ownedCollection||currentStatus?.collection?.id);if(id==="train-start")updateDatasetState();}});}
  function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
  function options(id,values,label,empty){const select=el(id),old=select.value;select.replaceChildren();if(empty)select.append(new Option(empty,""));for(const v of values){const option=new Option(label(v),v.id);option.disabled=Boolean(v.disabled);select.append(option);}if([...select.options].some(o=>o.value===old&&!o.disabled))select.value=old;}
  let activeModelId = null;
  const updateManager = createModelManager({api, activate, reload: reloadModels, message});

  function modelOptions() {
    const available = records.models.map(m => ({...m, disabled: m.formats[backend]?.state !== "ready"}));
    const initiallyEmpty = !el("live-model").value;
    for (const id of ["live-model", "teacher-model"]) {
      options(id, available, m => registryLabel(m) + (m.disabled ? " (형식 없음)" : ""));
    }
    if (initiallyEmpty && activeModelId) el("live-model").value = activeModelId;
    const base = records.models.find(m => m.kind === "builtin");
    options("initial-model", records.models.filter(m => m.kind === "custom" && m.formats.mps?.state === "ready"),
      registryLabel, base ? base.name + " · 기본 가중치" : "YOLOv8n 사전 학습 가중치");
  }

  function displayActive(id) {
    const model = records.models.find(m => m.id === id);
    el("active-version").textContent = model ? registryLabel(model) : id || "";
  }

  function updateModels(snapshot) {
    const next = JSON.stringify([snapshot.active_model_id, snapshot.backend, snapshot.models]);
    if (next === dataKey) return;
    dataKey = next;
    activeModelId = snapshot.active_model_id;
    backend = snapshot.backend;
    records.models = snapshot.models;
    modelOptions();
    displayActive(activeModelId);
    updateManager(snapshot);
  }

  async function reloadModels() {
    updateModels(await api("/api/models"));
  }
  const datasetPicker = createDatasetPicker({api, node, onChange: updateDatasetState});

  function updateDatasetState() {
    el("train-start").disabled = !datasetIsTrainable(datasetPicker.selected) || el("train-start").dataset.pending === "1";
  }

  updateDatasetState();

  async function refresh() {
    if (refreshPending) return;
    refreshPending = true;
    try {
      const [classes, groups, collections, models, jobs, products] = await Promise.all([
        api("/api/classes"), api("/api/capture-groups"), api("/api/collections"),
        api("/api/models"), api("/api/training/jobs"),
        targetSku && !productLoaded ? api("/api/packing/products") : null
      ]);
      updateModels(models);
      records.jobs = jobs;
      options("class-select", classes, c => c.name);
      if (products) {
        productLoaded = true;
        const product = products.find(p => p.sku === targetSku);
        if (product) {
          const known = classes.find(c => c.name.toLowerCase() === product.name.toLowerCase());
          if (known) el("class-select").value = known.id;
          else el("class-name").value = product.name;
          el("training-product-name").textContent = product.name + " · " + product.sku;
          el("training-product-return").href = "/?sku=" + encodeURIComponent(product.sku) + "#stock";
          el("training-product").hidden = false;
        } else message("재고에서 이 상품을 찾을 수 없습니다.", true);
      }
      options("group-select", groups, g => g.name);
      options("review-session", collections.slice().reverse(), c =>
        `${c.class_name} · ${c.created_at.slice(0,16)} · ${c.saved||0}장`);
      renderJobs();
    } finally { refreshPending = false; }
  }
  function cancelCollectionStart(cameraOff=false) {
    const attempt=pendingCollectionStart;
    if(attempt){attempt.cancelled=true;attempt.cameraOff ||= cameraOff;}
    return attempt;
  }
  function showCurrentPage() {
    const route = location.hash.slice(1);
    tab = ["live", "jobs", "models"].includes(route) ? route : "collect";
    const collecting = tab === "collect";
    el("live-intro").hidden = tab !== "live";
    for (const link of document.querySelectorAll("[data-tab]")) {
      link.setAttribute("aria-current", link.dataset.tab === tab ? "page" : "false");
    }
    el("live-controls").hidden = tab === "models";
    el("vision").hidden = tab !== "live" && !collecting;
    el("collect-controls").hidden = !collecting;
    el("review-page").hidden = !collecting;
    el("jobs-page").hidden = tab !== "jobs";
    el("models-page").hidden = tab !== "models";
    el("center-roi").hidden = !collecting;
    el("candidate-box").hidden = true;
    if (!collecting) {
      cancelCollectionStart();
      if (ownedCollection) {
        const id = ownedCollection;
        ownedCollection = null;
        api(`/api/collections/${id}/stop`, "POST").then(refresh).catch(e => message(e.message, true));
      }
    }
    refresh().catch(e => message(e.message, true));
  }
  async function activate(id) {
    const operation = await api(`/api/models/${encodeURIComponent(id)}/activate`, "POST");
    message("실시간 탐지 모델을 전환하고 있습니다.");
    while (true) {
      await new Promise(resolve => setTimeout(resolve, 500));
      const state = await api(`/api/operations/${operation.id}`);
      if (state.state === "completed") {
        message("모델 적용 완료");
        return;
      }
      if (state.state !== "running") {
        const restored = records.models.find(m => m.id === state.restored_model_id);
        const recovery = restored ? " 이전 모델로 복구했습니다. " + registryLabel(restored) : "";
        const error = (state.error || "모델 적용 중단") + recovery;
        message(error, true);
        throw Error(error);
      }
    }
  }
  action("activate-model", async () => {
    const selected = el("live-model").value;
    if (selected && window.confirm("선택한 모델을 연결된 모든 화면의 실시간 탐지에 적용할까요?")) {
      await activate(selected);
      await reloadModels();
    }
  });action("camera-on",()=>api("/api/camera/start","POST"));action("camera-off",async()=>{const pending=cancelCollectionStart(true);ownedCollection=null;await api("/api/camera/stop","POST");if(pending){const failure=await pending.done;if(failure)throw failure;}message("사진 저장과 카메라를 껐습니다. 학습 후 자동 재개도 취소됩니다.");});
  action("add-class",async()=>{const c=await api("/api/classes","POST",{name:el("class-name").value});el("class-name").value="";await refresh();el("class-select").value=c.id;});
  action("add-group",async()=>{const g=await api("/api/capture-groups","POST",{name:el("group-name").value});el("group-name").value="";await refresh();el("group-select").value=g.id;});
  action("collect-start",async()=>{
    const values={class_id:el("class-select").value,group_id:el("group-select").value,
      model_id:el("teacher-model").value,interval_ms:Number(el("interval").value),confidence:Number(el("collection-conf").value)};
    if(!values.model_id||!values.class_id||!values.group_id)throw Error("클래스·환경 그룹·수집 모델을 선택하세요");
    let finish;
    const attempt={cancelled:false,cameraOff:false,cameraRequested:false,startRequested:false,id:null,
      done:new Promise(resolve=>{finish=resolve;})};
    pendingCollectionStart=attempt;
    try {
      if(currentStatus?.model_version!==values.model_id)await activate(values.model_id);
      if(attempt.cancelled)return;
      attempt.cameraRequested=true;
      await api("/api/camera/start","POST");
      if(attempt.cancelled)return;
      let ready=false;
      for(let i=0;i<60;i++){
        const status=await api("/api/status");
        if(attempt.cancelled)return;
        if(status.ready){ready=true;break;}
        if(status.state==="error")throw Error(status.last_error||"카메라/모델 오류");
        await new Promise(r=>setTimeout(r,500));
        if(attempt.cancelled)return;
      }
      if(!ready)throw Error("카메라 준비 시간 초과. 상태를 확인하고 다시 시작하세요.");
      const c=await api("/api/collections","POST",values);
      attempt.id=c.id;
      if(attempt.cancelled)return;
      attempt.startRequested=true;
      await api(`/api/collections/${c.id}/start`,"POST");
      if(attempt.cancelled)return;
      ownedCollection=c.id;ownedObserved=false;
      await refresh();el("review-session").value=c.id;
      if(!attempt.cancelled)message("학습 사진을 이 Mac에 저장합니다. 대상이 없으면 저장을 대기합니다.");
    } finally {
      let cleanupError=null;
      try {
        // A stop can overtake an already-sent start request. Stop again after its
        // response, and do not acknowledge completion until this cleanup finishes.
        if(attempt.cancelled){
          if(ownedCollection===attempt.id)ownedCollection=null;
          try {
            if(attempt.startRequested)await api(`/api/collections/${attempt.id}/stop`,"POST");
          } finally {
            if(attempt.cameraOff&&attempt.cameraRequested)await api("/api/camera/stop","POST");
          }
        }
      } catch(error) {
        cleanupError=error;
        throw error;
      } finally {
        if(pendingCollectionStart===attempt)pendingCollectionStart=null;
        finish(cleanupError);
      }
    }
  });
  action("collect-stop",async()=>{
    const pending=cancelCollectionStart();
    const id=currentStatus?.collection?.id||ownedCollection;
    // Stop renewing the lease even if the HTTP stop fails; report that failure
    // while allowing the server's ten-second expiry to stop saving.
    ownedCollection=null;
    if(id)await api(`/api/collections/${id}/stop`,"POST");
    if(pending){const failure=await pending.done;if(failure)throw failure;}
    ownedCollection=null;await refresh();
    message("사진 저장 중지 완료 · 카메라 미리보기는 계속됩니다.");
  });
  setInterval(()=>{if(ownedCollection&&tab==="collect")api(`/api/collections/${ownedCollection}/heartbeat`,"POST").catch(e=>{ownedCollection=null;message(e.message,true);});},3000);
  window.addEventListener("pagehide", () => {
    const pending = cancelCollectionStart();
    const id = ownedCollection || (pending?.startRequested && pending.id);
    ownedCollection = null;
    if (id) fetch(`/api/collections/${id}/stop`, {method:"POST", keepalive:true}).catch(() => {});
  });
  window.addEventListener("vision-status",e=>{const c=e.detail.collection;displayActive(e.detail.model_version);if(!c)return;el("collection-state").textContent=c.state==="stopping"?"저장 마무리 중":`${c.id?"수집 활성 · ":""}${reasons[c.reason]||c.reason}`;el("collection-counts").textContent=`저장 ${c.saved}장 · 대기열 ${c.queue_size}/8 · ${c.error?`오류 ${c.error} · `:""}생략 ${Object.entries(c.skipped||{}).map(([k,v])=>`${reasons[k]||k} ${v}`).join(" / ")||"없음"}`;if(ownedCollection&&c.id===ownedCollection)ownedObserved=true;if(ownedCollection&&ownedObserved&&c.id!==ownedCollection)ownedCollection=null;for(const id of ["class-select","class-name","group-select","group-name","teacher-model","interval","collection-conf","collect-start"])el(id).disabled=Boolean(c.id)||Boolean(e.detail.active_job)||el(id).dataset.pending==="1";if(!e.detail.ready)el("candidate-box").hidden=true;});
  async function loadSamples(){if(!el("review-session").value)return;samples=await api(`/api/collections/${el("review-session").value}/samples`);renderSamples();}
  function renderSamples(){el("samples").replaceChildren();el("review-summary").textContent=`${samples.length}장 · 승인 ${samples.filter(s=>s.review==="approved").length} · 제외 ${samples.filter(s=>s.review==="excluded").length}`;for(const s of samples.slice().sort((a,b)=>a.similar_group-b.similar_group)){const card=node("article",undefined,`sample ${s.review}`),figure=node("div",undefined,"sample-figure"),image=node("img"),box=node("div",undefined,"sample-box");figure.style.aspectRatio=`${s.width}/${s.height}`;image.src=s.image_url;image.loading="lazy";image.alt=`자동 라벨 검토 사진 ${s.frame_id}`;const [x1,y1,x2,y2]=s.box;Object.assign(box.style,{left:`${x1/s.width*100}%`,top:`${y1/s.height*100}%`,width:`${(x2-x1)/s.width*100}%`,height:`${(y2-y1)/s.height*100}%`});figure.append(image,box);const caption=node("div",undefined,"caption"),label=node("label"),check=node("input");check.type="checkbox";check.value=s.id;check.className="sample-check";label.append(check,document.createTextNode(`프레임 ${s.frame_id} · ${s.review}`));caption.append(label,node("small",`유사 묶음 ${s.similar_group+1} · ${(s.prediction.confidence*100).toFixed(1)}%`));const actions=node("div",undefined,"actions");for(const [text,value]of [["승인","approved"],["제외","excluded"]]){const button=node("button",text);button.onclick=()=>api(`/api/samples/${s.id}`,"PATCH",{review:value}).then(loadSamples).catch(e=>message(e.message,true));actions.append(button);}card.append(figure,caption,actions);el("samples").append(card);}}
  action("load-samples",loadSamples);action("select-samples",()=>{const checks=[...document.querySelectorAll(".sample-check")],all=checks.every(c=>c.checked);checks.forEach(c=>c.checked=!all);});
  for(const [id,review]of [["approve-samples","approved"],["exclude-samples","excluded"]])action(id,async()=>{const ids=[...document.querySelectorAll(".sample-check:checked")].map(c=>c.value);if(!ids.length)throw Error("검토할 사진을 선택하세요");for(let i=0;i<ids.length;i+=1000)await api("/api/samples/review","POST",{ids:ids.slice(i,i+1000),review});await loadSamples();});
  action("build-dataset", async () => {
    const dataset = await api("/api/datasets", "POST");
    datasetPicker.selectCreated(dataset);
    message(datasetIsTrainable(dataset)
      ? `데이터셋 고정 완료 · 학습 ${dataset.counts.train}장, 검증 ${dataset.counts.val}장`
      : `데이터셋 저장 완료 · ${dataset.counts.train}장 · 검증 자료 대기`);
    location.hash = "#jobs";
  });

  action("train-start", async () => {
    const dataset = datasetPicker.selected;
    if (!datasetIsTrainable(dataset)) throw Error("학습 가능한 데이터셋을 선택하세요.");
    const job = await api("/api/training/jobs", "POST", {
      dataset_id: dataset.id, initial_model_id: el("initial-model").value || null,
      batch: Number(el("train-batch").value), epochs: Number(el("train-epochs").value),
      patience: Number(el("train-patience").value)
    });
    message("학습 시작: " + job.id);
    await refresh();
  });
  function renderJobs(){el("jobs-list").replaceChildren();for(const j of records.jobs.slice().reverse()){const card=node("article",undefined,"record");card.append(node("h3",`${stages[j.state]||j.state} · ${j.created_at.slice(0,19)}`),node("p",j.id,"hint"));const p=j.progress;if(p){const bar=node("progress");bar.max=p.epochs||1;bar.value=p.epoch||0;card.append(bar,node("p",`epoch ${p.epoch??"—"}/${p.epochs??j.epochs} · batch ${j.batch}`),node("pre",JSON.stringify({loss:p.losses,metrics:p.metrics},null,2)));}if(j.error||j.warning)card.append(node("p",j.error||j.warning,"error"));const actions=node("div",undefined,"controls"),log=node("button","로그 보기");log.onclick=()=>api(`/api/training/jobs/${j.id}/log`).then(v=>{el("job-log").hidden=false;el("job-log").textContent=Object.entries(v).map(([k,text])=>`${k}\n${text}`).join("\n");}).catch(e=>message(e.message,true));actions.append(log);if(!terminal.has(j.state)){const cancel=node("button","작업 취소","danger");cancel.onclick=()=>api(`/api/training/jobs/${j.id}/cancel`,"POST").then(refresh).catch(e=>message(e.message,true));actions.append(cancel);}card.append(actions);el("jobs-list").append(card);}}
  function events() {
    eventSocket = new WebSocket(`${location.protocol==="https:"?"wss":"ws"}://${location.host}/ws/events`);
    eventSocket.onmessage = event => {
      try {
        const data = JSON.parse(event.data);
        records.jobs = data.jobs;
        renderJobs();
        updateModels({models: data.models, active_model_id: data.status.model_version, backend: data.status.backend});
      } catch (error) { console.error(error); }
    };
    eventSocket.onclose = () => setTimeout(events, 2000);
    eventSocket.onerror = () => eventSocket.close();
  }
  window.addEventListener("hashchange", () => {
    showCurrentPage();
    window.scrollTo(0, 0);
  });
  showCurrentPage();
  events();
})();
