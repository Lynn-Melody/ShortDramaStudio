import json
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from production.services import create_project
from scheduler.models import Dispatch, DispatchKind, ExecutionState, Task, TaskState
from scheduler.service import PROVIDER_CHECK
from scheduler.worker import DeliveryError, Worker, WorkflowRequest, configured_scheduler
from studio import n8n


pytestmark = pytest.mark.django_db(transaction=True)
EXAMPLES = json.loads(
    (Path(__file__).resolve().parents[3] / "contracts/production-v1.examples.json").read_text(encoding="utf-8")
)["tasks"]


def identity(execution):
    return {
        "projectId": str(execution.task.schedule_id), "taskId": str(execution.task_id),
        "executionId": str(execution.id), "schemaVersion": "1",
    }


def endpoint(execution, action):
    return f"/internal/v1/executions/{execution.id}/{action}"


def scope(execution):
    return {key: value for key, value in identity(execution).items() if key != "executionId"}


def advance(scheduler):
    worker = Worker(scheduler)
    for _ in range(30):
        if not worker.tick():
            return
    pytest.fail("Worker did not reach idle")


@pytest.fixture
def clients(settings):
    settings.WORKFLOW_SERVICE_TOKEN = "workflow-token"
    settings.WORKFLOW_VERIFIER_TOKEN = "verification-token"
    workflow = APIClient(HTTP_AUTHORIZATION="Bearer workflow-token")
    verifier = APIClient(HTTP_AUTHORIZATION="Bearer verification-token")
    return workflow, verifier


@pytest.fixture
def pending():
    scheduler = configured_scheduler()
    project, _ = create_project(EXAMPLES["series_plan"]["input"]["idea"])
    scheduler.set_enabled(project.pk, True)
    advance(scheduler)
    task = Task.objects.get(schedule_id=project.pk, task_type="series_plan")
    return project, task.current_execution, scheduler


def test_accept_identity_status_and_callback_replay(pending, clients):
    project, execution, scheduler = pending
    workflow, verifier = clients
    url = endpoint(execution, "accept")
    body = identity(execution)
    assert APIClient().post(url, body, format="json").status_code == 401
    assert verifier.post(url, body, format="json").status_code == 401
    for key in ["projectId", "taskId"]:
        assert workflow.post(url, {**body, key: str(uuid4())}, format="json").status_code == 404
    assert workflow.post(url, {**body, "executionId": str(uuid4())}, format="json").status_code == 400
    for version in ["2", 1]:
        assert workflow.post(url, {**body, "schemaVersion": version}, format="json").status_code == 400

    result_url = endpoint(execution, "result")
    result = {**body, "outcome": "completed", **EXAMPLES["series_plan"]["result"]}
    status_url = endpoint(execution, "status")
    assert workflow.get(status_url, scope(execution)).json() == {"active": False}
    assert workflow.post(result_url, result, format="json").json() == {"status": "not_accepted"}
    assert workflow.post(url, body, format="json").json() == {
        "status": "accepted", "input": EXAMPLES["series_plan"]["input"],
    }
    assert workflow.post(url, body, format="json").json() == {"status": "already_accepted"}
    assert workflow.get(status_url, scope(execution)).json() == {"active": True}

    assert workflow.post(result_url, {**result, "outputs": [{}]}, format="json").status_code == 400
    assert workflow.post(result_url, {**result, "taskId": str(uuid4())}, format="json").status_code == 404
    assert not project.episodes.exists()
    assert workflow.post(result_url, result, format="json").json() == {"status": "applied"}
    assert workflow.post(result_url, result, format="json").json() == {"status": "already_processed"}
    assert workflow.get(status_url, scope(execution)).json() == {"active": False}
    advance(scheduler)
    project.refresh_from_db()
    assert project.title == result["data"]["title"]
    assert project.episodes.count() == 1
    assert Task.objects.filter(schedule_id=project.pk, task_type="global_assets").exists()


def test_configured_services_fail_closed(pending, clients, settings):
    _, execution, _ = pending
    workflow, verifier = clients
    settings.WORKFLOW_SERVICE_TOKEN = ""
    assert workflow.post(endpoint(execution, "accept"), identity(execution), format="json").status_code == 401
    settings.WORKFLOW_SERVICE_TOKEN = settings.WORKFLOW_VERIFIER_TOKEN
    assert verifier.get(endpoint(execution, "provider-check"), scope(execution)).status_code == 401


