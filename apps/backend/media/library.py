"""Project-scoped asset library reads. Object bytes are only reachable through signed previews."""

from collections import defaultdict

from django.db.models import Exists, OuterRef, Q
from django.db.models.fields.json import KT
from django.db.models.functions import Coalesce
from rest_framework.exceptions import ValidationError

from contracts.public_v1 import VISUAL_KINDS
from production.models import Episode, Shot
from production.workbench import episode_ref, task_ref
from scheduler.models import Task, TaskState
from studio.pagination import Key, paginate

from .models import AssetItem, AssetVersion, EpisodeDelivery
from .storage import sign_preview


ASSET_KEYS = (Key("episode_order", "int"), Key("shot_order", "int"), Key("asset_key", "text"))
VERSION_FIELDS = {
    "id": "pk", "assetId": "asset_id", "title": "title", "createdAt": "created_at",
    "contentType": "upload__content_type", "sizeBytes": "upload__size_bytes", "slot": "upload__slot",
    "taskId": "upload__execution__task_id", "taskType": "upload__execution__task__task_type",
    "executionId": "upload__execution_id",
}
DOCUMENTS = {"series_plan": "plan", "episode_script": "script", "episode_breakdown": "breakdown"}


def _items(project):
    delivered = EpisodeDelivery.objects.filter(
        Q(video__asset_id=OuterRef("pk")) | Q(subtitles__asset_id=OuterRef("pk")) | Q(timeline__asset_id=OuterRef("pk")),
    )
    return (
        AssetItem.objects.filter(project=project).select_related("episode", "shot")
        .annotate(
            is_delivery=Exists(delivered),
            episode_order=Coalesce("episode__number", 0),
            shot_order=Coalesce("shot__number", 0),
        )
    )


def _brief(item):
    return {
        "id": item.pk,
        "assetKey": item.asset_key,
        "kind": item.kind,
        "title": item.title,
        "episode": episode_ref(item.episode),
        "shot": None if item.shot is None else {"id": item.shot.pk, "number": item.shot.number},
        "delivery": item.is_delivery,
    }


def _refs(values):
    return {ref["versionId"] for ref in values if isinstance(ref, dict) and "versionId" in ref}


def adopted_version_ids(project, items, version_ids):
    """Version IDs that production adopted, derived from shot, episode and visual-asset records."""
    shots = {item.shot_id for item in items if item.shot_id}
    episodes = {item.episode_id for item in items if item.episode_id and not item.shot_id}
    adopted = set()
    for image, video, dialogue, sound in Shot.objects.filter(pk__in=shots, episode__project=project).values_list(
        "image", "video", "dialogue", "sound",
    ):
        adopted |= _refs([image, (video or {}).get("video"), *(line.get("audio") for line in dialogue),
                          *(cue.get("audio") for cue in sound)])
    for deliveries in Episode.objects.filter(pk__in=episodes, project=project).values_list("deliveries", flat=True):
        adopted |= _refs(deliveries.values())
    if any(item.kind in VISUAL_KINDS for item in items):
        adopted.update(
            Task.objects.filter(
                schedule_id=project.pk, task_type__in=[f"{kind}_asset" for kind in VISUAL_KINDS],
                state=TaskState.COMPLETED,
            ).annotate(version=KT("output__asset__versionId"))
            .filter(version__in=[str(version) for version in version_ids]).values_list("version", flat=True)
        )
    return adopted


def _version_rows(versions):
    return [
        {field: row[source] for field, source in VERSION_FIELDS.items()}
        for row in versions.values(*VERSION_FIELDS.values())
    ]


def _with_adoption(project, items, rows):
    adopted = adopted_version_ids(project, items, [row["id"] for row in rows])
    for row in rows:
        row["adopted"] = str(row["id"]) in adopted
    return rows


def _cards(project, items):
    rows = _with_adoption(project, items, _version_rows(
        AssetVersion.objects.filter(asset__in=items).order_by("asset_id", "-created_at", "-pk"),
    ))
    versions = defaultdict(list)
    for row in rows:
        versions[row["assetId"]].append(row)
    return [{
        **_brief(item),
        "versionCount": len(versions[item.pk]),
        "latestVersion": versions[item.pk][0],
        "adoptedVersionId": next((row["id"] for row in versions[item.pk] if row["adopted"]), None),
    } for item in items]


def _filter_kind(items, kind):
    if kind == "visual":
        return items.filter(kind__in=VISUAL_KINDS)
    if kind == "delivery":
        return items.filter(is_delivery=True)
    return items.filter(kind=kind)


