from django.db.models import QuerySet

from contracts import production_v1 as v1
from scheduler.tasks import PlanError

from .models import Episode, Project, Shot


def record_workflow_completion(execution, output, recorded):
    """Apply v1 domain writes and adopt the versions returned by the recorder."""
    descriptors = {item["slot"]: item for item in output["outputs"]}
    if descriptors:
        if recorded.keys() != descriptors.keys():
            raise PlanError("The recorder must return a version reference for every output slot")
        refs = {}
        for slot, ref in recorded.items():
            ref = v1.validated(v1.AssetRef, ref)
            descriptor = descriptors[slot]
            if (ref["assetKey"], ref["kind"]) != (descriptor["assetKey"], descriptor["kind"]):
                raise PlanError("Registered version does not match its output descriptor")
            refs[slot] = ref
    else:
        refs = {}
    return _apply_v1(execution, output["data"], refs)


def _set_fields(rows: QuerySet, **fields):
    if rows.update(**fields) != 1:
        raise PlanError("Missing production row for workflow result")


def _apply_v1(execution, data, refs):
    task = execution.task
    task_type = task.task_type
    scope = execution.snapshot["input"]
    project_id = task.schedule_id
    if task_type == "series_plan":
        _set_fields(Project.objects.filter(pk=project_id), title=data["title"], plan=data)
        Episode.objects.bulk_create(
            Episode(project_id=project_id, number=number, **outline)
            for number, outline in enumerate(data["episodes"], 1)
        )
        return data
    if task_type.endswith("_asset"):
        return {"asset": refs[data["selectedSlot"]]}

    episodes = Episode.objects.filter(project_id=project_id, number=scope["episodeNumber"])
    if task_type == "episode_script":
        _set_fields(episodes, script=data["script"])
        return data
    if task_type == "episode_breakdown":
        episode = episodes.get()
        Shot.objects.bulk_create(
            Shot(episode=episode, number=number, spec=spec)
            for number, spec in enumerate(data["shots"], 1)
        )
        return data
    if task_type == "episode_edit":
        deliveries = {slot: refs[slot] for slot in v1.DELIVERY_KINDS}
        _set_fields(episodes, deliveries=deliveries)
        return {"deliveries": deliveries}

    shot = Shot.objects.filter(episode__in=episodes, number=scope["shotNumber"])
    match task_type:
        case "shot_image":
            result = {"image": refs[data["selectedSlot"]]}
            _set_fields(shot, **result)
        case "shot_video":
            result = {"video": refs[data["selectedSlot"]], "durationMs": data["durationMs"]}
            _set_fields(shot, video=result)
        case "shot_dialogue":
            result = {"dialogue": [
                {
                    **{key: value for key, value in take.items() if key != "slot"},
                    "delivery": line["delivery"], "text": line["text"], "audio": refs[take["slot"]],
                }
                for take, line in zip(data["takes"], scope["dialogue"], strict=True)
            ]}
            _set_fields(shot, **result)
        case "shot_sound":
            result = {"sound": [
                {"cueId": take["cueId"], "audio": refs[take["slot"]]} for take in data["takes"]
            ]}
            _set_fields(shot, **result)
        case _:
            raise PlanError("Unsupported production workflow result")
    return result
