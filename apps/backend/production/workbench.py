"""Workbench reads and actions on production projects, scoped to one project at a time."""

from django.db import connection, transaction
from django.db.models import Count, F, IntegerField, OuterRef, Q, Subquery
from django.db.models.fields.json import KT
from django.db.models.functions import Cast, Coalesce

from scheduler.models import ChildSlot, Dependency, Execution, Schedule, Task, TaskState, TaskWait
from scheduler.service import PROVIDER_CHECK, StateConflict
from scheduler.worker import configured_scheduler
from studio.pagination import Key, paginate

from .models import Episode, Project, Shot
from .services import create_project


EXECUTION_HISTORY_LIMIT = 20
PROJECT_KEYS = (Key("created_at", "datetime", descending=True), Key("pk", "uuid", descending=True))
TASK_KEYS = (Key("pk", "uuid"),)
TASK_NAMES = {
    "short_drama": "短剧制作",
    "series_plan": "全剧规划",
    "global_assets": "全局关键素材",
    "all_episodes": "制作全部剧集",
    "make_episode": "制作第 {episode} 集",
    "episode_script": "制作分集剧本",
    "episode_breakdown": "分集剧本结构化",
    "episode_assets": "分集关键素材制作",
    "character_asset": "人物素材 · {asset}",
    "scene_asset": "场景素材 · {asset}",
    "prop_asset": "道具素材 · {asset}",
    "all_shot_images": "制作分集全部分镜图",
    "shot_image": "镜头 {shot} 分镜图",
    "all_shots": "制作所有分镜",
    "make_shot": "制作分镜 {shot}",
    "shot_dialogue": "镜头 {shot} 对话音频",
    "shot_video": "镜头 {shot} 视频",
    "shot_sound": "镜头 {shot} 音效",
    "episode_edit": "剪辑与合成",
}
NAME_LENGTH = 200


class ActionConflict(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def task_name(task_type, episode=None, shot=None, asset=None):
    template = TASK_NAMES.get(task_type)
    if template is None:
        return task_type
    return template.format(episode=episode or "?", shot=shot or "?", asset=asset or "?")


def project_name(project):
    if project.title:
        return project.title
    line = next((line.strip() for line in project.idea.splitlines() if line.strip()), "")
    return line[:NAME_LENGTH]


def episode_ref(episode):
    return None if episode is None else {"id": episode.pk, "number": episode.number, "title": episode.title}


def _int_input(path):
    return Cast(KT(path), IntegerField())


# Projects


def _count(condition):
    return Coalesce(Subquery(
        Task.objects.filter(condition, schedule_id=OuterRef("pk")).order_by()
        .values("schedule_id").annotate(total=Count("pk")).values("total"),
    ), 0)


def projects():
    root = Task.objects.filter(schedule_id=OuterRef("pk"), parent__isnull=True)
    current = (
        Task.objects.filter(schedule_id=OuterRef("pk"), task_type="make_episode")
        .exclude(state=TaskState.COMPLETED)
        .annotate(number=_int_input("input__episodeNumber")).order_by("number").values("number")[:1]
    )
    return Project.objects.select_related("schedule").annotate(
        root_id=Subquery(root.values("pk")[:1]),
        root_state=Subquery(root.values("state")[:1]),
        root_progress=Subquery(root.values("progress")[:1]),
        running_count=_count(Q(state=TaskState.RUNNING)),
        timed_out_count=_count(Q(state=TaskState.TIMED_OUT)),
        blocked_count=_count(Q(state=TaskState.WAITING) & ~Q(blocked_reason="")),
        current_episode_number=Subquery(current),
    )


def get_project(project_id):
    """Raise Project.DoesNotExist outside the workbench scope."""
    return projects().get(pk=project_id)


def current_episode(project):
    if project.current_episode_number is None:
        return None
    return Episode.objects.filter(project=project, number=project.current_episode_number).first()


def project_status(project):
    if project.root_state == TaskState.COMPLETED:
        return "completed"
    return "running" if project.schedule.enabled else "paused"


def _summaries(projects):
    numbers = {(project.pk, project.current_episode_number) for project in projects
               if project.current_episode_number is not None}
    episodes = {}
    if numbers:
        condition = Q(pk__in=[])
        for project_id, number in numbers:
            condition |= Q(project_id=project_id, number=number)
        episodes = {(episode.project_id, episode.number): episode for episode in Episode.objects.filter(condition)}
    return [{
        "id": project.pk,
        "name": project_name(project),
        "title": project.title,
        "status": project_status(project),
        "startedAt": project.schedule.started_at,
        "createdAt": project.created_at,
        "progress": float(project.root_progress),
        "rootTaskId": project.root_id,
        "currentEpisode": episode_ref(episodes.get((project.pk, project.current_episode_number))),
        "runningCount": project.running_count,
        "timedOutCount": project.timed_out_count,
        "blockedCount": project.blocked_count,
    } for project in projects]


def project_summary(project):
    return _summaries([project])[0]


def project_list(*, cursor, limit):
    page, next_cursor = paginate(projects(), PROJECT_KEYS, cursor=cursor, limit=limit)
    return {"results": _summaries(page), "nextCursor": next_cursor}


def project_detail(project):
    plan = project.plan
    return {
        **project_summary(project),
        "idea": project.idea,
        "plan": None if plan is None else {
            "world": plan["world"], "visualGuide": plan["visualGuide"], "characters": plan.get("characters", []),
        },
        "episodeCount": project.episodes.count(),
    }


CREATE_LOCK_NAMESPACE = 0x5D05  # Two-key advisory locks do not share the scheduler's bigint key space.


def create(idea, project_id=None):
    """Return (project, created). A replay with the same UUID and idea returns the original project."""
    with transaction.atomic():
        if project_id is not None:
            # Concurrent creations of one UUID queue here, so exactly one reports creation.
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s, hashtext(%s))", [CREATE_LOCK_NAMESPACE, str(project_id)],
                )
        existed = project_id is not None and Project.objects.filter(pk=project_id).exists()
        try:
            project, _ = create_project(idea, project_id=project_id)
        except StateConflict as exc:
            raise ActionConflict("project_conflict", "This project ID already belongs to a different project.") from exc
        return get_project(project.pk), not existed


