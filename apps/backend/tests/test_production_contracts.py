import json
from copy import deepcopy
from pathlib import Path

import pytest
from rest_framework.exceptions import ValidationError

from contracts.production_v1 import LEAVES, VERSION, validate_leaf_input, validate_leaf_success


SAMPLES = json.loads(
    (Path(__file__).resolve().parents[3] / "contracts" / "production-v1.examples.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("task_type", LEAVES)
def test_published_v1_example(task_type):
    assert SAMPLES["schemaVersion"] == VERSION
    assert SAMPLES["tasks"].keys() == LEAVES.keys()
    example = SAMPLES["tasks"][task_type]
    assert example["workflow"] == LEAVES[task_type][0]
    assert validate_leaf_input(task_type, example["input"]) == example["input"]
    assert validate_leaf_success(task_type, example["input"], example["result"]) == example["result"]


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
