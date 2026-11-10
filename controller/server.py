"""Demo Robot Controller REST API Server."""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional

from .demo_controller import DemoRobotController

app = FastAPI(title="Doosan E0509 Demo Robot Controller", version="1.0.0")
controller = DemoRobotController()


class PickCommand(BaseModel):
    product_id: str = Field(default="snack_box", description="Product identifier (e.g. snack_box, pet_bottle, can)")
    base_xy: List[float] = Field(description="Base coordinates [x, y] in mm converted from YOLO vision")
    slot_id: int = Field(default=1, ge=1, le=5, description="Box slot index")
    request_id: Optional[str] = Field(default="DEMO-REQ-001", description="Unique request ID")


@app.get("/healthz")
def health():
    return {"status": "ok", "service": "robot-demo-controller"}


@app.get("/api/demo/status")
def status():
    return {
        "busy": controller.busy,
        "floor_limit_z_min": controller.floor_limit,
        "current_posx": controller.backend.current_posx,
        "current_posj": controller.backend.current_posj,
        "gripper": {
            "mode": controller.gripper.mode,
            "connected": controller.gripper.connected,
            "position": controller.gripper.current_position,
            "current_ma": controller.gripper.current_ma
        },
        "poses_loaded": list(controller.poses.keys()),
        "products_loaded": list(controller.products.keys())
    }


@app.post("/api/demo/gripper/test")
def test_gripper():
    result = controller.gripper.run_self_test(timeout_sec=5.0)
    return result


@app.post("/api/demo/pick")
def execute_pick(cmd: PickCommand):
    if controller.busy:
        raise HTTPException(status_code=409, detail="Controller is busy with another motion sequence")
    result = controller.execute_pick_sequence(
        product_id=cmd.product_id,
        base_xy=cmd.base_xy,
        slot_id=cmd.slot_id,
        request_id=cmd.request_id
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Execution failed"))
    return result


@app.get("/api/demo/trace")
def get_trace():
    return {
        "count": len(controller.trace),
        "events": controller.trace
    }


@app.delete("/api/demo/trace")
def clear_trace():
    controller.trace.clear()
    return {"ok": True}
