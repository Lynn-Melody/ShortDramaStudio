from datetime import timedelta
from decimal import Decimal
from uuid import uuid4, uuid5

import pytest
from django.contrib.auth.models import Group
from django.db import connection
from django.utils import timezone

from contracts import production_v1 as v1
from production.models import Episode, Project, Shot
from production.services import create_project
from scheduler.models import Dispatch, DispatchKind, Execution, ExecutionState, Schedule, Task, TaskState
from scheduler.service import PROVIDER_CHECK, StateConflict
from scheduler.tasks import Completed, Failed, PlanError
from scheduler.worker import Worker, configured_scheduler


pytestmark = pytest.mark.django_db(transaction=True)
IDEA = "A conductor follows the last train home."
SCENE = {"assetKey": "station", "kind": "scene", "title": "Station", "description": "An old station"}
PROP = {"assetKey": "pass", "kind": "prop", "title": "Pass", "description": "A worn ticket"}
SECOND_SCENE = {"assetKey": "street", "kind": "scene", "title": "Street", "description": "A dark street"}
CHARACTER = {"assetKey": "mei", "kind": "character", "title": "Mei", "description": "A conductor"}
LINE = {"lineId": "line-1", "speaker": "Mei", "delivery": "lip_sync", "text": "Wait!"}
VOICEOVER = {"lineId": "line-2", "speaker": "Mei", "delivery": "voiceover", "text": "Nobody heard."}
CUE = {"cueId": "cue-1", "description": "Train brakes"}


def descriptor(slot, key, kind):
    return {"slot": slot, "assetKey": key, "kind": kind, "title": key, "uploadId": str(uuid4())}


def fake_result(task_type, inp):
    data, outputs = {}, []
    episode = inp.get("episodeNumber", 0)
    shot = inp.get("shotNumber", 0)

    def shot_key(role, position=None):
        return v1.production_asset_key(episode, role, shot_number=shot, position=position)

    if task_type == "series_plan":
        data = {
            "title": "Night Service", "world": "A night train takes a secret route.",
            "visualGuide": "Cool fluorescent light", "characters": [{"name": "Mei", "profile": "Conductor"}],
            "episodes": [{"title": f"Episode {i}", "outline": "Mei boards the train"} for i in (1, 2)],
            "assets": [SCENE, SECOND_SCENE, CHARACTER],
        }
    elif task_type == "episode_script":
        data = {"script": "INT. STATION - NIGHT\nMei boards the train."}
    elif task_type == "episode_breakdown":
        data = {
            "assets": [PROP] if episode == 1 else [],
            "shots": [
                {"sceneId": scene, "description": "Mei climbs the stairs", "estimatedDurationMs": 900,
                 "assetKeys": ["pass", "mei"], "dialogue": [LINE, VOICEOVER] if i == 1 else [],
                 "sound": [] if episode == 2 and i == 2 else [CUE]}
                for i, scene in enumerate(["station", "station" if episode == 1 else "street"], 1)
            ],
        }
    elif task_type.endswith("_asset"):
        asset = inp["asset"]
        data = {"selectedSlot": "chosen"}
        outputs = [descriptor(slot, asset["assetKey"], asset["kind"]) for slot in ("candidate", "chosen")]
    elif task_type == "shot_image":
        data = {"selectedSlot": "chosen"}
        outputs = [descriptor(slot, shot_key("image"), "image") for slot in ("candidate", "chosen")]
    elif task_type == "shot_dialogue":
        data = {"takes": [
            {"lineId": "line-1", "slot": "line-1", "startMs": 100, "endMs": 2100, "durationMs": 2000},
            {"lineId": "line-2", "slot": "line-2", "startMs": 2200, "endMs": 3000, "durationMs": 800},
        ]}
        outputs = [
            descriptor(take["slot"], shot_key("dialogue", position), "audio")
            for position, take in enumerate(data["takes"], 1)
        ]
    elif task_type == "shot_video":
        data = {"selectedSlot": "main", "durationMs": inp["targetDurationMs"]}
        outputs = [descriptor("main", shot_key("video"), "video")]
    elif task_type == "shot_sound":
        data = {"takes": [{"cueId": cue["cueId"], "slot": cue["cueId"]} for cue in inp["sound"]]}
        outputs = [
            descriptor(take["slot"], shot_key("sound", position), "audio")
            for position, take in enumerate(data["takes"], 1)
        ]
    elif task_type == "episode_edit":
        outputs = [
            descriptor(slot, v1.production_asset_key(episode, slot), kind)
            for slot, kind in v1.DELIVERY_KINDS.items()
        ]
    else:
        raise AssertionError(task_type)
    return Completed({"data": data, "outputs": outputs})