def test_expired_accepted_workflow_requires_exact_provider_snapshot(pending, clients):
    _, execution, scheduler = pending
    workflow, verifier = clients
    body = identity(execution)
    accept = endpoint(execution, "accept")
    job = {"provider": "animation-service", "jobId": "job-1"}
    upload = endpoint(execution, "provider-jobs/render")
    check = endpoint(execution, "provider-check")
    assert workflow.put(upload, {**body, **job}, format="json").status_code == 409
    assert workflow.post(accept, body, format="json").json() == {
        "status": "accepted", "input": EXAMPLES["series_plan"]["input"],
    }
    execution.deadline_at = timezone.now() - timedelta(seconds=1)
    execution.save(update_fields=["deadline_at"])
    assert workflow.get(endpoint(execution, "status"), scope(execution)).json() == {"active": False}
    assert workflow.post(accept, body, format="json").json() == {"status": "expired"}
    execution.task.refresh_from_db()
    assert execution.task.blocked_reason == PROVIDER_CHECK
    assert scheduler.claim_next() is None

    initial = verifier.get(check, scope(execution)).json()
    assert initial == {"pending": True, "jobs": []}
    assert workflow.put(upload, {**body, **job}, format="json").status_code == 204
    assert workflow.put(upload, {**body, **job}, format="json").status_code == 204
    assert workflow.put(upload, {**body, **job, "jobId": "different"}, format="json").status_code == 409
    assert workflow.get(check, scope(execution)).status_code == 401
    confirmation = {**body, "workflowStopped": True, "requestsSettled": True, "jobsInactive": True}
    assert verifier.post(check, {**confirmation, "jobs": initial["jobs"]}, format="json").status_code == 409
    current = verifier.get(check, scope(execution)).json()
    assert current == {"pending": True, "jobs": [{"stepKey": "render", **job}]}
    confirmation["jobs"] = current["jobs"]
    assert verifier.post(check, {**confirmation, "requestsSettled": False}, format="json").status_code == 400
    assert workflow.post(check, confirmation, format="json").status_code == 401
    assert verifier.post(check, confirmation, format="json").status_code == 204
    execution.refresh_from_db()
    assert execution.provider_jobs_checked_at is not None
    execution.task.refresh_from_db()
    assert execution.task.state == TaskState.WAITING and execution.task.retry_at is not None


def test_late_success_adopted_for_current_execution(pending, clients):
    project, execution, scheduler = pending
    workflow, _ = clients
    body = identity(execution)
    workflow.post(endpoint(execution, "accept"), body, format="json")
    execution.deadline_at = timezone.now() - timedelta(seconds=1)
    execution.save(update_fields=["deadline_at"])
    scheduler.reap_one()
    result = {**body, "outcome": "completed", **EXAMPLES["series_plan"]["result"]}
    assert workflow.post(endpoint(execution, "result"), result, format="json").json() == {"status": "applied"}
    assert project.episodes.count() == 1


def test_failed_result_with_registered_job_waits_for_verification(pending, clients):
    _, execution, scheduler = pending
    workflow, _ = clients
    body = identity(execution)
    workflow.post(endpoint(execution, "accept"), body, format="json")
    job = {**body, "provider": "agent-service", "jobId": "job-1"}
    assert workflow.put(endpoint(execution, "provider-jobs/plan"), job, format="json").status_code == 204
    failed = {**body, "outcome": "failed", "errorCode": "provider_busy", "message": "Try again later."}
    result_url = endpoint(execution, "result")
    reserved = {**failed, "errorCode": PROVIDER_CHECK}
    assert workflow.post(result_url, reserved, format="json").status_code == 400
    assert workflow.post(result_url, failed, format="json").json() == {"status": "applied"}
    assert workflow.post(result_url, failed, format="json").json() == {"status": "already_processed"}
    execution.refresh_from_db()
    assert execution.state == ExecutionState.FAILED
    assert execution.result == {
        "outcome": "failed", "error_code": "provider_busy", "message": "Try again later.", "retryable": True,
    }
    assert execution.task.blocked_reason == PROVIDER_CHECK
    assert scheduler.claim_next() is None


