import json
from copy import deepcopy
from pathlib import Path

import pytest
from rest_framework.exceptions import ValidationError

from contracts.production_v1 import AssetRef, LEAVES, VERSION, validate_leaf_input, validate_leaf_success, validated
from contracts.workflow_v1 import WorkflowCompleted, WorkflowUploadRequest


CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"
SAMPLES = json.loads((CONTRACTS_DIR / "production-v1.examples.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("task_type", LEAVES)
def test_published_v1_example(task_type):
    assert SAMPLES["schemaVersion"] == VERSION
    assert SAMPLES["tasks"].keys() == LEAVES.keys()
    example = SAMPLES["tasks"][task_type]
    assert example["workflow"] == LEAVES[task_type][0]
    assert validate_leaf_input(task_type, example["input"]) == example["input"]
    assert validate_leaf_success(task_type, example["input"], example["result"]) == example["result"]


def test_published_media_delivery_example():
    sample = json.loads((CONTRACTS_DIR / "media-v1.examples.json").read_text(encoding="utf-8"))
    assert sample["schemaVersion"] == VERSION
    example = sample["episodeEdit"]
    uploads = [validated(WorkflowUploadRequest, body) for body in example["uploadRequests"]]
    completed = validated(WorkflowCompleted, example["completion"])
    assert [body["slot"] for body in uploads] == [item["slot"] for item in completed["outputs"]]
    assert example["videoUploadGrant"]["uploadId"] == completed["outputs"][0]["uploadId"]
    case = SAMPLES["tasks"]["episode_edit"]
    assert validate_leaf_success("episode_edit", case["input"], {
        "data": completed["data"], "outputs": completed["outputs"],
    }) == case["result"]
    for output in completed["outputs"]:
        ref = validated(AssetRef, example["persistedDeliveryRefs"][output["slot"]])
        assert (ref["assetKey"], ref["kind"]) == (output["assetKey"], output["kind"])


@pytest.mark.parametrize(("task_type", "field", "value"), [
    ("episode_breakdown", "assets", [{"assetKey": "station", "kind": "scene", "title": "Station", "description": "New"}]),
    ("episode_breakdown", "shots", []),
    ("shot_image", "selectedSlot", "missing"),
    ("shot_dialogue", "takes", [{"lineId": "line-1", "startMs": 1, "endMs": 20, "durationMs": 1, "slot": "line-1"}]),
    ("shot_video", "durationMs", 1),
    ("shot_video", "durationMs", "1750"),
    ("episode_edit", "unexpected", True),
])
def test_invalid_result_data(task_type, field, value):
    example = deepcopy(SAMPLES["tasks"][task_type])
    example["result"]["data"][field] = value
    with pytest.raises(ValidationError):
        validate_leaf_success(task_type, example["input"], example["result"])


def test_duplicate_delivery_slot_and_wrong_kind_are_rejected():
    example = deepcopy(SAMPLES["tasks"]["episode_edit"])
    example["result"]["outputs"][1]["slot"] = "video"
    with pytest.raises(ValidationError):
        validate_leaf_success("episode_edit", example["input"], example["result"])
    example["result"]["outputs"][1]["slot"] = "subtitles"
    example["result"]["outputs"][1]["kind"] = "video"
    with pytest.raises(ValidationError):
        validate_leaf_success("episode_edit", example["input"], example["result"])


@pytest.mark.parametrize(("task_type", "wrong_key"), [
    ("shot_image", "station"),
    ("shot_image", "production:e1:s2:image"),
    ("shot_video", "production:e1:s1:image"),
    ("shot_dialogue", "production:e1:s1:sound:1"),
    ("shot_sound", "production:e1:s1:sound:2"),
    ("episode_edit", "production:e2:video"),
])
def test_derived_artifacts_reject_keys_from_other_assets(task_type, wrong_key):
    example = deepcopy(SAMPLES["tasks"][task_type])
    example["result"]["outputs"][0]["assetKey"] = wrong_key
    with pytest.raises(ValidationError, match="must match the output scope and role"):
        validate_leaf_success(task_type, example["input"], example["result"])


def test_visual_asset_cannot_use_the_reserved_production_prefix():
    inputs = deepcopy(SAMPLES["tasks"]["scene_asset"]["input"])
    inputs["asset"]["assetKey"] = "production:e1:s1:image"
    with pytest.raises(ValidationError, match="reserved for production outputs"):
        validate_leaf_input("scene_asset", inputs)


def test_shot_image_candidates_share_one_asset_key():
    example = deepcopy(SAMPLES["tasks"]["shot_image"])
    result = example["result"]
    candidate = {**result["outputs"][0], "slot": "alternate", "uploadId": "00000000-0000-4000-8000-000000000099"}
    result["outputs"].append(candidate)
    assert validate_leaf_success("shot_image", example["input"], result) == result
    candidate["assetKey"] = "production:e1:s2:image"
    with pytest.raises(ValidationError, match="must match the output scope and role"):
        validate_leaf_success("shot_image", example["input"], result)