def set_running(project, running):
    """Switch dispatch and return the summary read under the same schedule lock."""
    # Check completion under the schedule lock that also serializes the root task's completion.
    with transaction.atomic():
        Schedule.objects.select_for_update().get(pk=project.pk)
        if Task.objects.filter(schedule_id=project.pk, parent__isnull=True, state=TaskState.COMPLETED).exists():
            raise ActionConflict("project_completed", "Completed projects cannot be paused or resumed.")
        configured_scheduler().set_enabled(project.pk, running)
        return project_summary(get_project(project.pk))


# Episodes and shots


def _episode_tasks(project):
    return {
        row["number"]: row for row in
        Task.objects.filter(schedule_id=project.pk, task_type="make_episode")
        .annotate(number=_int_input("input__episodeNumber")).values("number", "pk", "state", "progress")
    }


def _episode_summary(episode, task, current_number):
    return {
        **episode_ref(episode),
        "outline": episode.outline,
        "taskId": task["pk"] if task else None,
        "state": task["state"] if task else None,
        "progress": float(task["progress"]) if task else 0.0,
        "shotCount": episode.shot_count,
        "delivered": bool(episode.deliveries),
        "current": episode.number == current_number,
    }


def _episodes(project):
    # Aggregation drops Meta.ordering, so order explicitly.
    return project.episodes.annotate(shot_count=Count("shots")).order_by("number")


def episode_list(project):
    tasks = _episode_tasks(project)
    return {"results": [
        _episode_summary(episode, tasks.get(episode.number), project.current_episode_number)
        for episode in _episodes(project)
    ]}


def get_episode(project, episode_id):
    """Raise Episode.DoesNotExist outside the project."""
    return _episodes(project).get(pk=episode_id)


def episode_detail(project, episode):
    task = _episode_tasks(project).get(episode.number)
    return {**_episode_summary(episode, task, project.current_episode_number), "script": episode.script}


