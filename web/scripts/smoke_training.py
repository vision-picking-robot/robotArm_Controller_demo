"""Isolated end-to-end MPS/Core ML smoke test using single-object COCO128 examples."""
import json
import time
from pathlib import Path
from types import SimpleNamespace

from app.config import ROOT, Settings
from app.pipeline import Pipeline
from app.runtime import Runtime, TERMINAL
from app.store import atomic_json, uid


def main():
    import cv2
    root = ROOT/"artifacts"/uid("training-smoke")
    runtime = Runtime(Settings(backend="mps"), Pipeline, root)
    runtime.camera_desired = False  # This test never opens the camera.
    try:
        cls = runtime.store.named("class", "검증_물체")
        selected = []
        for label in sorted((ROOT/"data/coco128/labels/train2017").glob("*.txt")):
            lines = label.read_text().strip().splitlines()
            image = ROOT/"data/coco128/images/train2017"/(label.stem+".jpg")
            if len(lines)==1 and image.exists():
                selected.append((image,lines[0]))
            if len(selected)==10:break
        if len(selected)<10:raise RuntimeError("Prepare COCO128 first")
        for i,(path,line) in enumerate(selected):
            frame=cv2.imread(str(path));h,w=frame.shape[:2]
            _,cx,cy,bw,bh=map(float,line.split())
            box=[max(0,(cx-bw/2)*w),max(0,(cy-bh/2)*h),min(w,(cx+bw/2)*w),min(h,(cy+bh/2)*h)]
            group=runtime.store.named("group",f"public-test-scene-{i}")
            collection={"id":uid("collection"),"class_id":cls["id"],"group_id":group["id"],"model_id":"coco-yolov8n","model_hash":"public-label-test","state":"stopped","created_at":"smoke"}
            runtime.store.put("collection",collection)
            sample=runtime.collector._write(collection,SimpleNamespace(frame_id=i,captured_at="smoke",frame=frame),
                {"session_id":"smoke"},{"xyxy":box,"confidence":1.,"label":"test","class_id":0})
            runtime.store.update("sample",sample["id"],review="approved")
        dataset=runtime.dataset()
        job=runtime.create_job({"dataset_id":dataset["id"],"initial_model_id":None,"batch":4,"epochs":1,"patience":1})
        print(json.dumps({"root":str(root),"job_id":job["id"],"dataset_counts":dataset["counts"]}),flush=True)
        previous=None
        while True:
            current=runtime.store.get("job",job["id"])
            if current["state"]!=previous:
                print(current["state"],current.get("error"),flush=True);previous=current["state"]
            if current["state"] in TERMINAL:break
            time.sleep(1)
        result={"purpose":"Functional smoke test only; ten public single-object examples relabeled 검증_물체, not user object accuracy",
                "root":str(root),"job":current,"models":runtime.store.list("model")}
        atomic_json(root/"smoke-result.json",result)
        if current["state"]!="completed":raise RuntimeError(str(current))
        print("Smoke test completed:",root/"smoke-result.json",flush=True)
    finally:
        runtime.stop()


if __name__=="__main__":main()