def asset_list(project, current_episode, *, scope, episode_id=None, shot_id=None, kind=None, q=None, cursor, limit):
    items = _items(project).filter(Exists(AssetVersion.objects.filter(asset_id=OuterRef("pk"))))
    episode = None
    if scope == "current":
        episode = current_episode
        scope = "shared" if episode is None else "episode"
    elif scope == "episode":
        episode = Episode.objects.filter(project=project, pk=episode_id).first()
        if episode is None:
            raise ValidationError({"episodeId": "Unknown episode in this project."})
    if scope == "shared":
        items = items.filter(episode__isnull=True)
    elif scope == "episode":
        items = items.filter(episode=episode)
    if shot_id is not None:
        if not Shot.objects.filter(pk=shot_id, episode__project=project).exists():
            raise ValidationError({"shotId": "Unknown shot in this project."})
        items = items.filter(shot_id=shot_id)
    if kind is not None:
        items = _filter_kind(items, kind)
    if q:
        items = items.filter(Q(title__icontains=q) | Q(asset_key__icontains=q))
    page, next_cursor = paginate(items, ASSET_KEYS, cursor=cursor, limit=limit)
    return {"scope": scope, "episode": episode_ref(episode), "results": _cards(project, page), "nextCursor": next_cursor}


def get_asset(project, asset_id):
    """Raise AssetItem.DoesNotExist outside the project."""
    return _items(project).get(pk=asset_id)


def asset_versions(project, item):
    rows = _with_adoption(project, [item], _version_rows(
        AssetVersion.objects.filter(asset=item).order_by("-created_at", "-pk"),
    ))
    [card] = _cards(project, [item])
    return {"asset": card, "versions": rows}


def output_count(project, task_id):
    return AssetVersion.objects.filter(asset__project=project, upload__execution__task_id=task_id).count()


def task_outputs(project, task_id):
    task = task_ref(project, task_id)
    versions = AssetVersion.objects.filter(asset__project=project, upload__execution__task_id=task["id"])
    rows = _version_rows(versions.order_by("created_at", "pk"))
    items = {item.pk: item for item in _items(project).filter(pk__in={row["assetId"] for row in rows})}
    rows = _with_adoption(project, list(items.values()), rows)
    documents = []
    if task["state"] == TaskState.COMPLETED and task["taskType"] in DOCUMENTS:
        number = Task.objects.filter(pk=task["id"]).values_list(KT("input__episodeNumber"), flat=True).get()
        episode = None if number is None else Episode.objects.get(project=project, number=int(number))
        documents.append({"document": DOCUMENTS[task["taskType"]], "episode": episode_ref(episode)})
    return {
        "task": task,
        "versions": [{**row, "asset": _brief(items[row["assetId"]])} for row in rows],
        "documents": documents,
    }


def delivery_list(project, *, episode_id=None):
    deliveries = (
        EpisodeDelivery.objects.filter(episode__project=project).select_related("episode", "execution")
        .order_by("episode__number", "-created_at", "-pk")
    )
    if episode_id is not None:
        if not Episode.objects.filter(project=project, pk=episode_id).exists():
            raise ValidationError({"episodeId": "Unknown episode in this project."})
        deliveries = deliveries.filter(episode_id=episode_id)
    deliveries = list(deliveries)
    version_ids = {getattr(delivery, f"{slot}_id") for delivery in deliveries for slot in ("video", "subtitles", "timeline")}
    rows = _version_rows(AssetVersion.objects.filter(pk__in=version_ids))
    items = list(_items(project).filter(pk__in={row["assetId"] for row in rows}))
    versions = {row["id"]: row for row in _with_adoption(project, items, rows)}
    groups = {}
    for delivery in deliveries:
        adopted = delivery.episode.deliveries
        entry = {
            "id": delivery.pk,
            "createdAt": delivery.created_at,
            "current": all(
                adopted.get(slot, {}).get("versionId") == str(getattr(delivery, f"{slot}_id"))
                for slot in ("video", "subtitles", "timeline")
            ),
            "taskId": delivery.execution.task_id,
            "executionId": delivery.execution_id,
            **{slot: versions[getattr(delivery, f"{slot}_id")] for slot in ("video", "subtitles", "timeline")},
        }
        groups.setdefault(delivery.episode_id, {"episode": episode_ref(delivery.episode), "deliveries": []})
        groups[delivery.episode_id]["deliveries"].append(entry)
    return {"results": list(groups.values())}


def preview(project, version_id):
    """Raise AssetVersion.DoesNotExist outside the project."""
    version = AssetVersion.objects.select_related("upload").get(pk=version_id, asset__project=project)
    url, expires_at = sign_preview(version.upload)
    return {
        "versionId": version.pk,
        "method": "GET",
        "url": url,
        "expiresAt": expires_at,
        "contentType": version.upload.content_type,
        "sizeBytes": version.upload.size_bytes,
    }