def shot_list(episode):
    return {
        "episode": episode_ref(episode),
        "results": [{
            "id": shot.pk,
            "number": shot.number,
            "sceneId": shot.spec["sceneId"],
            "description": shot.spec["description"],
            "estimatedDurationMs": shot.spec["estimatedDurationMs"],
            "assetKeys": shot.spec["assetKeys"],
            "dialogue": shot.spec["dialogue"],
            "sound": shot.spec["sound"],
        } for shot in Shot.objects.filter(episode=episode)],
    }


# Tasks


def _nodes(queryset):
    bound = (
        ChildSlot.objects.filter(parent_id=OuterRef("pk"), child__isnull=False).order_by()
        .values("parent_id").annotate(total=Count("pk")).values("total")
    )
    return queryset.annotate(
        episode_number=_int_input("input__episodeNumber"),
        shot_number=_int_input("input__shotNumber"),
        asset_title=KT("input__asset__title"),
        slot_key=F("slot__key"),
        slot_order=F("slot__id"),
        child_count=Coalesce(Subquery(bound), 0),
    ).values(
        "pk", "parent_id", "task_type", "state", "blocked_reason", "progress", "child_total",
        "episode_number", "shot_number", "asset_title", "slot_key", "slot_order", "child_count",
    )


def _ref(row):
    return {
        "id": row["pk"],
        "name": task_name(row["task_type"], row["episode_number"], row["shot_number"], row["asset_title"]),
        "taskType": row["task_type"],
        "state": row["state"],
    }


def _node(row):
    blocked = row["blocked_reason"] or None
    pending = blocked == PROVIDER_CHECK
    failed = row["state"] == TaskState.WAITING and blocked is not None
    return {
        **_ref(row),
        "parentId": row["parent_id"],
        "slotKey": row["slot_key"],
        "blockedReason": blocked,
        "canRetry": not pending and (failed or row["state"] == TaskState.TIMED_OUT),
        "providerCheckPending": pending,
        "progress": float(row["progress"]),
        "childTotal": row["child_total"],
        "childCount": row["child_count"],
        "episodeNumber": row["episode_number"],
        "shotNumber": row["shot_number"],
    }


def _tree_order(row):
    return (row["slot_order"] is not None, row["slot_order"] or 0)


def _tasks(project):
    return Task.objects.filter(schedule_id=project.pk)


def _with_ancestors(project, rows):
    """Add every missing ancestor of rows, keyed by task ID."""
    found = {row["pk"]: row for row in rows}
    frontier = {row["parent_id"] for row in rows} - found.keys() - {None}
    while frontier:
        batch = list(_nodes(_tasks(project).filter(pk__in=frontier)))
        found.update((row["pk"], row) for row in batch)
        frontier = {row["parent_id"] for row in batch} - found.keys() - {None}
    return found


def _path(rows, task_id):
    path = []
    parent_id = rows[task_id]["parent_id"]
    while parent_id is not None:
        path.append(rows[parent_id])
        parent_id = rows[parent_id]["parent_id"]
    return path[::-1]


def _task_row(project, task_id):
    """Raise Task.DoesNotExist outside the project."""
    return _nodes(_tasks(project).filter(pk=task_id)).get()


def task_tree(project, view, root_task_id=None):
    if view == "running":
        running = list(_nodes(_tasks(project).filter(state=TaskState.RUNNING)))
        rows = _with_ancestors(project, running)
        if not rows:
            rows = {project.root_id: _task_row(project, project.root_id)}
        return {
            "view": view, "rootTaskId": project.root_id, "path": [],
            "nodes": [_node(row) for row in sorted(rows.values(), key=_tree_order)],
        }
    root = _task_row(project, root_task_id)
    descendants = _tasks(project).filter(Q(parent_id=root["pk"]) | Q(parent__parent_id=root["pk"]))
    rows = [root, *_nodes(descendants)]
    lineage = _with_ancestors(project, [root])
    return {
        "view": view, "rootTaskId": root["pk"],
        "path": [_ref(row) for row in _path(lineage, root["pk"])],
        "nodes": [_node(row) for row in sorted(rows, key=_tree_order)],
    }