def version_id(execution_id, slot):
    return str(uuid5(execution_id, slot))


def fake_register(execution, output):
    # Group rows stand in for T04's rows to verify the real transaction boundary.
    assert connection.in_atomic_block
    refs = {}
    for item in output["outputs"]:
        slot = item["slot"]
        version = version_id(execution.id, slot)
        Group.objects.create(name=version)
        refs[slot] = {"assetKey": item["assetKey"], "kind": item["kind"], "versionId": version}
    return refs


class Pipeline:
    def __init__(self):
        self.scheduler = configured_scheduler()
        self.pending = {}
        self.calls = []
        self.worker = Worker(self.scheduler, dispatch_workflow=self.accept)

    def accept(self, request):
        acceptance = self.scheduler.accept_workflow(request.execution_id)
        assert acceptance.status == "accepted"
        assert request.name == v1.LEAVES[request.task_type][0]
        assert request.definition_version == v1.VERSION
        assert v1.validate_leaf_input(request.task_type, acceptance.payload) == acceptance.payload
        self.pending[request.execution_id] = (request, acceptance.payload)
        self.calls.append((request, acceptance.payload))

    def idle(self):
        for _ in range(150):
            if not self.worker.tick():
                break
        else:
            pytest.fail("Worker did not reach idle")
        blocked = list(Task.objects.exclude(blocked_reason="").values_list("task_type", "blocked_reason"))
        assert not blocked

    def requests(self, task_type):
        return [(req, inp) for req, inp in self.pending.values() if req.task_type == task_type]

    def complete(self, request, inp):
        result = fake_result(request.task_type, inp)
        recorder = fake_register if result.output["outputs"] else None
        assert self.scheduler.submit_workflow_result(request.execution_id, result, record_outputs=recorder) == "applied"
        count = Group.objects.count()
        assert self.scheduler.submit_workflow_result(request.execution_id, result, record_outputs=fake_register) == "already_processed"
        assert Group.objects.count() == count
        self.pending.pop(request.execution_id)

    def finish_type(self, task_type, count):
        requests = self.requests(task_type)
        assert len(requests) == count
        for req, inp in requests:
            self.complete(req, inp)
        self.idle()
        return requests


def test_project_creation_replay_and_rollback(monkeypatch):
    project_id = uuid4()
    original = Project.objects.get_or_create

    def fail(*args, **kwargs):
        raise RuntimeError("write failed")

    with monkeypatch.context() as patch:
        patch.setattr(Project.objects, "get_or_create", fail)
        with pytest.raises(RuntimeError, match="write failed"):
            create_project(IDEA, project_id=project_id)
        assert not Schedule.objects.filter(pk=project_id).exists()
        assert not Task.objects.exists()

    project, root = create_project(f" {IDEA} ", project_id=project_id)
    assert original(schedule=project.schedule) == (project, False)
    assert (root.input, root.state, root.child_total) == ({"idea": IDEA}, TaskState.WAITING, 3)
    assert project.pk == project_id and not project.schedule.enabled
    assert create_project(IDEA, project_id=str(project_id)) == (project, root)
    with pytest.raises(StateConflict):
        create_project("Different", project_id=project_id)
    assert Project.objects.count() == Task.objects.count() == 1


