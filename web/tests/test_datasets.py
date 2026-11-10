import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.collection import Collector
from app.datasets import build_dataset, components, verify_dataset
from app.store import DomainError, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path)
    yield s
    s.close()


def add_sample(store, cls, group, seed, review="approved"):
    collector = Collector(store)
    image = np.random.default_rng(seed).integers(0,256,(80,120,3),dtype=np.uint8)
    packet = SimpleNamespace(frame_id=seed,captured_at="test",frame=image)
    config = {"id":f"collection-{group}-{seed}","class_id":cls,"group_id":group,"model_id":"teacher","model_hash":"hash"}
    if group not in {g["id"] for g in store.list("group")}:
        store.put("group", {"id": group, "name": group})
    store.put("model", {"id":"teacher", "name":"teacher"})
    store.put("collection", {**config, "state":"stopped"})
    try:
        result=collector._write(config,packet,{"session_id":"s"},{"class_id":0,"label":"old","confidence":.9,"xyxy":[20,20,100,60]})
        return store.update("sample",result["id"],review=review)
    finally:
        collector.close()


def populated(store):
    a=store.named("class","내 물체")
    b=store.named("class","다른 물체")
    for seed,(cls,group) in enumerate([(a,"a"),(a,"b"),(b,"a"),(b,"b")],1):
        add_sample(store,cls["id"],group,seed)
    return a,b


def test_approved_mapping_snapshot_and_no_group_leakage(store):
    a,b=populated(store)
    add_sample(store,a["id"],"c",50,review="excluded")
    snapshot=build_dataset(store)
    assert len(snapshot["items"])==4
    assert sorted(c["id"] for c in snapshot["classes"])==[0,1]
    by_group={}
    for item in snapshot["items"]:
        assert by_group.setdefault(item["group_id"],item["split"])==item["split"]
        label=(Path(snapshot["path"])/item["label"]).read_text().split()
        mapping=next(c["id"] for c in snapshot["classes"] if c["uuid"]==item["class_uuid"])
        assert int(label[0])==mapping
    store.update("sample",snapshot["items"][0]["sample_id"],review="excluded")
    verify_dataset(snapshot)
    assert len(json.loads((Path(snapshot["path"])/"manifest.json").read_text())["items"])==4
    (Path(snapshot["path"])/snapshot["items"][0]["label"]).write_text("changed")
    with pytest.raises(ValueError,match="modified"):verify_dataset(snapshot)


def test_near_duplicates_join_across_environment_groups(store):
    cls=store.named("class","thing")
    one=add_sample(store,cls["id"],"room1",12)
    two=add_sample(store,cls["id"],"room2",12)
    assert len(components([one,two]))==1
    with pytest.raises(DomainError,match="追加|추가|독립"):build_dataset(store)


def test_missing_file_and_insufficient_class_groups_rejected(store):
    a,b=populated(store)
    third=store.named("class","third")
    sample=add_sample(store,third["id"],"single",99)
    with pytest.raises(DomainError):build_dataset(store)
    store.update("sample",sample["id"],review="excluded")
    first=store.list("sample")[0]
    (store.root/first["image"]).unlink()
    with pytest.raises(DomainError,match="누락"):build_dataset(store)


def test_restart_recovery_and_orphan_cleanup(store):
    populated(store)
    dataset = build_dataset(store)
    store.put("job", {"id":"job1","state":"training","dataset_id":dataset["id"]})
    store.put("collection", {**store.list("collection")[0],"id":"c1","state":"collecting"})
    orphan=store.root/"collections/c1"/("sample-"+"0"*32+".jpg")
    orphan.parent.mkdir(parents=True);orphan.write_bytes(b"incomplete")
    store.recover()
    assert not orphan.exists()
    assert store.get("job","job1")["state"]=="interrupted"
    assert store.get("collection","c1")["state"]=="interrupted"


def test_one_environment_saved_then_second_approved_as_new_snapshot(store):
    cls = store.named("class", "시계")
    room = store.named("group", "실내")
    outside = store.named("group", "창가")
    for seed in (101, 102, 103):
        add_sample(store, cls["id"], room["id"], seed)
    extra = add_sample(store, cls["id"], outside["id"], 104, review="pending")
    saved = build_dataset(store)
    assert saved["status"] == "awaiting_validation"
    assert saved["counts"] == {"train": 3, "val": 0}
    assert not (Path(saved["path"])/"val.txt").read_text()
    assert extra["id"] not in {item["sample_id"] for item in saved["items"]}
    store.update("sample", extra["id"], review="approved")
    ready = build_dataset(store)
    assert ready["id"] != saved["id"] and ready["status"] == "ready"
    assert ready["counts"] == {"train": 3, "val": 1}
    assert store.get("dataset", saved["id"]) == saved
    verify_dataset(saved)
    verify_dataset(ready)


def test_missing_single_environment_label_does_not_create_snapshot(store):
    cls = store.named("class", "item")
    sample = add_sample(store, cls["id"], "only", 107)
    (store.root/sample["label"]).unlink()
    with pytest.raises(DomainError, match="누락"):
        build_dataset(store)
    assert store.list("dataset") == []


def test_split_failure_explains_duplicate_merge_across_named_environments(store):
    cls = store.named("class", "시계")
    for name in ("실내", "창가"):
        group = store.named("group", name)
        add_sample(store, cls["id"], group["id"], 105)
    with pytest.raises(DomainError) as error:
        build_dataset(store)
    detail = str(error.value)
    assert "촬영 환경 그룹 2개, 분할 가능한 독립 그룹 1개" in detail
    assert "동일·유사 사진" in detail
    assert "실내" in detail and "창가" in detail
