"""Public workbench API v1 serializers. Responses are rendered through these classes."""

from drf_spectacular.extensions import OpenApiSerializerExtension
from rest_framework import serializers as s
from rest_framework.exceptions import ValidationError

from .production_v1 import Object, Text, UUID


TASK_STATES = ("waiting", "running", "timed_out", "completed")
EXECUTION_STATES = ("queued", "accepted", "succeeded", "failed", "expired")
EXECUTION_OUTCOMES = ("completed", "failed", "expired", "wait_for_children")
PROJECT_STATUSES = ("running", "paused", "completed")
ASSET_KINDS = ("character", "scene", "prop", "image", "audio", "video", "subtitles", "timeline")
VISUAL_KINDS = ("character", "scene", "prop")
KIND_FILTERS = ("visual", *ASSET_KINDS, "delivery")
ASSET_SCOPES = ("current", "shared", "all", "episode")
RESOLVED_SCOPES = ("shared", "all", "episode")
DOCUMENT_KINDS = ("plan", "script", "breakdown")
ERROR_CODES = ("project_conflict", "project_completed", "retry_unavailable", "provider_check_pending")
IDEA_MAX_LENGTH = 10_000
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def output(serializer_class, value):
    return serializer_class(value).data


# Requests


class SessionLogin(Object):
    username = Text(max_length=150)
    password = Text(max_length=4096, trim_whitespace=False)


class ProjectCreate(Object):
    idea = Text(max_length=IDEA_MAX_LENGTH, help_text="Short drama idea. Leading and trailing spaces are removed.")
    projectId = UUID(required=False, help_text="Optional client UUID. Replaying the same idea returns the project.")


class ListQuery(s.Serializer):
    cursor = s.CharField(required=False, max_length=1000, help_text="nextCursor from the previous page.")
    limit = s.IntegerField(required=False, min_value=1, max_value=MAX_LIMIT, default=DEFAULT_LIMIT)


class TaskTreeQuery(s.Serializer):
    view = s.ChoiceField(choices=("running", "subtree"), default="running")
    rootTaskId = s.UUIDField(required=False, help_text="Required for view=subtree.")

    def validate(self, attrs):
        if attrs["view"] == "subtree" and "rootTaskId" not in attrs:
            raise ValidationError({"rootTaskId": "Required for the subtree view."})
        if attrs["view"] == "running" and "rootTaskId" in attrs:
            raise ValidationError({"rootTaskId": "Only the subtree view accepts a root task."})
        return attrs


class TaskListQuery(ListQuery):
    state = s.ChoiceField(choices=TASK_STATES, required=False)
    blocked = s.BooleanField(required=False, allow_null=True, default=None,
                             help_text="true: failed or awaiting provider check; false: not blocked.")
    taskType = s.RegexField(r"\A[a-z_]{1,100}\Z", required=False)


class AssetQuery(ListQuery):
    scope = s.ChoiceField(choices=ASSET_SCOPES, default="current")
    episodeId = s.IntegerField(required=False, min_value=1, help_text="Required for scope=episode.")
    shotId = s.IntegerField(required=False, min_value=1)
    kind = s.ChoiceField(choices=KIND_FILTERS, required=False,
                         help_text="visual covers character, scene and prop. delivery covers episode deliveries.")
    q = s.CharField(required=False, max_length=200, help_text="Case-insensitive title or asset key search.")

    def validate(self, attrs):
        if (attrs["scope"] == "episode") != ("episodeId" in attrs):
            raise ValidationError({"episodeId": "Use episodeId exactly when scope=episode."})
        return attrs


class DeliveryQuery(s.Serializer):
    episodeId = s.IntegerField(required=False, min_value=1)


# Responses


class Error(s.Serializer):
    detail = s.CharField()
    code = s.ChoiceField(choices=ERROR_CODES, required=False, help_text="Present on 409 conflicts.")


class FieldErrors(s.Serializer):
    """400 body: messages keyed by request field, cursor, or non_field_errors."""


class FieldErrorsSchema(OpenApiSerializerExtension):
    target_class = FieldErrors

    def map_serializer(self, auto_schema, direction):
        return {
            "type": "object",
            "description": "Validation messages keyed by field name, cursor or non_field_errors.",
            "additionalProperties": {"type": "array", "items": {"type": "string"}},
        }


class Session(s.Serializer):
    authenticated = s.BooleanField()
    username = s.CharField(allow_null=True)
    csrfToken = s.CharField(help_text="Send as X-CSRFToken on unsafe requests.")


