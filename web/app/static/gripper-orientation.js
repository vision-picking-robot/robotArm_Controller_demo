import { connectDepthPreview } from "./gripper-depth.js";

// Tool axes in BASE coordinates for the intrinsic Z, Y′, Z″ rotations.
export function toolAxes([a, b, c]) {
  const rad = Math.PI / 180;
  const ca = Math.cos(a * rad), sa = Math.sin(a * rad);
  const cb = Math.cos(b * rad), sb = Math.sin(b * rad);
  const cc = Math.cos(c * rad), sc = Math.sin(c * rad);
  return [
    [ca * cb * cc - sa * sc, sa * cb * cc + ca * sc, -sb * cc],
    [-ca * cb * sc - sa * cc, -sa * cb * sc + ca * cc, sb * sc],
    [ca * sb, sa * sb, cb]
  ];
}

// Unset fields use these angles only to illustrate the pose. Only edits/readback
// enter the calibration draft, which still requires three explicit angles to save.
const exampleAngles = [9, 103, 33];
const axisNames = ["BASE Z축", "회전된 Y축", "공구 Z축"];

export function connectOrientation(el, onAngle, signal) {
  const depth = connectDepthPreview(el, signal);
  const controls = ["A", "B", "C"].map((name, index) => {
    const row = document.createElement("div"), label = document.createElement("label");
    row.className = "pick-angle-row";
    const heading = document.createElement("div"), axis = document.createElement("b");
    heading.className = "pick-angle-label";
    const slider = document.createElement("input"), number = document.createElement("input");
    slider.type = "range"; slider.step = "0.1"; slider.id = "pick-angle-" + name;
    slider.setAttribute("aria-label", name + " 회전 슬라이더");
    number.type = "number"; number.step = "any"; number.placeholder = "—";
    number.setAttribute("aria-label", name + " 각도 직접 입력");
    axis.textContent = name;
    label.htmlFor = slider.id; label.append(axis, axisNames[index]);
    const value = document.createElement("span");
    value.className = "pick-angle-value"; value.append(number, "°");
    heading.append(label, value); row.append(heading, slider); el.angles.append(row);
    for (const input of [slider, number]) input.addEventListener("input", () => {
      const angle = input.valueAsNumber;
      onAngle(index, Number.isFinite(angle) ? angle : null, input);
    }, {signal});
    return {slider, number};
  });

  function render(corners, corner, locked, camera, editing = null) {
    const angles = corners[0].pose.slice(3);
    const complete = angles.every(Number.isFinite);
    const illustrated = angles.map((angle, i) => Number.isFinite(angle) ? angle : exampleAngles[i]);
    depth.render(corners, toolAxes(illustrated),
      illustrated.map((v, i) => "ABC"[i] + " " + v + "도").join(", ") + (complete ? "" : " (미입력 각도는 예시)"), camera, corner);
    el.angleHint.textContent = "A·B·C는 1번 모서리에 저장되고 네 모서리에 공통으로 적용됩니다." +
      (complete ? "" : " 빈 각도는 예시로 표시합니다.");
    controls.forEach(({slider, number}, i) => {
      const value = illustrated[i];
      // Keep previously taught angles outside ±180° and full readback precision.
      const limit = Math.max(180, Math.ceil(Math.abs(value) / 180) * 180);
      slider.min = -limit; slider.max = limit; slider.value = value;
      if (number !== editing) number.value = angles[i] ?? "";
      slider.disabled = number.disabled = locked;
    });
  }
  return {render, setEstimate:depth.setEstimate, setFloor:depth.setFloor, editExample:depth.editExample,
    setManipulator:depth.setManipulator,robotStatus:depth.robotStatus,
    setGripperAttached:depth.setGripperAttached,gripperStatus:depth.gripperStatus,setParcelBox:depth.setParcelBox,
    simulate:depth.simulate,simulationAction:depth.simulationAction,observeSimulation:depth.observeSimulation,
    simulateRecipe:depth.simulateRecipe,setScenarioPreview:depth.setScenarioPreview,setScenarioVisible:depth.setScenarioVisible};
}
