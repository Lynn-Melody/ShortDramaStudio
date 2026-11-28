from uuid import uuid4

from django.db import models
from django.db.models import Q


class ExecutionUpload(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    execution = models.ForeignKey("scheduler.Execution", on_delete=models.CASCADE, related_name="uploads")
    slot = models.CharField(max_length=100)
    bucket = models.CharField(max_length=63)
    object_key = models.CharField(max_length=300, unique=True)
    content_type = models.CharField(max_length=200)
    size_bytes = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("execution", "slot"), name="media_unique_execution_slot"),
            models.CheckConstraint(condition=Q(size_bytes__gt=0), name="media_upload_positive_size"),
        ]


class AssetItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    project = models.ForeignKey("production.Project", on_delete=models.CASCADE, related_name="asset_items")
    episode = models.ForeignKey("production.Episode", on_delete=models.CASCADE, null=True, blank=True)
    shot = models.ForeignKey("production.Shot", on_delete=models.CASCADE, null=True, blank=True)
    asset_key = models.CharField(max_length=100)
    kind = models.CharField(max_length=32)
    title = models.CharField(max_length=200)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("project", "asset_key"), name="media_unique_project_asset"),
            models.CheckConstraint(
                condition=Q(shot__isnull=True) | Q(episode__isnull=False), name="media_shot_requires_episode",
            ),
        ]


class AssetVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    asset = models.ForeignKey(AssetItem, on_delete=models.CASCADE, related_name="versions")
    upload = models.OneToOneField(ExecutionUpload, on_delete=models.RESTRICT, related_name="version")
    title = models.CharField(max_length=200)
    etag = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)


class EpisodeDelivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    episode = models.ForeignKey("production.Episode", on_delete=models.CASCADE, related_name="delivery_records")
    execution = models.OneToOneField("scheduler.Execution", on_delete=models.CASCADE, related_name="delivery_record")
    video = models.ForeignKey(AssetVersion, on_delete=models.RESTRICT, related_name="+")
    subtitles = models.ForeignKey(AssetVersion, on_delete=models.RESTRICT, related_name="+")
    timeline = models.ForeignKey(AssetVersion, on_delete=models.RESTRICT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
