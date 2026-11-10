"""Immutable approved-data snapshots; environment/near-duplicate connected components never leak."""
import itertools
import json
import random
import shutil
from collections import defaultdict

from .store import DomainError, atomic_json, now, sha256, uid


def components(samples):
    parent = list(range(len(samples)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    def union(i, j):
        parent[root(j)] = root(i)
    groups, hashes = {}, {}
    # dHash Hamming <= 6 is a conservative duplicate heuristic, not a semantic identity detector.
    for i, sample in enumerate(samples):
        for field, lookup in (("group_id", groups), ("sha256", hashes)):
            key = sample[field]
            if key in lookup:
                union(i, lookup[key])
            lookup[key] = i
        for j in range(i):
            if (int(sample["dhash"], 16) ^ int(samples[j]["dhash"], 16)).bit_count() <= 6:
                union(i, j)
    result = defaultdict(list)
    for i, sample in enumerate(samples):
        result[root(i)].append(sample)
    return list(result.values())


def split_components(groups):
    classes = {s["class_id"] for g in groups for s in g}
    if len(groups) < 2:
        raise DomainError("서로 다른 배경·각도에서 추가 촬영하세요. 환경 그룹/유사 사진을 분리할 수 없습니다.", 422)
    sets = [{s["class_id"] for s in g} for g in groups]
    for cls in classes:
        if sum(cls in s for s in sets) < 2:
            raise DomainError("각 클래스에 독립적인 환경 그룹이 2개 이상 필요합니다. 추가 촬영 후 승인하세요.", 422)
    n, total = len(groups), sum(map(len, groups))
    if n <= 17:
        choices = itertools.chain.from_iterable(itertools.combinations(range(n), count) for count in range(1, n))
    else:
        choices = large_split_choices(groups, sets, classes)
    best, score = None, float("inf")
    for choice in choices:
        selected = set(choice)
        val_classes = set().union(*(sets[i] for i in selected)) if selected else set()
        train_classes = set().union(*(sets[i] for i in range(n) if i not in selected))
        if val_classes != classes or train_classes != classes:
            continue
        candidate = abs(sum(len(groups[i]) for i in selected)/total - .2)
        if candidate < score:
            best, score = selected, candidate
            if score == 0:
                break
    if best is None:
        raise DomainError("그룹을 분리하면서 모든 클래스를 양쪽에 배치할 수 없습니다. 독립 환경을 추가하세요.", 422)
    return [(s, "val" if i in best else "train") for i, group in enumerate(groups) for s in group]


def large_split_choices(groups, sets, classes):
    """Cover every validation class while preserving training coverage first.

    Pure Bernoulli draws have exponentially small success probabilities when many
    classes each have only two environments, even though a valid split exists.
    """
    rng = random.Random(42)
    members = {cls: [i for i, group in enumerate(sets) if cls in group] for cls in sorted(classes)}
    total = sum(map(len, groups))
    for _ in range(256):
        selected, covered = set(), set()
        remaining = {cls: len(indices) for cls, indices in members.items()}
        order = list(members)
        rng.shuffle(order)
        order.sort(key=lambda cls: len(members[cls]))
        for cls in order:
            if cls in covered:
                continue
            candidates = [i for i in members[cls] if all(remaining[c] > 1 for c in sets[i])]
            if not candidates:
                break
            chosen = rng.choice(candidates)
            selected.add(chosen)
            covered.update(sets[chosen])
            for c in sets[chosen]:
                remaining[c] -= 1
        if covered != classes:
            continue
        yield tuple(selected)
        size = sum(len(groups[i]) for i in selected)
        extras = [i for i in range(len(groups)) if i not in selected]
        rng.shuffle(extras)
        for i in extras:
            if (all(remaining[c] > 1 for c in sets[i]) and
                    abs((size+len(groups[i]))/total-.2) < abs(size/total-.2)):
                selected.add(i)
                size += len(groups[i])
                for c in sets[i]:
                    remaining[c] -= 1
        yield tuple(selected)
    for _ in range(10000):
        yield tuple(i for i in range(len(groups)) if rng.random() < .2)


def split_failure_message(store, samples, groups, classes, reason):
    """Explain the approved data that actually participates in splitting."""
    environments = {g["id"]: g["name"] for g in store.list("group")}
    environment_count = len({s["group_id"] for s in samples})
    parts = [str(reason),
             f"현재 승인 자료: 사진 {len(samples)}장, 촬영 환경 그룹 {environment_count}개, "
             f"분할 가능한 독립 그룹 {len(groups)}개."]
    insufficient = []
    for cls in classes:
        selected = [s for s in samples if s["class_id"] == cls["id"]]
        independent = sum(any(s["class_id"] == cls["id"] for s in group) for group in groups)
        if independent < 2:
            ids = sorted({s["group_id"] for s in selected})
            names = ", ".join(environments.get(identifier, identifier) for identifier in ids)
            insufficient.append(f"'{cls['name']}' (승인 {len(selected)}장, 환경 {len(ids)}개: {names}, 독립 그룹 {independent}개)")
    if insufficient:
        parts.append("추가 촬영이 필요한 클래스: " + "; ".join(insufficient) + ".")
    if len(groups) < environment_count:
        parts.append("서로 다른 환경의 동일·유사 사진이 연결되어 환경 그룹들이 하나로 묶였습니다.")
    parts.append("같은 클래스를 선택한 채 실제 배경·조명·각도를 바꾸고 '새 환경 등록' 후 추가 촬영·승인하세요. "
                 "각 클래스에 최소 2개의 독립 환경 자료가 필요합니다. 그룹 이름만 추가하거나 사진을 복제하는 것으로는 해결되지 않습니다.")
    return " ".join(parts)


def build_dataset(store):
    samples = [s for s in store.list("sample") if s["review"] == "approved"]
    if not samples:
        raise DomainError("승인된 사진이 없습니다", 422)
    classes = sorted([c for c in store.list("class") if any(s["class_id"] == c["id"] for s in samples)], key=lambda c: c["id"])
    mapping = [{"id": i, "uuid": c["id"], "name": c["name"]} for i, c in enumerate(classes)]
    lookup = {c["uuid"]: c["id"] for c in mapping}
    if len({s["group_id"] for s in samples}) == 1:
        # Preserve this collection now, without presenting related frames as
        # independent validation data. A later save creates a separate snapshot.
        assigned = [(sample, "train") for sample in samples]
        status = "awaiting_validation"
        policy = "single capture environment; all approved samples in train; no validation samples"
    else:
        groups = components(samples)
        try:
            assigned = split_components(groups)
        except DomainError as exc:
            raise DomainError(split_failure_message(store, samples, groups, classes, exc), exc.status) from exc
        status = "ready"
        policy = "environment + sha256 + dHash Hamming <= 6 connected components; target 80:20"
    identifier = uid("dataset")
    directory = store.root / "datasets" / identifier
    items = []
    try:
        for sample, split in assigned:
            source, label_source = store.root/sample["image"], store.root/sample["label"]
            if not source.is_file() or not label_source.is_file() or sha256(source) != sample["sha256"] or sha256(label_source) != sample["label_sha256"]:
                raise DomainError("승인 자료가 누락되거나 변경되었습니다", 422)
            image = directory / "images" / split / source.name
            label = directory / "labels" / split / label_source.name
            image.parent.mkdir(parents=True, exist_ok=True)
            label.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, image)
            label.write_text(str(lookup[sample["class_id"]]) + " " + " ".join(f"{v:.10f}" for v in sample["normalized_box"]) + "\n")
            items.append({"sample_id": sample["id"], "group_id": sample["group_id"], "class_uuid": sample["class_id"],
                          "split": split, "image": str(image.relative_to(directory)), "label": str(label.relative_to(directory)),
                          "sha256": sha256(image), "label_sha256": sha256(label)})
        manifest = {"id": identifier, "created_at": now(), "classes": mapping, "items": items,
                    "status": status, "split_policy": policy,
                    "counts": {s: sum(i["split"] == s for i in items) for s in ("train", "val")}}
        atomic_json(directory/"manifest.json", manifest)
        split_hashes = {}
        for split in ("train", "val"):
            listing = directory / f"{split}.txt"
            listing.write_text("".join(str(directory/item["image"]) + "\n" for item in items if item["split"] == split))
            split_hashes[split] = sha256(listing)
        # JSON is a YAML subset; safe for Unicode names and absolute paths.
        atomic_json(directory/"data.yaml", {"path": str(directory), "train": "train.txt", "val": "val.txt",
                                           "names": [c["name"] for c in mapping]})
        return store.put("dataset", {**manifest, "path": str(directory), "manifest_sha256": sha256(directory/"manifest.json"),
                                     "yaml_sha256": sha256(directory/"data.yaml"), "split_sha256": split_hashes})
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def require_training_split(dataset):
    # Older snapshots have counts but predate the status field.
    counts = dataset.get("counts", {})
    if dataset.get("status", "ready") != "ready" or min(counts.get("train", 0), counts.get("val", 0)) < 1:
        raise DomainError("데이터셋은 저장되어 있습니다. 학습하려면 같은 클래스의 다른 환경 사진을 촬영·승인한 뒤 새 데이터셋을 고정하세요.", 422)


def verify_dataset(dataset):
    from pathlib import Path
    root = Path(dataset["path"])
    for name, key in (("manifest.json", "manifest_sha256"), ("data.yaml", "yaml_sha256")):
        if sha256(root/name) != dataset[key]:
            raise ValueError("Dataset snapshot was modified")
    for split, expected in dataset.get("split_sha256", {}).items():
        if sha256(root/f"{split}.txt") != expected:
            raise ValueError("Dataset snapshot file list was modified")
    for item in dataset["items"]:
        if sha256(root/item["image"]) != item["sha256"] or sha256(root/item["label"]) != item["label_sha256"]:
            raise ValueError("Dataset snapshot files were modified")
