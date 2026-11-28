import json
import os
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import boto3
import httpx
import pytest
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from media import uploads as media_uploads, workflow_outputs
from media.models import AssetItem, AssetVersion, EpisodeDelivery, ExecutionUpload
from media.storage import storage_client
from production import leaves
from production.models import Episode, Project, Shot
from scheduler.models import ExecutionState, TaskState
from scheduler.tasks import Failed, PlanError
from scheduler.worker import Worker, configured_scheduler


@pytest.mark.skipif("STUDIO_S3_ENDPOINT" not in os.environ, reason="requires the development S3 endpoint")
def test_private_development_bucket():
    endpoint = os.environ["STUDIO_S3_ENDPOINT"]
    bucket = os.environ["STUDIO_MEDIA_BUCKET"]
    key = f"tests/{uuid4()}"
    body = b"storage smoke test"
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )
    anonymous = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        config=Config(signature_version=UNSIGNED, s3={"addressing_style": "path"}),
    )
    client.put_object(Bucket=bucket, Key=key, Body=body)
    try:
        with client.get_object(Bucket=bucket, Key=key)["Body"] as result:
            assert result.read() == body
        with pytest.raises(ClientError) as error:
            anonymous.get_object(Bucket=bucket, Key=key)
        assert error.value.response["ResponseMetadata"]["HTTPStatusCode"] == 403
    finally:
        client.delete_object(Bucket=bucket, Key=key)


PRODUCTION_CASES = json.loads(
    (Path(__file__).resolve().parents[3] / "contracts/production-v1.examples.json").read_text(encoding="utf-8")
)["tasks"]


@pytest.fixture
def live_media(settings, transactional_db):
    if not settings.STUDIO_S3_ENDPOINT:
        pytest.skip("requires the development S3 endpoint")
    settings.WORKFLOW_SERVICE_TOKEN = "test-media-workflow"
    api = APIClient(HTTP_AUTHORIZATION="Bearer test-media-workflow")
    with httpx.Client(trust_env=False, timeout=5) as http:
        yield api, http, configured_scheduler()
    uploads = list(ExecutionUpload.objects.values_list("bucket", "object_key"))
    if uploads:
        with closing(storage_client()) as client:
            for bucket, key in uploads:
                client.delete_object(Bucket=bucket, Key=key)


def create_leaf(scheduler, task_class):
    inputs = PRODUCTION_CASES[task_class.task_type]["input"]
    schedule, task = scheduler.create_schedule(task_class, inputs)
    project = Project.objects.create(schedule=schedule, idea="Media upload integration")
    if "episodeNumber" in inputs:
        episode = Episode.objects.create(
            project=project, number=inputs["episodeNumber"], title="Test episode", outline="Test outline",
        )
        if "shotNumber" in inputs:
            Shot.objects.create(episode=episode, number=inputs["shotNumber"], spec={})
    scheduler.set_enabled(schedule.pk, True)
    return next_execution(scheduler, task)


def next_execution(scheduler, task):
    assert Worker(scheduler).tick()
    task.refresh_from_db()
    return task.current_execution


def workflow_path(execution, suffix):
    return f"/internal/v1/executions/{execution.pk}/{suffix}"


def workflow_identity(execution):
    return {
        "projectId": str(execution.task.schedule_id), "taskId": str(execution.task_id),
        "executionId": str(execution.pk), "schemaVersion": "1",
    }


def accept_client(api, execution):
    response = api.post(workflow_path(execution, "accept"), workflow_identity(execution), format="json")
    assert response.status_code == 200 and response.json()["status"] == "accepted"