def test_two_episodes_from_paused_idea_to_delivery(monkeypatch):
    p = Pipeline()
    project, root = create_project(IDEA)
    p.idle()
    assert not p.calls and not Execution.objects.exists()
    p.scheduler.set_enabled(project.pk, True)
    p.idle()
    p.scheduler.set_enabled(project.pk, False)
    plan_requests = p.finish_type("series_plan", 1)
    assert Episode.objects.count() == 2
    assert p.scheduler.accept_workflow(plan_requests[0][0].execution_id).status == "already_processed"
    root.refresh_from_db()
    assert abs(root.progress - Decimal(1) / 3) < Decimal("0.000000000000000000000000001")
    assert not p.requests("scene_asset")
    p.scheduler.set_enabled(project.pk, True)
    p.idle()
    scenes = p.finish_type("scene_asset", 2)
    assert not p.requests("episode_script")
    [(character, character_input)] = p.finish_type("character_asset", 1)
    assert "episodeNumber" not in character_input
    chosen_character = version_id(character.execution_id, "chosen")
    chosen_scene = next(version_id(req.execution_id, "chosen") for req, inp in scenes
                        if inp["asset"]["assetKey"] == "station")

    for number in (1, 2):
        [(_, script_in)] = p.requests("episode_script")
        assert script_in["episodeNumber"] == number
        assert script_in["characters"] == [{"name": "Mei", "profile": "Conductor"}]
        episode_task = Task.objects.get(task_type="make_episode", input__episodeNumber=number)
        assert set(episode_task.slots.values_list("key", flat=True)) == {
            "script", "breakdown", "assets", "images", "shots", "edit",
        }
        assert episode_task.children.count() == 1
        p.finish_type("episode_script", 1)
        episode_task.refresh_from_db()
        assert abs(episode_task.progress - Decimal(1) / 6) < Decimal("0.000000000000000000000000001")
        [(_, breakdown_in)] = p.finish_type("episode_breakdown", 1)
        assert breakdown_in["script"] == Episode.objects.get(project=project, number=number).script
        if number == 1:
            # The first Worker leaves a durable, unclaimed dispatch via the normal scheduler.
            [first] = p.requests("prop_asset")
            assert first[1]["episodeNumber"] == 1
            def wrong_ref(execution, output):
                refs = fake_register(execution, output)
                refs["chosen"]["assetKey"] = "wrong"
                return refs

            req, inp = first
            before = Group.objects.count()
            result = fake_result(req.task_type, inp)
            with pytest.raises(PlanError, match="does not match"):
                p.scheduler.submit_workflow_result(req.execution_id, result, record_outputs=wrong_ref)
            assert Group.objects.count() == before
            assert Execution.objects.get(pk=req.execution_id).state == ExecutionState.ACCEPTED
            p.complete(*first)
            chosen_prop = version_id(first[0].execution_id, "chosen")
            assert p.scheduler.claim_next(schedule_id=project.pk) is not None
            assert Dispatch.objects.filter(kind=DispatchKind.LOCAL, delivered_at__isnull=True).exists()
            connection.close()
            p.scheduler = configured_scheduler()
            p.worker = Worker(p.scheduler, dispatch_workflow=p.accept)
            p.idle()
        else:
            assert "pass" in {asset["assetKey"] for asset in breakdown_in["assets"]}
            batch = episode_task.slots.get(key="assets").child
            assert batch.child_total == 0 and batch.state == TaskState.COMPLETED
            assert batch.completion_reason

        images = sorted(p.requests("shot_image"), key=lambda call: call[1]["shotNumber"])
        assert len(images) == 2
        for _, inp in images:
            refs = {ref["assetKey"]: ref["versionId"] for ref in inp["assets"]}
            assert refs["pass"] == chosen_prop
            assert refs["mei"] == chosen_character
            if inp["shot"]["sceneId"] == "station":
                assert refs["station"] == chosen_scene
        p.complete(*images[0])
        p.idle()
        assert not p.requests("shot_dialogue") and not p.requests("shot_video") and not p.requests("shot_sound")
        assert not Task.objects.filter(task_type="make_shot", input__episodeNumber=number).exists()
        p.complete(*images[1])
        p.idle()
        assert len(p.requests("shot_dialogue")) == 1
        for kind in ("shot_video", "shot_sound"):
            assert [inp["shotNumber"] for _, inp in p.requests(kind)] == [2]
        empty_dialogue = Task.objects.get(task_type="shot_dialogue", input__episodeNumber=number, input__shotNumber=2)
        assert empty_dialogue.state == TaskState.COMPLETED and empty_dialogue.completion_reason
        assert not Dispatch.objects.filter(execution__task=empty_dialogue, kind=DispatchKind.WORKFLOW).exists()

        dialogue = p.finish_type("shot_dialogue", 1)[0][0]
        videos = sorted(p.requests("shot_video"), key=lambda call: call[1]["shotNumber"])
        assert len(videos) == len(p.requests("shot_sound")) == 2
        first_video, tail_video = [inp for _, inp in videos]
        assert first_video["targetDurationMs"] == 3250
        assert [cue["audio"]["versionId"] for cue in first_video["lipSync"]] == [version_id(dialogue.execution_id, "line-1")]
        assert first_video["firstFrame"]["versionId"] == version_id(images[0][0].execution_id, "chosen")
        assert first_video["lastFrame"] == (tail_video["firstFrame"] if number == 1 else None)
        assert tail_video["lastFrame"] is None
        assert tail_video["targetDurationMs"] == 900 and tail_video["lipSync"] == []
        for task in episode_task.children.get(task_type="all_shots").children.all():
            assert task.child_total == 3
            cue_task = task.slots.get(key="dialogue").child
            for branch in ("video", "sound"):
                assert task.slots.get(key=branch).child.dependencies.get().prerequisite == cue_task

        p.finish_type("shot_video", 2)
        assert not p.requests("episode_edit")
        p.finish_type("shot_sound", 2)
        [(edit, edit_input)] = p.requests("episode_edit")
        assert not p.requests("episode_script")
        assert [shot["shotNumber"] for shot in edit_input["shots"]] == [1, 2]
        assert edit_input["shots"][0]["dialogue"][1]["audio"]["versionId"] == version_id(dialogue.execution_id, "line-2")
        assert edit_input["shots"][0]["dialogue"][0]["startMs"] == 100
        assert edit_input["shots"][0]["video"]["versionId"] == version_id(videos[0][0].execution_id, "main")
        if number == 2:
            assert edit_input["shots"][1]["sound"] == []

        if number == 1:
            # Fail after both the media registrar and the production projection write.
            def fail_completion(*args):
                assert Episode.objects.get(project=project, number=1).deliveries
                raise RuntimeError("completion failed")

            result = fake_result(edit.task_type, edit_input)
            before = Group.objects.count()
            with monkeypatch.context() as patch:
                patch.setattr(p.scheduler, "_complete", fail_completion)
                with pytest.raises(RuntimeError, match="completion failed"):
                    p.scheduler.submit_workflow_result(edit.execution_id, result, record_outputs=fake_register)
            assert Group.objects.count() == before
            assert Episode.objects.get(project=project, number=1).deliveries == {}
            assert Execution.objects.get(pk=edit.execution_id).state == ExecutionState.ACCEPTED

        p.finish_type("episode_edit", 1)
        episode = Episode.objects.get(project=project, number=number)
        assert {slot: ref["versionId"] for slot, ref in episode.deliveries.items()} == {
            slot: version_id(edit.execution_id, slot) for slot in v1.DELIVERY_KINDS
        }
        assert episode.shots.count() == 2
        first_shot = episode.shots.get(number=1)
        assert first_shot.image == first_video["firstFrame"]
        assert first_shot.video["video"] == edit_input["shots"][0]["video"]
        assert first_shot.dialogue == edit_input["shots"][0]["dialogue"]
        assert first_shot.sound == edit_input["shots"][0]["sound"]
        episode_task.refresh_from_db()
        assert episode_task.state == TaskState.COMPLETED and episode_task.progress == 1

    root.refresh_from_db()
    project.refresh_from_db()
    assert root.state == TaskState.COMPLETED and root.progress == 1
    assert project.title == root.output["title"] and project.plan
    assert [item["deliveries"] for item in root.output["episodes"]] == [
        episode.deliveries for episode in project.episodes.all()
    ]
    assert Shot.objects.count() == 4
    assert not p.pending
    assert create_project(IDEA, project_id=project.pk) == (project, root)
    assert Schedule.objects.get(pk=project.pk).enabled


