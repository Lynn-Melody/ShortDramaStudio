"""Browser-test fixture, piped to `manage.py shell` in the API environment.

It creates the test account and stands in for the fixed sample n8n workflows: it accepts a
project's dispatched workflow executions and submits the fixed two-episode, four-shot results
used by the backend production tests. Media goes through the T04 upload grant into private
storage, so the workbench previews real signed objects.
"""

import json
import struct
import sys
import zlib
from contextlib import closing
from pathlib import Path

# The shell runs from the repository root (or /workspace in the API container).
sys.path.insert(0, str(Path("apps/backend/tests").resolve()))

from django.contrib.auth import get_user_model  # noqa: E402

from media.uploads import authorize_upload  # noqa: E402
from media.storage import storage_client  # noqa: E402
from media.workflow_outputs import prepare_workflow_outputs  # noqa: E402
from scheduler.models import Execution, ExecutionState, Task, TaskState  # noqa: E402
from scheduler.tasks import Completed, Failed  # noqa: E402
from scheduler.worker import configured_scheduler  # noqa: E402
from test_production import fake_result  # noqa: E402

USERNAME = "e2e-director"
PASSWORD = "e2e-studio-password"


def _png(width):
    """A transparent width x 1 PNG, so test versions differ in their decoded width."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", width, 1, 8, 6, 0, 0, 0)
    signature = bytes([0x89]) + b'PNG' + bytes([13, 10, 26, 10])
    row = bytes(1 + 4 * width)  # filter byte, then transparent RGBA pixels
    return signature + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(row)) + chunk(b"IEND", b"")


# The adopted ("chosen") image is 1x1; the candidate version is 2x1.
PNG, PNG2 = _png(1), _png(2)
BODIES = {
    "image/png": PNG, "audio/mpeg": b"ID3 fixture audio", "video/mp4": b"fixture video",
    "text/vtt": b"WEBVTT\n\n00:00.000 --> 00:01.000\nWait!\n", "application/json": b'{"shots": []}',
}
CONTENT_TYPES = {
    "character": "image/png", "scene": "image/png", "prop": "image/png", "image": "image/png",
    "audio": "audio/mpeg", "video": "video/mp4", "subtitles": "text/vtt", "timeline": "application/json",
}


def ensure_user():
    user, _ = get_user_model().objects.get_or_create(username=USERNAME)
    user.set_password(PASSWORD)
    user.save()
    return {"username": USERNAME, "password": PASSWORD}


def _upload(execution, outputs):
    with closing(storage_client()) as client:
        for item in outputs:
            content_type = CONTENT_TYPES[item["kind"]]
            # Candidate slots get distinct bytes so a test can tell versions apart; PNGs differ in width.
            body = BODIES[content_type] if content_type != "image/png" else (PNG if item["slot"] != "candidate" else PNG2)
            grant = authorize_upload(execution, slot=item["slot"], size_bytes=len(body), content_type=content_type)
            upload = execution.uploads.get(pk=grant["uploadId"])
            client.put_object(Bucket=upload.bucket, Key=upload.object_key, Body=body, ContentType=content_type)
            item["uploadId"] = grant["uploadId"]


def advance(project_id, task_types=None, fail=None):
    """Answer every dispatched workflow of the project once, optionally limited to task types."""
    scheduler = configured_scheduler()
    done = []
    executions = (
        Execution.objects.filter(task__schedule_id=project_id, state__in=[ExecutionState.QUEUED, ExecutionState.ACCEPTED],
                                 dispatches__kind="workflow")
        .select_related("task").order_by("started_at").distinct()
    )
    for execution in executions:
        task = execution.task
        if task.current_execution_id != execution.pk or (task_types and task.task_type not in task_types):
            continue
        if scheduler.accept_workflow(execution.pk).status not in ("accepted", "already_accepted"):
            continue
        if fail == task.task_type:
            outcome = Failed("fixture_rejected", "The fixture workflow rejected this request")
        else:
            outcome = fake_result(task.task_type, execution.snapshot["input"])
            _upload(execution, outcome.output["outputs"])
        status = scheduler.submit_workflow_result(execution.pk, outcome, prepare_outputs=prepare_workflow_outputs)
        done.append({"taskType": task.task_type, "status": status})
    return done


def state(project_id):
    """Read-only snapshot: running task types and how many executions the project has had."""
    tasks = Task.objects.filter(schedule_id=project_id)
    return {
        "running": sorted(set(tasks.filter(state=TaskState.RUNNING).values_list("task_type", flat=True))),
        "rootState": tasks.get(parent__isnull=True).state,
        "executions": Execution.objects.filter(task__schedule_id=project_id).count(),
    }


def run(command):
    action = command["action"]
    if action == "user":
        return ensure_user()
    if action == "advance":
        return {"done": advance(command["projectId"], command.get("taskTypes"), command.get("fail")),
                **state(command["projectId"])}
    if action == "state":
        return state(command["projectId"])
    raise ValueError(f"Unknown fixture action {action!r}")