def grant_upload(api, execution, slot, content, content_type):
    request = {**workflow_identity(execution), "slot": slot, "sizeBytes": len(content), "contentType": content_type}
    response = api.post(workflow_path(execution, "uploads"), request, format="json")
    assert response.status_code == 200, response.json()
    grant = response.json()
    signed = parse_qs(urlsplit(grant["url"]).query)
    signed_at = datetime.strptime(signed["X-Amz-Date"][0], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    expires_at = signed_at + timedelta(seconds=int(signed["X-Amz-Expires"][0]))
    assert grant["method"] == "PUT" and datetime.fromisoformat(grant["expiresAt"]) == expires_at
    assert expires_at <= execution.deadline_at
    assert grant["headers"] == {
        "Content-Type": content_type, "Content-Length": str(len(content)), "If-None-Match": "*",
    }
    return grant


def test_private_media_upload_and_registration_are_idempotent(live_media, monkeypatch):
    api, http, scheduler = live_media
    execution = create_leaf(scheduler, leaves.ShotImage)
    path = workflow_path(execution, "uploads")
    content = b"example frame"
    request = {
        **workflow_identity(execution), "slot": "main", "sizeBytes": len(content), "contentType": "image/png",
    }
    assert APIClient().post(path, request, format="json").status_code == 401
    assert api.post(path, {**request, "projectId": str(uuid4())}, format="json").status_code == 404
    assert api.post(path, request, format="json").status_code == 409
    accept_client(api, execution)
    grant = grant_upload(api, execution, "main", content, "image/png")
    replay = grant_upload(api, execution, "main", content, "image/png")
    assert (replay["uploadId"], replay["objectKey"]) == (grant["uploadId"], grant["objectKey"])
    assert api.post(path, {**request, "sizeBytes": len(content) + 1}, format="json").status_code == 409
    assert ExecutionUpload.objects.count() == 1

    expected = PRODUCTION_CASES["shot_image"]["result"]
    descriptor = {**expected["outputs"][0], "uploadId": grant["uploadId"]}
    result = {
        **workflow_identity(execution), "outcome": "completed", "data": expected["data"], "outputs": [descriptor],
    }
    path = workflow_path(execution, "result")
    assert api.post(path, result, format="json").status_code == 409
    assert not AssetVersion.objects.exists()
    # A privileged test write bypasses the grant. HEAD must still reject mismatched metadata.
    upload = ExecutionUpload.objects.get(pk=grant["uploadId"])
    with closing(storage_client()) as client:
        client.put_object(Bucket=upload.bucket, Key=upload.object_key, Body=b"wrong", ContentType="image/png")
        assert api.post(path, result, format="json").status_code == 409
        client.delete_object(Bucket=upload.bucket, Key=upload.object_key)
    assert not AssetVersion.objects.exists()

    unsigned_url = grant["url"].split("?", 1)[0]
    assert http.put(unsigned_url, content=content, headers=grant["headers"]).status_code == 403
    assert http.put(grant["url"], content=content, headers=grant["headers"]).status_code == 200
    assert http.put(grant["url"], content=b"x" * len(content), headers=grant["headers"]).status_code == 412
    assert http.get(unsigned_url).status_code == 403

    verify = workflow_outputs.verify_uploaded_object

    def verify_before_transaction(client, upload):
        assert not connection.in_atomic_block
        return verify(client, upload)

    monkeypatch.setattr(workflow_outputs, "verify_uploaded_object", verify_before_transaction)
    assert api.post(path, result, format="json").json() == {"status": "applied"}
    assert api.post(path, result, format="json").json() == {"status": "already_processed"}
    version = AssetVersion.objects.get()
    shot = Shot.objects.get()
    expected_ref = {"assetKey": descriptor["assetKey"], "kind": "image", "versionId": str(version.pk)}
    assert shot.image == expected_ref
    assert version.asset.shot == shot and version.asset.episode == shot.episode
    assert version.asset.project_id == execution.task.schedule_id
    assert version.upload.execution_id == execution.pk and version.upload.slot == "main"
    assert version.etag
    execution.task.refresh_from_db()
    assert execution.task.state == TaskState.COMPLETED and execution.task.output == {"image": expected_ref}


def test_upload_cannot_be_claimed_by_other_slot_project_or_execution(live_media):
    api, _, scheduler = live_media
    first = create_leaf(scheduler, leaves.ShotImage)
    accept_client(api, first)
    grant = grant_upload(api, first, "other", b"image", "image/png")
    assert api.post(
        workflow_path(first, "result"),
        {
            **workflow_identity(first), "outcome": "completed", "data": {"selectedSlot": "main"},
            "outputs": [{**PRODUCTION_CASES["shot_image"]["result"]["outputs"][0], "uploadId": grant["uploadId"]}],
        },
        format="json",
    ).status_code == 400

    grant = grant_upload(api, first, "main", b"image", "image/png")
    second_project = create_leaf(scheduler, leaves.ShotImage)
    accept_client(api, second_project)
    descriptor = {**PRODUCTION_CASES["shot_image"]["result"]["outputs"][0], "uploadId": grant["uploadId"]}
    payload = {"outcome": "completed", "data": {"selectedSlot": "main"}, "outputs": [descriptor]}
    assert api.post(
        workflow_path(second_project, "result"), {**workflow_identity(second_project), **payload}, format="json",
    ).status_code == 400

    failure = {**workflow_identity(first), "outcome": "failed", "errorCode": "test_failure", "message": ""}
    assert api.post(workflow_path(first, "result"), failure, format="json").json() == {"status": "applied"}
    scheduler.retry(first.task.schedule_id, first.task_id)
    newer = next_execution(scheduler, first.task)
    accept_client(api, newer)
    assert newer.pk != first.pk
    assert api.post(
        workflow_path(newer, "result"), {**workflow_identity(newer), **payload}, format="json",
    ).status_code == 400
    newer.deadline_at = timezone.now() - timedelta(seconds=1)
    newer.save(update_fields=["deadline_at"])
    assert api.post(
        workflow_path(newer, "uploads"),
        {**workflow_identity(newer), "slot": "main", "sizeBytes": 5, "contentType": "image/png"},
        format="json",
    ).status_code == 409
    assert not AssetVersion.objects.exists()


def test_upload_signature_is_withheld_if_execution_finishes_during_signing(live_media, monkeypatch):
    api, _, scheduler = live_media
    execution = create_leaf(scheduler, leaves.ShotImage)
    accept_client(api, execution)
    sign = media_uploads.sign_upload

    def finish_before_signing(upload, deadline_at):
        assert not connection.in_atomic_block
        assert scheduler.submit_workflow_result(execution.pk, Failed("test_failure")) == "applied"
        return sign(upload, deadline_at)

    monkeypatch.setattr(media_uploads, "sign_upload", finish_before_signing)
    response = api.post(
        workflow_path(execution, "uploads"),
        {**workflow_identity(execution), "slot": "main", "sizeBytes": 5, "contentType": "image/png"},
        format="json",
    )
    assert response.status_code == 409
    assert "url" not in response.json()
    execution.refresh_from_db()
    assert execution.state == ExecutionState.FAILED


def test_delivery_registration_rolls_back_with_workflow_completion(live_media, monkeypatch):
    api, http, scheduler = live_media
    execution = create_leaf(scheduler, leaves.EpisodeEdit)
    accept_client(api, execution)
    assert api.post(
        workflow_path(execution, "uploads"),
        {**workflow_identity(execution), "slot": "extra", "sizeBytes": 1, "contentType": "video/mp4"},
        format="json",
    ).status_code == 400
    content_types = {"video": "video/mp4", "subtitles": "text/vtt", "timeline": "application/json"}
    outputs = []
    for descriptor in PRODUCTION_CASES["episode_edit"]["result"]["outputs"]:
        content = f"fixture {descriptor['slot']}".encode()
        grant = grant_upload(api, execution, descriptor["slot"], content, content_types[descriptor["slot"]])
        assert http.put(grant["url"], content=content, headers=grant["headers"]).status_code == 200
        outputs.append({**descriptor, "uploadId": grant["uploadId"]})
    result = {**workflow_identity(execution), "outcome": "completed", "data": {}, "outputs": outputs}
    path = workflow_path(execution, "result")
    original = leaves.EpisodeEdit.workflow_completion_handler

    def fail_in_completion(execution, output, recorded):
        original(execution, output, recorded)
        assert connection.in_atomic_block
        assert EpisodeDelivery.objects.filter(execution=execution).exists()
        assert Episode.objects.get().deliveries
        raise PlanError("force rollback after domain writes")

    with monkeypatch.context() as patch:
        patch.setattr(leaves.EpisodeEdit, "workflow_completion_handler", staticmethod(fail_in_completion))
        api.raise_request_exception = False
        assert api.post(path, result, format="json").status_code == 500
    api.raise_request_exception = True
    execution.refresh_from_db()
    assert execution.state == ExecutionState.ACCEPTED and execution.result is None
    assert not Episode.objects.get().deliveries
    assert not AssetItem.objects.exists() and not AssetVersion.objects.exists() and not EpisodeDelivery.objects.exists()

    assert api.post(path, result, format="json").json() == {"status": "applied"}
    assert api.post(path, result, format="json").json() == {"status": "already_processed"}
    delivery = EpisodeDelivery.objects.get(execution=execution)
    refs = delivery.episode.deliveries
    assert set(refs) == set(content_types)
    for slot, reference in refs.items():
        version = getattr(delivery, slot)
        assert reference == {"assetKey": version.asset.asset_key, "kind": slot, "versionId": str(version.pk)}
        assert version.upload.slot == slot and version.asset.episode == delivery.episode
    assert AssetVersion.objects.count() == 3


def test_workbench_preview_signs_short_reads_of_registered_versions(live_media, settings, django_user_model):
    api, http, scheduler = live_media
    execution = create_leaf(scheduler, leaves.EpisodeEdit)
    accept_client(api, execution)
    content_types = {"video": "video/mp4", "subtitles": "text/vtt", "timeline": "application/json"}
    outputs = []
    for descriptor in PRODUCTION_CASES["episode_edit"]["result"]["outputs"]:
        content = f"preview {descriptor['slot']}".encode()
        grant = grant_upload(api, execution, descriptor["slot"], content, content_types[descriptor["slot"]])
        assert http.put(grant["url"], content=content, headers=grant["headers"]).status_code == 200
        outputs.append({**descriptor, "uploadId": grant["uploadId"]})
    result = {**workflow_identity(execution), "outcome": "completed", "data": {}, "outputs": outputs}
    assert api.post(workflow_path(execution, "result"), result, format="json").json() == {"status": "applied"}

    # The test process reaches storage at the API endpoint; browsers use STUDIO_S3_BROWSER_ENDPOINT.
    settings.STUDIO_S3_BROWSER_ENDPOINT = settings.STUDIO_S3_ENDPOINT
    workbench = APIClient()
    workbench.force_login(django_user_model.objects.create_user("preview-reader"))
    project = f"/api/v1/projects/{execution.task.schedule_id}"
    [group] = workbench.get(f"{project}/deliveries").json()["results"]
    [delivery] = group["deliveries"]
    for slot, content_type in content_types.items():
        version = delivery[slot]
        preview = workbench.get(f"{project}/asset-versions/{version['id']}/preview").json()
        response = http.get(preview["url"])
        assert response.status_code == 200 and response.content == f"preview {slot}".encode()
        assert response.headers["content-type"] == content_type == preview["contentType"]
        assert response.headers["cache-control"] == "private, no-store"
        assert http.get(preview["url"].split("?", 1)[0]).status_code == 403
    other = create_leaf(scheduler, leaves.EpisodeEdit).task.schedule_id
    assert workbench.get(
        f"/api/v1/projects/{other}/asset-versions/{delivery['video']['id']}/preview",
    ).status_code == 404