def test_invalid_result_blocks_before_recording_and_can_retry():
    p = Pipeline()
    project, _ = create_project(IDEA)
    p.scheduler.set_enabled(project.pk, True)
    p.idle()
    [(request, inp)] = p.requests("series_plan")
    invalid = fake_result(request.task_type, inp)
    invalid.output["data"]["episodes"] = []
    assert p.scheduler.submit_workflow_result(request.execution_id, invalid, record_outputs=fake_register) == "applied"
    task = Task.objects.get(pk=request.task_id)
    assert task.state == TaskState.WAITING and task.blocked_reason == "invalid_workflow_result"
    assert Execution.objects.get(pk=request.execution_id).state == ExecutionState.FAILED
    assert not Episode.objects.exists()
    assert not Group.objects.exists()
    project.refresh_from_db()
    assert project.plan is None
    p.scheduler.retry(project.pk, task.id)
    p.pending.pop(request.execution_id)
    p.idle()
    [(new_request, _)] = p.requests("series_plan")
    assert request.execution_id != new_request.execution_id
    assert p.scheduler.submit_workflow_result(request.execution_id, fake_result(request.task_type, inp)) == "expired"
    p.finish_type("series_plan", 1)
    assert project.episodes.count() == 2


def test_provider_busy_retries_after_confirmation_until_attempts_are_exhausted():
    p = Pipeline()
    project, _ = create_project(IDEA)
    p.scheduler.set_enabled(project.pk, True)
    p.idle()

    [(first, original_input)] = p.requests("series_plan")
    p.scheduler.register_provider_job(first.execution_id, "plan", "example", "remote-1")
    failure = Failed("provider_busy")
    assert p.scheduler.submit_workflow_result(first.execution_id, failure) == "applied"
    p.pending.pop(first.execution_id)
    task = Task.objects.get(pk=first.task_id)
    assert task.blocked_reason == PROVIDER_CHECK and task.retry_at is None
    assert p.scheduler.claim_next(schedule_id=project.pk) is None

    jobs = Execution.objects.get(pk=first.execution_id).provider_jobs
    p.scheduler.confirm_provider_jobs_inactive(first.execution_id, jobs=jobs)
    seen = {first.execution_id}
    for attempt in (2, 3):
        task.refresh_from_db()
        assert task.state == TaskState.WAITING and not task.blocked_reason
        assert task.retry_at > timezone.now()
        Task.objects.filter(pk=task.id).update(retry_at=timezone.now() - timedelta(seconds=1))
        p.idle()
        [(request, inputs)] = p.requests("series_plan")
        assert (request.task_id, inputs) == (task.id, original_input)
        assert request.execution_id not in seen
        seen.add(request.execution_id)
        assert p.scheduler.submit_workflow_result(request.execution_id, failure) == "applied"
        p.pending.pop(request.execution_id)
        task.refresh_from_db()
        assert task.attempt_count == attempt

    assert task.state == TaskState.WAITING and task.blocked_reason == "provider_busy"
    assert task.retry_at is None
    assert p.scheduler.claim_next(schedule_id=project.pk) is None
