import json
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone
from rest_framework.test import APIClient

from contracts import public_v1 as v1
from media.models import AssetItem, AssetVersion, ExecutionUpload
from media.workflow_outputs import _record_outputs
from production.models import Episode, Project
from scheduler.models import Execution, Schedule, Task, TaskState
from scheduler.service import PROVIDER_CHECK
from scheduler.tasks import Failed
from scheduler.worker import Worker, configured_scheduler
from test_production import IDEA, fake_result


pytestmark = pytest.mark.django_db(transaction=True)
EXAMPLES_PATH = Path(__file__).resolve().parents[3] / "contracts/public-v1.examples.json"
CONTENT_TYPES = {
    "character": "image/png", "scene": "image/png", "prop": "image/png", "image": "image/png",
    "audio": "audio/mpeg", "video": "video/mp4", "subtitles": "text/vtt", "timeline": "application/json",
}
PROJECTS = "/api/v1/projects"


def project_path(project_id, suffix=""):
    return f"{PROJECTS}/{project_id}{suffix}"


class Studio:
    """Drive the real production graph with fixed workflow results and T04 media registration."""

    def __init__(self, *, accept=True):
        self.scheduler = configured_scheduler()
        self.pending = {}
        self.worker = Worker(self.scheduler, dispatch_workflow=self.accept if accept else lambda request: None)

    def accept(self, request):
        acceptance = self.scheduler.accept_workflow(request.execution_id)
        assert acceptance.status == "accepted"
        self.pending[request.execution_id] = (request, acceptance.payload)

    def idle(self):
        for _ in range(200):
            if not self.worker.tick():
                return
        pytest.fail("Worker did not reach idle")

    def prepare(self, execution, output):
        uploads = {}
        for item in output["outputs"]:
            uploads[item["uploadId"]] = ExecutionUpload.objects.create(
                id=item["uploadId"], execution=execution, slot=item["slot"], bucket="studio-test",
                object_key=f"projects/{execution.task.schedule_id}/executions/{execution.pk}/{item['uploadId']}",
                content_type=CONTENT_TYPES[item["kind"]], size_bytes=1024,
            )
        etags = {upload_id: f'"{upload_id}"' for upload_id in uploads}
        return lambda locked, mapped: _record_outputs(locked, mapped["outputs"], uploads, etags)

    def complete(self, execution_id, outcome=None):
        request, payload = self.pending.pop(execution_id)
        outcome = outcome or fake_result(request.task_type, payload)
        status = self.scheduler.submit_workflow_result(execution_id, outcome, prepare_outputs=self.prepare)
        assert status == "applied"
        return request

    def pending_of(self, task_type):
        return [request for request, _ in self.pending.values() if request.task_type == task_type]

    def run(self, until=None):
        """Complete workflows until none remain or until(request, payload) is true for the next one."""
        for _ in range(500):
            self.idle()
            if not self.pending:
                return None
            for execution_id, (request, payload) in list(self.pending.items()):
                if until is not None and until(request, payload):
                    return request
                self.complete(execution_id)
        pytest.fail("Production did not settle")


@pytest.fixture
def user():
    return get_user_model().objects.create_user("director", password="studio-password")


@pytest.fixture
def api(user):
    client = APIClient()
    client.force_login(user)
    return client


def create(api, idea=IDEA, **extra):
    response = api.post(PROJECTS, {"idea": idea, **extra}, format="json")
    assert response.status_code == 201, response.json()
    return response.json()


def get(api, path, status=200, **params):
    response = api.get(path, params)
    assert response.status_code == status, response.content
    return response.json()


def post(api, path, status=200):
    response = api.post(path, format="json")
    assert response.status_code == status, response.content
    return response.json()


def node_ids(tree):
    return [node["id"] for node in tree["nodes"]]


def task_id(project_id, task_type, **inputs):
    tasks = Task.objects.filter(schedule_id=project_id, task_type=task_type)
    for key, value in inputs.items():
        tasks = tasks.filter(**{f"input__{key}": value})
    return str(tasks.get().pk)


