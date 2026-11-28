from contextlib import closing

from rest_framework.exceptions import ValidationError

from contracts.production_v1 import DELIVERY_KINDS
from production.models import Episode, Shot
from scheduler.tasks import PlanError

from .models import AssetItem, AssetVersion, EpisodeDelivery, ExecutionUpload
from .storage import storage_client, verify_uploaded_object


def prepare_workflow_outputs(execution, output):
    """Validate declared objects before completion and return the transactional recorder."""
    descriptors = output["outputs"]
    if not descriptors:
        return None
    uploads = {
        str(upload.id): upload
        for upload in ExecutionUpload.objects.filter(
            execution=execution, id__in=[descriptor["uploadId"] for descriptor in descriptors],
        )
    }
    for descriptor in descriptors:
        upload = uploads.get(descriptor["uploadId"])
        if upload is None or upload.slot != descriptor["slot"]:
            raise ValidationError({"outputs": "Each upload must belong to this execution and output slot."})

    with closing(storage_client()) as client:
        etags = {upload_id: verify_uploaded_object(client, upload) for upload_id, upload in uploads.items()}

    def record_outputs(locked_execution, locked_output):
        return _record_outputs(locked_execution, locked_output["outputs"], uploads, etags)

    return record_outputs


def _record_outputs(execution, descriptors, uploads, etags):
    project_id = execution.task.schedule_id
    scope = execution.snapshot["input"]
    episode = shot = None
    if "episodeNumber" in scope:
        episode = Episode.objects.get(project_id=project_id, number=scope["episodeNumber"])
    if "shotNumber" in scope:
        shot = Shot.objects.get(episode=episode, number=scope["shotNumber"])

    refs = {}
    for descriptor in descriptors:
        asset, _ = AssetItem.objects.get_or_create(
            project_id=project_id,
            asset_key=descriptor["assetKey"],
            defaults={"kind": descriptor["kind"], "title": descriptor["title"], "episode": episode, "shot": shot},
        )
        if (asset.kind, asset.episode_id, asset.shot_id) != (
            descriptor["kind"], episode.pk if episode else None, shot.pk if shot else None,
        ):
            raise PlanError("An existing asset has different kind or scope")
        upload_id = descriptor["uploadId"]
        version = AssetVersion.objects.create(
            asset=asset, upload=uploads[upload_id], title=descriptor["title"], etag=etags[upload_id],
        )
        refs[descriptor["slot"]] = {"assetKey": asset.asset_key, "kind": asset.kind, "versionId": str(version.pk)}

    if execution.task.task_type == "episode_edit":
        EpisodeDelivery.objects.create(
            episode=episode,
            execution=execution,
            **{f"{slot}_id": refs[slot]["versionId"] for slot in DELIVERY_KINDS},
        )
    return refs