def test_leaf_validation_and_media_registration_boundary(pending, clients):
    project, execution, scheduler = pending
    workflow, _ = clients
    body = identity(execution)
    workflow.post(endpoint(execution, "accept"), body, format="json")
    result = {**body, "outcome": "completed", "data": {}, "outputs": []}
    assert workflow.post(endpoint(execution, "result"), result, format="json").json() == {"status": "applied"}
    execution.refresh_from_db()
    assert execution.result["error_code"] == "invalid_workflow_result"
    assert not project.episodes.exists()

    scheduler.retry(project.pk, execution.task_id)
    advance(scheduler)
    execution.task.refresh_from_db()
    execution = execution.task.current_execution
    body = identity(execution)
    workflow.post(endpoint(execution, "accept"), body, format="json")
    result = {**body, "outcome": "completed", **EXAMPLES["series_plan"]["result"]}
    assert workflow.post(endpoint(execution, "result"), result, format="json").json() == {"status": "applied"}
    advance(scheduler)
    asset = Task.objects.get(schedule_id=project.pk, task_type="scene_asset").current_execution
    body = identity(asset)
    workflow.post(endpoint(asset, "accept"), body, format="json")
    result = {**body, "outcome": "completed", **EXAMPLES["scene_asset"]["result"]}
    assert workflow.post(endpoint(asset, "result"), result, format="json").status_code == 503
    asset.refresh_from_db()
    assert asset.state == ExecutionState.ACCEPTED
    assert asset.result is None


def test_normal_worker_retries_same_webhook_delivery(pending, settings, monkeypatch):
    _, execution, _ = pending
    settings.SCHEDULER_WORKFLOW_DISPATCHER = "studio.n8n.dispatch_workflow"
    url = "http://n8n:5678/webhook/series_plan"
    settings.N8N_WEBHOOKS = {"series_plan": url}
    settings.N8N_DISPATCH_TOKEN = "dispatch-token"
    post = AsyncMock(side_effect=[TimeoutError(), 503, 200])
    monkeypatch.setattr(n8n, "_post_webhook", post)
    dispatch = Dispatch.objects.get(execution=execution, kind=DispatchKind.WORKFLOW)
    for failures in [1, 2]:
        call_command("run_scheduler", once=True)
        dispatch.refresh_from_db()
        assert dispatch.failures == failures and dispatch.delivered_at is None and dispatch.lease_id is None
        assert dispatch.available_at > timezone.now()
        Dispatch.objects.filter(pk=dispatch.pk).update(available_at=timezone.now())
    call_command("run_scheduler", once=True)
    dispatch.refresh_from_db()
    assert dispatch.delivered_at is not None
    assert post.await_count == 3
    for call in post.await_args_list:
        webhook_url, payload, headers, timeout = call.args
        assert webhook_url == url
        assert headers == {"Idempotency-Key": str(execution.id), "X-Studio-Dispatch-Token": "dispatch-token"}
        assert payload == {
            **identity(execution), "taskType": "series_plan", "deadlineAt": execution.deadline_at.isoformat(),
        }
        assert 0 < timeout <= 5


@pytest.mark.parametrize("status_code", [302, 401])
def test_normal_worker_stops_on_permanent_http_failure(pending, settings, monkeypatch, status_code):
    _, execution, _ = pending
    settings.SCHEDULER_WORKFLOW_DISPATCHER = "studio.n8n.dispatch_workflow"
    url = "http://n8n:5678/webhook/series_plan"
    settings.N8N_WEBHOOKS = {"series_plan": url}
    settings.N8N_DISPATCH_TOKEN = "dispatch-token"
    monkeypatch.setattr(n8n, "_post_webhook", AsyncMock(return_value=status_code))
    call_command("run_scheduler", once=True)
    execution.refresh_from_db()
    assert execution.state == ExecutionState.FAILED
    assert execution.result["error_code"] == f"workflow_http_{status_code}"
    assert execution.result["retryable"] is False


def test_slow_response_headers_cannot_extend_dispatch_deadline(settings):
    response_started = Event()
    stop = Event()

    class SlowWebhook(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            try:
                self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                response_started.set()
                for _ in range(30):
                    if stop.wait(0.04):
                        return
                    self.wfile.write(b"X-Partial: value\r\n")
                self.wfile.write(b"Content-Length: 0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                return

    with ThreadingHTTPServer(("127.0.0.1", 0), SlowWebhook) as server:
        settings.N8N_WEBHOOKS = {"series_plan": f"http://127.0.0.1:{server.server_port}/webhook/series_plan"}
        settings.N8N_DISPATCH_TOKEN = "dispatch-token"
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            start = monotonic()
            request = WorkflowRequest(
                "series_plan", uuid4(), uuid4(), "series_plan", "1", uuid4(),
                timezone.now() + timedelta(seconds=0.35),
            )
            with pytest.raises(DeliveryError, match="workflow_transport_failed") as error:
                n8n.dispatch_workflow(request)
            assert error.value.retryable
            assert response_started.is_set() and monotonic() - start < 0.9
        finally:
            stop.set()
            server.shutdown()
            thread.join()