def test_workbench_sessions_are_separate_from_service_credentials(settings, user):
    settings.WORKFLOW_SERVICE_TOKEN = "workflow-token"
    assert APIClient().get(PROJECTS).status_code == 403
    assert APIClient(HTTP_AUTHORIZATION="Bearer workflow-token").get(PROJECTS).status_code == 403

    browser = APIClient(enforce_csrf_checks=True)
    session = get(browser, "/api/v1/session")
    assert session["authenticated"] is False and session["username"] is None
    assert browser.post("/api/v1/session", {"username": "director", "password": "studio-password"},
                        format="json").status_code == 403
    token = {"HTTP_X_CSRFTOKEN": browser.cookies["csrftoken"].value}
    wrong = browser.post("/api/v1/session", {"username": "director", "password": "wrong"}, format="json", **token)
    assert wrong.status_code == 400 and wrong.json()["code"] == "invalid_credentials"
    login = browser.post("/api/v1/session", {"username": "director", "password": "studio-password"},
                         format="json", **token)
    assert login.status_code == 200 and login.json()["authenticated"] and login.json()["username"] == "director"
    token = {"HTTP_X_CSRFTOKEN": browser.cookies["csrftoken"].value}
    assert browser.get(PROJECTS).status_code == 200
    assert browser.post(PROJECTS, {"idea": IDEA}, format="json").status_code == 403
    assert browser.post(PROJECTS, {"idea": IDEA}, format="json", **token).status_code == 201

    # A workbench session is not a workflow identity.
    internal = browser.get(f"/internal/v1/executions/{uuid4()}/status", {
        "projectId": str(uuid4()), "taskId": str(uuid4()), "schemaVersion": "1",
    })
    assert internal.status_code == 401
    assert browser.delete("/api/v1/session", **token).status_code == 204
    assert browser.get(PROJECTS).status_code == 403