def task_list(project, *, state=None, blocked=None, task_type=None, cursor, limit):
    tasks = _tasks(project)
    if state is not None:
        tasks = tasks.filter(state=state)
    if blocked is not None:
        tasks = tasks.exclude(blocked_reason="") if blocked else tasks.filter(blocked_reason="")
    if task_type is not None:
        tasks = tasks.filter(task_type=task_type)
    page, next_cursor = paginate(_nodes(tasks), TASK_KEYS, cursor=cursor, limit=limit)
    rows = _with_ancestors(project, page)
    return {
        "results": [{**_node(row), "path": [ancestor["pk"] for ancestor in _path(rows, row["pk"])]} for row in page],
        "nextCursor": next_cursor,
    }


def _execution(execution, current_id):
    result = execution.result or {}
    return {
        "id": execution.pk,
        "state": execution.state,
        "current": execution.pk == current_id,
        "startedAt": execution.started_at,
        "deadlineAt": execution.deadline_at,
        "endedAt": execution.ended_at,
        "workflowAcceptedAt": execution.workflow_accepted_at,
        "outcome": result.get("outcome"),
        "errorCode": result.get("error_code"),
        "message": result.get("message"),
        "retryable": result.get("retryable"),
        "providerJobs": [
            {"stepKey": key, "provider": job["provider"]} for key, job in sorted(execution.provider_jobs.items())
        ],
        "providerJobsCheckedAt": execution.provider_jobs_checked_at,
    }


def task_detail(project, task_id, *, output_count):
    row = _task_row(project, task_id)
    task = Task.objects.only("attempt_count", "retry_at", "completion_reason", "current_execution_id").get(pk=row["pk"])
    lineage = _with_ancestors(project, [row])
    dependencies = list(Dependency.objects.filter(task_id=task.pk).values_list("prerequisite_id", flat=True))
    waits = list(TaskWait.objects.filter(task_id=task.pk).order_by("slot_id").values_list("slot__key", "slot__child_id"))
    related = {
        other["pk"]: other
        for other in _nodes(_tasks(project).filter(pk__in=[*dependencies, *(child for _, child in waits if child)]))
    }
    executions = Execution.objects.filter(task_id=task.pk).defer("snapshot").order_by("-started_at", "-pk")
    history = [_execution(execution, task.current_execution_id) for execution in executions[:EXECUTION_HISTORY_LIMIT]]
    current = next((item for item in history if item["current"]), None)
    if current is None and task.current_execution_id is not None:
        current = _execution(executions.get(pk=task.current_execution_id), task.current_execution_id)
    return {
        **_node(row),
        "path": [_ref(ancestor) for ancestor in _path(lineage, row["pk"])],
        "attemptCount": task.attempt_count,
        "retryAt": task.retry_at,
        "completionReason": task.completion_reason,
        "dependencies": [
            {"task": _ref(related[prerequisite]), "satisfied": related[prerequisite]["state"] == TaskState.COMPLETED}
            for prerequisite in dependencies
        ],
        "waits": [{
            "slotKey": key,
            "task": _ref(related[child]) if child else None,
            "satisfied": bool(child) and related[child]["state"] == TaskState.COMPLETED,
        } for key, child in waits],
        "currentExecution": current,
        "executions": history,
        "executionCount": executions.count(),
        "outputCount": output_count(task.pk),
    }


def task_ref(project, task_id):
    return _ref(_task_row(project, task_id))


def retry(project, task_id, *, output_count):
    """Retry under the schedule lock and return the detail read before any Worker can claim the task."""
    with transaction.atomic():
        Schedule.objects.select_for_update().get(pk=project.pk)
        row = _task_row(project, task_id)
        if row["blocked_reason"] == PROVIDER_CHECK:
            raise ActionConflict(
                "provider_check_pending", "The workflow and provider jobs must be verified before a retry.",
            )
        try:
            configured_scheduler().retry(project.pk, row["pk"])
        except StateConflict as exc:
            raise ActionConflict("retry_unavailable", "Only failed-blocked or timed-out tasks can be retried.") from exc
        return task_detail(project, row["pk"], output_count=output_count)
