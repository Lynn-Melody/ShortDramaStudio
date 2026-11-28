from uuid import uuid4

from django.conf import settings
from django.db import transaction
from rest_framework.exceptions import ValidationError

from contracts.production_v1 import DATA_ONLY_LEAVES, DELIVERY_KINDS, LEAVES
from scheduler.models import Schedule
from scheduler.worker import configured_scheduler

from .models import ExecutionUpload
from .storage import StorageUnavailable, UploadConflict, sign_upload


@transaction.atomic
def _allocate_upload(execution, slot, size_bytes, content_type):
    Schedule.objects.select_for_update().get(pk=execution.task.schedule_id)
    if not configured_scheduler().workflow_is_current(execution.id):
        raise UploadConflict("Uploads require a current, accepted workflow before its deadline.")
    if not settings.STUDIO_MEDIA_BUCKET:
        raise StorageUnavailable()
    upload_id = uuid4()
    upload, _ = ExecutionUpload.objects.get_or_create(
        execution=execution,
        slot=slot,
        defaults={
            "id": upload_id,
            "bucket": settings.STUDIO_MEDIA_BUCKET,
            "object_key": f"projects/{execution.task.schedule_id}/executions/{execution.id}/{upload_id}",
            "size_bytes": size_bytes,
            "content_type": content_type,
        },
    )
    if (upload.size_bytes, upload.content_type) != (size_bytes, content_type):
        raise UploadConflict("This slot already has an upload with different terms.")
    return upload


def authorize_upload(execution, *, slot, size_bytes, content_type):
    task_type = execution.task.task_type
    if task_type not in LEAVES or task_type in DATA_ONLY_LEAVES:
        raise ValidationError({"slot": "This task does not produce media outputs."})
    if task_type == "episode_edit" and slot not in DELIVERY_KINDS:
        raise ValidationError({"slot": "Expected video, subtitles or timeline."})
    upload = _allocate_upload(execution, slot, size_bytes, content_type)
    url, headers, expires_at = sign_upload(upload, execution.deadline_at)
    return {
        "uploadId": str(upload.pk),
        "objectKey": upload.object_key,
        "method": "PUT",
        "url": url,
        "headers": headers,
        "expiresAt": expires_at,
    }