def test_create_start_inspect_pause_and_resume_real_production(api):
    studio = Studio()
    project = create(api, projectId=str(uuid4()))
    pid, root = project["id"], project["rootTaskId"]
    assert (project["status"], project["startedAt"], project["progress"], project["plan"]) == ("paused", None, 0, None)
    assert project["name"] == IDEA and project["currentEpisode"] is None
    replay = api.post(PROJECTS, {"idea": f" {IDEA} ", "projectId": pid}, format="json")
    assert replay.status_code == 200 and replay.json()["rootTaskId"] == root
    conflict = api.post(PROJECTS, {"idea": "Another idea", "projectId": pid}, format="json")
    assert conflict.status_code == 409 and conflict.json()["code"] == "project_conflict"
    assert api.post(PROJECTS, {"idea": "   "}, format="json").status_code == 400
    assert api.post(PROJECTS, {"idea": IDEA, "extra": 1}, format="json").status_code == 400
    assert [item["id"] for item in get(api, PROJECTS)["results"]] == [pid]

    # Creation leaves production paused: the Worker claims nothing.
    studio.idle()
    assert not Execution.objects.exists()
    tree = get(api, project_path(pid, "/task-tree"))
    assert (tree["rootTaskId"], node_ids(tree), tree["nodes"][0]["name"]) == (root, [root], "短剧制作")

    started = post(api, project_path(pid, "/resume"))
    assert started["status"] == "running" and started["startedAt"]
    assert post(api, project_path(pid, "/resume"))["startedAt"] == started["startedAt"]
    studio.idle()
    plan_task = task_id(pid, "series_plan")
    tree = get(api, project_path(pid, "/task-tree"), view="running")
    assert node_ids(tree) == [root, plan_task]
    assert (tree["nodes"][1]["state"], tree["nodes"][1]["parentId"], tree["nodes"][1]["slotKey"]) == (
        "running", root, "plan",
    )
    running = get(api, project_path(pid, "/tasks"), state="running")["results"]
    assert [(item["id"], item["path"]) for item in running] == [(plan_task, [root])]
    detail = get(api, project_path(pid, f"/tasks/{plan_task}"))
    assert detail["name"] == "全剧规划" and detail["path"][0]["id"] == root
    assert detail["currentExecution"]["workflowAcceptedAt"] and detail["executionCount"] == 1
    assert detail["canRetry"] is False and detail["outputCount"] == 0
    root_detail = get(api, project_path(pid, f"/tasks/{root}"))
    assert root_detail["waits"] == [{"slotKey": "plan", "task": {
        "id": plan_task, "name": "全剧规划", "taskType": "series_plan", "state": "running",
    }, "satisfied": False}]
    refused = api.post(project_path(pid, f"/tasks/{plan_task}/retry"))
    assert refused.status_code == 409 and refused.json()["code"] == "retry_unavailable"

    # Pausing stops new claims while the running plan still finishes and updates progress.
    assert post(api, project_path(pid, "/pause"))["status"] == "paused"
    assert post(api, project_path(pid, "/pause"))["status"] == "paused"
    studio.complete(UUID(detail["currentExecution"]["id"]))
    studio.idle()
    assert not studio.pending
    paused = get(api, project_path(pid))
    assert paused["status"] == "paused" and abs(paused["progress"] - 1 / 3) < 1e-9
    assert paused["name"] == "Night Service" and paused["plan"]["characters"][0]["name"] == "Mei"
    assert paused["episodeCount"] == 2 and paused["runningCount"] == 0
    assert node_ids(get(api, project_path(pid, "/task-tree"))) == [root]
    outputs = get(api, project_path(pid, f"/tasks/{plan_task}/outputs"))
    assert outputs["versions"] == [] and outputs["documents"] == [{"document": "plan", "episode": None}]

    post(api, project_path(pid, "/resume"))
    studio.idle()
    global_assets = task_id(pid, "global_assets")
    leaves = {task_id(pid, f"{kind}_asset", asset__assetKey=key) for kind, key in (
        ("scene", "station"), ("scene", "street"), ("character", "mei"),
    )}
    tree = get(api, project_path(pid, "/task-tree"))
    assert node_ids(tree)[:2] == [root, global_assets] and set(node_ids(tree)[2:]) == leaves
    assert {node["name"] for node in tree["nodes"][2:]} == {"场景素材 · Station", "场景素材 · Street", "人物素材 · Mei"}
    subtree = get(api, project_path(pid, "/task-tree"), view="subtree", rootTaskId=root)
    assert subtree["path"] == [] and set(node_ids(subtree)) == {root, plan_task, global_assets, *leaves}
    child = get(api, project_path(pid, "/task-tree"), view="subtree", rootTaskId=global_assets)
    assert [ancestor["id"] for ancestor in child["path"]] == [root]
    assert child["nodes"][0]["childTotal"] == child["nodes"][0]["childCount"] == 3
    assert get(api, project_path(pid, "/task-tree"), status=400, view="subtree")["rootTaskId"]
    examples = {"projectDetail": paused, "taskTree": tree, "taskDetail": root_detail}

    # Stop while the first episode is being edited to read the current episode library.
    edit = studio.run(until=lambda request, payload: request.task_type == "episode_edit")
    assert edit is not None
    summary = get(api, project_path(pid))
    episode_one = summary["currentEpisode"]
    assert episode_one["number"] == 1
    current = get(api, project_path(pid, "/assets"))
    assert current["scope"] == "episode" and current["episode"] == episode_one
    keys = {card["assetKey"] for card in current["results"]}
    assert "pass" in keys and "production:e1:s1:image" in keys and "station" not in keys
    shared = get(api, project_path(pid, "/assets"), scope="shared", kind="visual")
    assert {card["assetKey"] for card in shared["results"]} == {"station", "street", "mei"}
    selected = {
        task.input["asset"]["assetKey"]: task.output["asset"]["versionId"]
        for task in Task.objects.filter(schedule_id=pid, task_type__in=["scene_asset", "character_asset"])
    }
    assert {card["assetKey"]: card["adoptedVersionId"] for card in shared["results"]} == selected
    assert all(card["versionCount"] == 2 for card in shared["results"])
    shots = get(api, project_path(pid, f"/episodes/{episode_one['id']}/shots"))
    assert [shot["number"] for shot in shots["results"]] == [1, 2]
    assert shots["results"][0]["dialogue"][0]["lineId"] == "line-1"
    first_shot = shots["results"][0]["id"]
    filtered = get(api, project_path(pid, "/assets"), scope="episode", episodeId=episode_one["id"],
                   shotId=first_shot, kind="audio")
    assert {card["assetKey"] for card in filtered["results"]} == {
        "production:e1:s1:dialogue:1", "production:e1:s1:dialogue:2", "production:e1:s1:sound:1",
    }
    assert [card["assetKey"] for card in get(api, project_path(pid, "/assets"), scope="all", q="STAT")["results"]] == [
        "station",
    ]

    studio.run()
    done = get(api, project_path(pid))
    assert (done["status"], done["progress"], done["currentEpisode"]) == ("completed", 1, None)
    assert post(api, project_path(pid, "/pause"), status=409)["code"] == "project_completed"
    assert get(api, project_path(pid, "/assets"))["scope"] == "shared"
    assert node_ids(get(api, project_path(pid, "/task-tree"))) == [root]

    episodes = get(api, project_path(pid, "/episodes"))["results"]
    assert [(e["number"], e["state"], e["delivered"], e["shotCount"], e["progress"]) for e in episodes] == [
        (1, "completed", True, 2, 1), (2, "completed", True, 2, 1),
    ]
    episode = get(api, project_path(pid, f"/episodes/{episodes[0]['id']}"))
    assert episode["script"].startswith("INT. STATION")
    deliveries = get(api, project_path(pid, "/deliveries"))["results"]
    assert [group["episode"]["number"] for group in deliveries] == [1, 2]
    [delivery] = deliveries[0]["deliveries"]
    assert delivery["current"] and delivery["taskId"] == task_id(pid, "episode_edit", episodeNumber=1)
    assert [delivery[slot]["contentType"] for slot in ("video", "subtitles", "timeline")] == [
        "video/mp4", "text/vtt", "application/json",
    ]
    assert all(delivery[slot]["adopted"] for slot in ("video", "subtitles", "timeline"))
    only_two = get(api, project_path(pid, "/deliveries"), episodeId=episodes[1]["id"])["results"]
    assert [group["episode"]["number"] for group in only_two] == [2]
    delivery_cards = get(api, project_path(pid, "/assets"), scope="episode", episodeId=episodes[0]["id"],
                         kind="delivery")["results"]
    assert sorted(card["assetKey"] for card in delivery_cards) == [
        "production:e1:subtitles", "production:e1:timeline", "production:e1:video",
    ]
    assert all(card["delivery"] for card in delivery_cards)

    image_task = task_id(pid, "shot_image", episodeNumber=1, shotNumber=1)
    outputs = get(api, project_path(pid, f"/tasks/{image_task}/outputs"))
    assert outputs["task"]["name"] == "镜头 1 分镜图" and outputs["documents"] == []
    assert sorted((version["slot"], version["adopted"]) for version in outputs["versions"]) == [
        ("candidate", False), ("chosen", True),
    ]
    assert {version["asset"]["assetKey"] for version in outputs["versions"]} == {"production:e1:s1:image"}
    assert get(api, project_path(pid, f"/tasks/{image_task}"))["outputCount"] == 2
    asset_id = outputs["versions"][0]["assetId"]
    history = get(api, project_path(pid, f"/assets/{asset_id}/versions"))
    chosen = next(version["id"] for version in outputs["versions"] if version["slot"] == "chosen")
    assert history["asset"]["versionCount"] == 2 and history["asset"]["adoptedVersionId"] == chosen
    assert len(history["versions"]) == 2 and history["versions"][0]["createdAt"] >= history["versions"][1]["createdAt"]

    # Cursors walk every card once.
    seen, cursor = [], None
    while True:
        page = get(api, project_path(pid, "/assets"), scope="all", limit=7, **({"cursor": cursor} if cursor else {}))
        seen += [card["id"] for card in page["results"]]
        cursor = page["nextCursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == AssetItem.objects.filter(project_id=pid).count()
    assert get(api, project_path(pid, "/assets"), status=400, cursor="not-a-cursor")["cursor"]
    assert get(api, project_path(pid, "/assets"), status=400, scope="episode")["episodeId"]

    examples.update({
        "projectList": get(api, PROJECTS), "episodeList": {"results": episodes}, "shotList": shots,
        "assetList": current, "assetVersions": history, "taskOutputs": outputs,
        "deliveries": {"results": deliveries[:1]},
    })
    if os.environ.get("STUDIO_WRITE_PUBLIC_EXAMPLES"):
        EXAMPLES_PATH.write_text(json.dumps(examples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_retry_rules_follow_scheduler_state(api):
    studio = Studio()
    pid = create(api)["id"]
    post(api, project_path(pid, "/resume"))
    studio.idle()
    [request] = studio.pending_of("series_plan")
    studio.complete(request.execution_id, Failed("script_rejected", "Rewrites exhausted"))
    plan_task = str(request.task_id)
    failed = get(api, project_path(pid, f"/tasks/{plan_task}"))
    assert (failed["state"], failed["blockedReason"], failed["canRetry"]) == ("waiting", "script_rejected", True)
    assert failed["executions"][0]["errorCode"] == "script_rejected"
    assert failed["executions"][0]["message"] == "Rewrites exhausted"
    assert get(api, project_path(pid))["blockedCount"] == 1
    assert [item["id"] for item in get(api, project_path(pid, "/tasks"), blocked="true")["results"]] == [plan_task]
    retried = post(api, project_path(pid, f"/tasks/{plan_task}/retry"))
    assert (retried["state"], retried["blockedReason"], retried["attemptCount"]) == ("waiting", None, 0)
    assert post(api, project_path(pid, f"/tasks/{plan_task}/retry"), status=409)["code"] == "retry_unavailable"

    studio.idle()
    [request] = studio.pending_of("series_plan")
    studio.scheduler.register_provider_job(request.execution_id, "plan", "example", "remote-1")
    studio.complete(request.execution_id, Failed("provider_busy"))
    pending = get(api, project_path(pid, f"/tasks/{plan_task}"))
    assert pending["providerCheckPending"] and not pending["canRetry"]
    assert pending["blockedReason"] == PROVIDER_CHECK
    assert pending["executions"][0]["providerJobs"] == [{"stepKey": "plan", "provider": "example"}]
    assert post(api, project_path(pid, f"/tasks/{plan_task}/retry"), status=409)["code"] == "provider_check_pending"
    assert get(api, project_path(pid, f"/tasks/{plan_task}"))["executionCount"] == 2

    # An accepted workflow that times out needs a provider check before any retry.
    studio.scheduler.confirm_provider_jobs_inactive(
        request.execution_id, jobs=Execution.objects.get(pk=request.execution_id).provider_jobs,
    )
    Task.objects.filter(pk=plan_task).update(retry_at=timezone.now() - timedelta(seconds=1))
    studio.idle()
    [request] = studio.pending_of("series_plan")
    Execution.objects.filter(pk=request.execution_id).update(deadline_at=timezone.now() - timedelta(seconds=1))
    assert studio.scheduler.reap_one() == request.execution_id
    studio.pending.pop(request.execution_id)
    expired = get(api, project_path(pid, f"/tasks/{plan_task}"))
    assert (expired["state"], expired["providerCheckPending"], expired["canRetry"]) == ("timed_out", True, False)
    assert post(api, project_path(pid, f"/tasks/{plan_task}/retry"), status=409)["code"] == "provider_check_pending"

    # A workflow that never accepted cannot have provider activity, so exhausting its timeouts allows retry.
    silent = Studio(accept=False)
    other = create(api, "A clockmaker repairs yesterday.")["id"]
    post(api, project_path(other, "/resume"))
    silent.idle()
    task = Task.objects.get(schedule_id=other, task_type="series_plan")
    Task.objects.filter(pk=task.pk).update(attempt_count=3)
    Execution.objects.filter(pk=task.current_execution_id).update(deadline_at=timezone.now() - timedelta(seconds=1))
    assert silent.scheduler.reap_one() == task.current_execution_id
    timed_out = get(api, project_path(other, f"/tasks/{task.pk}"))
    assert (timed_out["state"], timed_out["canRetry"], timed_out["blockedReason"]) == ("timed_out", True, None)
    assert timed_out["executions"][0]["outcome"] == "expired"
    assert get(api, project_path(other))["timedOutCount"] == 1
    located = get(api, project_path(other, "/tasks"), state="timed_out")["results"]
    assert [(item["id"], item["path"]) for item in located] == [(str(task.pk), [str(task.parent_id)])]
    assert post(api, project_path(other, f"/tasks/{task.pk}/retry"))["state"] == "waiting"
    silent.idle()
    assert get(api, project_path(other, f"/tasks/{task.pk}"))["executionCount"] == 2


def test_queries_are_scoped_to_one_project(api, settings, monkeypatch):
    studio = Studio()
    first = create(api)["id"]
    second = create(api, "A lighthouse keeper hears the sea.")["id"]
    post(api, project_path(first, "/resume"))
    studio.run(until=lambda request, payload: request.task_type == "episode_script")
    asset = AssetItem.objects.filter(project_id=first).first()
    version = asset.versions.first()
    episode = Episode.objects.filter(project_id=first).first()
    first_root = Task.objects.get(schedule_id=first, parent__isnull=True).pk
    leaf = Task.objects.filter(schedule_id=first, task_type="scene_asset").first().pk

    for path in (
        f"/tasks/{first_root}", f"/tasks/{leaf}/outputs", f"/assets/{asset.pk}/versions",
        f"/asset-versions/{version.pk}/preview", f"/episodes/{episode.pk}", f"/episodes/{episode.pk}/shots",
    ):
        assert api.get(project_path(second, path)).status_code == 404, path
    assert api.post(project_path(second, f"/tasks/{leaf}/retry")).status_code == 404
    assert api.get(project_path(second, "/task-tree"), {"view": "subtree", "rootTaskId": first_root}).status_code == 404
    assert get(api, project_path(second, "/assets"), status=400, scope="episode", episodeId=episode.pk)["episodeId"]
    assert get(api, project_path(second, "/deliveries"), status=400, episodeId=episode.pk)["episodeId"]
    assert get(api, project_path(second, "/assets"), scope="all")["results"] == []
    assert get(api, project_path(second, "/tasks"))["results"][0]["taskType"] == "short_drama"
    assert len(get(api, project_path(second, "/tasks"))["results"]) == 1
    assert api.get(project_path(uuid4())).status_code == 404

    settings.STUDIO_MEDIA_BUCKET = "studio-test"
    settings.STUDIO_S3_BROWSER_ENDPOINT = "http://media.example.test:9000"
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "preview-test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "preview-test-secret")
    response = api.get(project_path(first, f"/asset-versions/{version.pk}/preview"))
    assert response.status_code == 200 and response["Cache-Control"] == "no-store"
    preview = response.json()
    url = urlsplit(preview["url"])
    query = parse_qs(url.query)
    assert (url.scheme, url.netloc) == ("http", "media.example.test:9000")
    assert url.path == f"/studio-test/{version.upload.object_key}"
    assert query["X-Amz-Expires"] == ["300"] and query["response-content-disposition"] == ["inline"]
    ExecutionUpload.objects.filter(pk=version.upload_id).update(content_type="image/svg+xml")
    unsafe = parse_qs(urlsplit(api.get(project_path(first, f"/asset-versions/{version.pk}/preview")).json()["url"]).query)
    assert unsafe["response-content-disposition"] == ["attachment"]
    assert (preview["method"], preview["contentType"], preview["sizeBytes"]) == ("GET", "image/png", 1024)
    settings.STUDIO_MEDIA_BUCKET = ""
    assert api.get(project_path(first, f"/asset-versions/{version.pk}/preview")).status_code == 503


def test_project_cursor_pages_are_stable(api):
    ids = [create(api, f"Idea {number}")["id"] for number in range(5)]
    page = get(api, PROJECTS, limit=2)
    seen = [item["id"] for item in page["results"]]
    while page["nextCursor"]:
        page = get(api, PROJECTS, limit=2, cursor=page["nextCursor"])
        seen += [item["id"] for item in page["results"]]
    assert seen == ids[::-1]
    assert get(api, PROJECTS, status=400, limit=0)["limit"]


def test_admin_actions_use_domain_services():
    admin_user = get_user_model().objects.create_superuser("operator", password="operator-password")
    browser = Client()
    browser.force_login(admin_user)
    response = browser.post("/admin/production/project/add/", {"idea": f"  {IDEA}  "})
    assert response.status_code == 302
    project = Project.objects.get()
    root = Task.objects.get(schedule_id=project.pk)
    assert (project.idea, root.state, root.task_type) == (IDEA, TaskState.WAITING, "short_drama")
    assert not Schedule.objects.get(pk=project.pk).enabled
    for path in ("production/project", "production/episode", "scheduler/task", "scheduler/execution",
                 "media/assetitem", "media/assetversion", "media/episodedelivery"):
        assert browser.get(f"/admin/{path}/").status_code == 200, path
        assert browser.get(f"/admin/{path}/", {"q": "night"}).status_code == 200, path
    assert browser.get(f"/admin/production/project/{project.pk}/change/").status_code == 200
    assert browser.post(f"/admin/production/project/{project.pk}/change/", {"idea": "Edited"}).status_code == 403
    assert browser.get(f"/admin/production/project/{project.pk}/delete/").status_code == 403

    def act(path, action, pk):
        return browser.post(f"/admin/{path}/", {"action": action, "_selected_action": [str(pk)]}, follow=True)

    act("production/project", "resume_projects", project.pk)
    assert Schedule.objects.get(pk=project.pk).enabled
    studio = Studio()
    studio.idle()
    [request] = studio.pending_of("series_plan")
    assert "Only failed or timed-out tasks" in act("scheduler/task", "retry_tasks", request.task_id).content.decode()
    studio.complete(request.execution_id, Failed("script_rejected"))
    act("scheduler/task", "retry_tasks", request.task_id)
    task = Task.objects.get(pk=request.task_id)
    assert (task.state, task.blocked_reason, task.attempt_count) == (TaskState.WAITING, "", 0)
    act("production/project", "pause_projects", project.pk)
    assert not Schedule.objects.get(pk=project.pk).enabled
    assert browser.get(f"/admin/scheduler/task/{task.pk}/change/").status_code == 200
    assert AssetVersion.objects.count() == 0


def test_published_examples_match_the_public_serializers():
    examples = json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))
    serializers = {
        "projectDetail": v1.ProjectDetail, "projectList": v1.ProjectList, "taskTree": v1.TaskTree,
        "taskDetail": v1.TaskDetail, "episodeList": v1.EpisodeList, "shotList": v1.ShotList,
        "assetList": v1.AssetList, "assetVersions": v1.AssetVersionList, "taskOutputs": v1.TaskOutputs,
        "deliveries": v1.DeliveryList,
    }
    assert examples.keys() == serializers.keys()
    for name, serializer in serializers.items():
        assert v1.output(serializer, examples[name]) == examples[name], name