class EpisodeRef(s.Serializer):
    id = s.IntegerField()
    number = s.IntegerField()
    title = s.CharField()


class ShotRef(s.Serializer):
    id = s.IntegerField()
    number = s.IntegerField()


class ProjectSummary(s.Serializer):
    id = s.UUIDField()
    name = s.CharField(help_text="Planned title, or the first line of the idea before planning.")
    title = s.CharField(allow_blank=True)
    status = s.ChoiceField(choices=PROJECT_STATUSES)
    startedAt = s.DateTimeField(allow_null=True, help_text="First resume. Null until production starts.")
    createdAt = s.DateTimeField()
    progress = s.FloatField(min_value=0, max_value=1, help_text="Root task structural progress from 0 to 1.")
    rootTaskId = s.UUIDField()
    currentEpisode = EpisodeRef(allow_null=True)
    runningCount = s.IntegerField()
    timedOutCount = s.IntegerField()
    blockedCount = s.IntegerField(help_text="Waiting tasks blocked by a failure or a pending provider check.")


class ProjectList(s.Serializer):
    results = ProjectSummary(many=True)
    nextCursor = s.CharField(allow_null=True)


class CharacterProfile(s.Serializer):
    name = s.CharField()
    profile = s.CharField()


class ProjectPlan(s.Serializer):
    world = s.CharField()
    visualGuide = s.CharField()
    characters = CharacterProfile(many=True)


class ProjectDetail(ProjectSummary):
    idea = s.CharField()
    plan = ProjectPlan(allow_null=True)
    episodeCount = s.IntegerField()


class EpisodeSummary(EpisodeRef):
    outline = s.CharField()
    taskId = s.UUIDField(allow_null=True, help_text="The make_episode task, once created.")
    state = s.ChoiceField(choices=TASK_STATES, allow_null=True)
    progress = s.FloatField(min_value=0, max_value=1)
    shotCount = s.IntegerField()
    delivered = s.BooleanField()
    current = s.BooleanField()


class EpisodeList(s.Serializer):
    results = EpisodeSummary(many=True)


class EpisodeDetail(EpisodeSummary):
    script = s.CharField(allow_blank=True)


class DialogueLine(s.Serializer):
    lineId = s.CharField()
    speaker = s.CharField()
    delivery = s.CharField()
    text = s.CharField()


class SoundCue(s.Serializer):
    cueId = s.CharField()
    description = s.CharField()


class ShotSummary(ShotRef):
    sceneId = s.CharField()
    description = s.CharField()
    estimatedDurationMs = s.IntegerField()
    assetKeys = s.ListField(child=s.CharField())
    dialogue = DialogueLine(many=True)
    sound = SoundCue(many=True)


class ShotList(s.Serializer):
    episode = EpisodeRef()
    results = ShotSummary(many=True)


class TaskRef(s.Serializer):
    id = s.UUIDField()
    name = s.CharField()
    taskType = s.CharField()
    state = s.ChoiceField(choices=TASK_STATES)


class TaskNode(TaskRef):
    parentId = s.UUIDField(allow_null=True)
    slotKey = s.CharField(allow_null=True, help_text="Reserved position in the parent.")
    blockedReason = s.CharField(allow_null=True, help_text="Failure code, or provider_jobs_unverified.")
    canRetry = s.BooleanField(help_text="Failed-blocked or timed-out, and not awaiting a provider check.")
    providerCheckPending = s.BooleanField()
    progress = s.FloatField(min_value=0, max_value=1)
    childTotal = s.IntegerField()
    childCount = s.IntegerField(help_text="Reserved positions already bound to a child task.")
    episodeNumber = s.IntegerField(allow_null=True)
    shotNumber = s.IntegerField(allow_null=True)


class TaskTree(s.Serializer):
    view = s.ChoiceField(choices=("running", "subtree"))
    rootTaskId = s.UUIDField(help_text="Project root for view=running, otherwise the requested subtree root.")
    path = TaskRef(many=True, help_text="Ancestors of rootTaskId from the project root.")
    nodes = TaskNode(many=True)


class TaskListItem(TaskNode):
    path = s.ListField(child=s.UUIDField(), help_text="Ancestor task IDs from the project root.")


class TaskList(s.Serializer):
    results = TaskListItem(many=True)
    nextCursor = s.CharField(allow_null=True)


