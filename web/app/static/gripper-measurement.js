// The teaching view and confirmation use the same server result and units.
export function describeGripper(estimate) {
  if (!Number.isFinite(estimate?.opening_mm)) {
    return "권장 벌림 — · " + (estimate?.reason || "탐지 폭을 측정할 수 없습니다.");
  }
  const mm = value => value.toFixed(1) + " mm";
  return "탐지 상자 기준 추정 폭 " + mm(estimate.width_mm) +
    " · 한쪽 여유 " + mm(estimate.clearance_per_side_mm) + " · 권장 벌림 " + mm(estimate.opening_mm);
}

// Slider edits can overtake an older response. Clear the obsolete drawing at
// once and publish only the result for the current draft and selected instance.
export function trackGripperEstimate(request, publish) {
  let key, timer, controller;
  function reset() {
    key = null;
    clearTimeout(timer);
    controller?.abort();
  }
  function update(draft, object, index, reason) {
    // A save changes the revision but not the dimensions of the same drawing.
    const body = reason ? null : {calibration:{...draft, revision:null}, xyxy:object.xyxy};
    const next = JSON.stringify([index,body,reason]);
    if (key === next) return;
    reset();
    key = next;
    publish({reason:reason || "탐지 상자 폭을 계산하는 중입니다."});
    if (!body) return;
    const current = controller = new AbortController();
    timer = setTimeout(async () => {
      try {
        const estimate = await request(body, current.signal);
        if (!current.signal.aborted) publish(estimate);
      } catch (error) {
        if (!current.signal.aborted) {
          publish({reason:error.message});
          key = null;
        }
      }
    }, 90);
  }
  return {update, reset};
}