class TaskDependency(s.Serializer):
    task = TaskRef()
    satisfied = s.BooleanField()


class TaskWait(s.Serializer):
    slotKey = s.CharField()
    task = TaskRef(allow_null=True)
    satisfied = s.BooleanField()


class ExecutionProviderJob(s.Serializer):
    stepKey = s.CharField()
    provider = s.CharField()


class ExecutionSummary(s.Serializer):
    id = s.UUIDField()
    state = s.ChoiceField(choices=EXECUTION_STATES)
    current = s.BooleanField()
    startedAt = s.DateTimeField()
    deadlineAt = s.DateTimeField()
    endedAt = s.DateTimeField(allow_null=True)
    workflowAcceptedAt = s.DateTimeField(allow_null=True)
    outcome = s.ChoiceField(choices=EXECUTION_OUTCOMES, allow_null=True)
    errorCode = s.CharField(allow_null=True)
    message = s.CharField(allow_null=True, allow_blank=True)
    retryable = s.BooleanField(allow_null=True)
    providerJobs = ExecutionProviderJob(many=True)
    providerJobsCheckedAt = s.DateTimeField(allow_null=True)


class TaskDetail(TaskNode):
    path = TaskRef(many=True, help_text="Ancestors from the project root.")
    attemptCount = s.IntegerField()
    retryAt = s.DateTimeField(allow_null=True, help_text="Scheduled automatic retry.")
    completionReason = s.CharField(allow_blank=True)
    dependencies = TaskDependency(many=True)
    waits = TaskWait(many=True)
    currentExecution = ExecutionSummary(allow_null=True)
    executions = ExecutionSummary(many=True, help_text="Newest first, at most 20.")
    executionCount = s.IntegerField()
    outputCount = s.IntegerField(help_text="Media versions produced by this task.")


class AssetVersion(s.Serializer):
    id = s.UUIDField()
    assetId = s.UUIDField()
    title = s.CharField()
    createdAt = s.DateTimeField()
    contentType = s.CharField()
    sizeBytes = s.IntegerField()
    adopted = s.BooleanField(help_text="Selected by production for downstream use.")
    slot = s.CharField()
    taskId = s.UUIDField()
    taskType = s.CharField()
    executionId = s.UUIDField()


class AssetBrief(s.Serializer):
    id = s.UUIDField()
    assetKey = s.CharField()
    kind = s.ChoiceField(choices=ASSET_KINDS)
    title = s.CharField()
    episode = EpisodeRef(allow_null=True)
    shot = ShotRef(allow_null=True)
    delivery = s.BooleanField(help_text="Part of an episode delivery.")


class AssetCard(AssetBrief):
    versionCount = s.IntegerField()
    latestVersion = AssetVersion()
    adoptedVersionId = s.UUIDField(allow_null=True)


class AssetList(s.Serializer):
    scope = s.ChoiceField(choices=RESOLVED_SCOPES, help_text="current resolves to episode or shared.")
    episode = EpisodeRef(allow_null=True)
    results = AssetCard(many=True)
    nextCursor = s.CharField(allow_null=True)


class AssetVersionList(s.Serializer):
    asset = AssetCard()
    versions = AssetVersion(many=True, help_text="Newest first.")


class TaskOutputVersion(AssetVersion):
    asset = AssetBrief()


class TaskDocument(s.Serializer):
    document = s.ChoiceField(choices=DOCUMENT_KINDS)
    episode = EpisodeRef(allow_null=True)


class TaskOutputs(s.Serializer):
    task = TaskRef()
    versions = TaskOutputVersion(many=True)
    documents = TaskDocument(many=True, help_text="Text products stored on the project or episode.")


class Delivery(s.Serializer):
    id = s.UUIDField()
    createdAt = s.DateTimeField()
    current = s.BooleanField(help_text="Adopted by the episode.")
    taskId = s.UUIDField()
    executionId = s.UUIDField()
    video = AssetVersion()
    subtitles = AssetVersion()
    timeline = AssetVersion()


class EpisodeDeliveries(s.Serializer):
    episode = EpisodeRef()
    deliveries = Delivery(many=True)


class DeliveryList(s.Serializer):
    results = EpisodeDeliveries(many=True)


class AssetPreview(s.Serializer):
    versionId = s.UUIDField()
    method = s.ChoiceField(choices=["GET"])
    url = s.URLField()
    expiresAt = s.DateTimeField()
    contentType = s.CharField()
    sizeBytes = s.IntegerField()
