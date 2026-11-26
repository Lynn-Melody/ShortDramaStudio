from uuid import uuid4

from django.db import models
from django.db.models import Q
from django.utils import timezone


class TaskState(models.TextChoices):
    WAITING = "waiting"
    RUNNING = "running"
    TIMED_OUT = "timed_out"
    COMPLETED = "completed"


class ExecutionState(models.TextChoices):
    QUEUED = "queued"
    ACCEPTED = "accepted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"


class DispatchKind(models.TextChoices):
    LOCAL = "local"
    WORKFLOW = "workflow"


class Schedule(models.Model):
    """Scheduler-owned project scope and lock. Domain projects can share its UUID."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    definition_version = models.CharField(max_length=64)
    enabled = models.BooleanField(default=False)
    started_at = models.DateTimeField(null=True)


class Task(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, related_name="tasks")
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, related_name="children")
    task_type = models.CharField(max_length=100)
    definition_version = models.CharField(max_length=64)
    child_total = models.PositiveIntegerField()
    input = models.JSONField(default=dict)
    checkpoint = models.JSONField(default=dict)
    output = models.JSONField(null=True)
    state = models.CharField(max_length=12, choices=TaskState, default=TaskState.WAITING)
    blocked_reason = models.CharField(max_length=100, blank=True, default="")
    completion_reason = models.CharField(max_length=200, blank=True, default="")
    progress = models.DecimalField(max_digits=29, decimal_places=28, default=0)
    current_execution = models.ForeignKey("Execution", on_delete=models.SET_NULL, null=True, related_name="+")
    attempt_count = models.PositiveIntegerField(default=0)
    retry_at = models.DateTimeField(null=True)
    waiting_since = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["schedule"], condition=Q(parent__isnull=True), name="scheduler_one_root"),
            models.CheckConstraint(condition=Q(progress__gte=0, progress__lte=1), name="scheduler_progress_range"),
        ]
        indexes = [models.Index(fields=["schedule", "state", "waiting_since"])]


class ChildSlot(models.Model):
    parent = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="slots")
    key = models.CharField(max_length=100)
    child = models.OneToOneField(Task, null=True, on_delete=models.CASCADE, related_name="slot")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["parent", "key"], name="scheduler_unique_slot")]


class Dependency(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="dependencies")
    prerequisite = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["task", "prerequisite"], name="scheduler_unique_dependency"),
            models.CheckConstraint(condition=~Q(task=models.F("prerequisite")), name="scheduler_no_self_dependency"),
        ]


class TaskWait(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="waits")
    slot = models.ForeignKey(ChildSlot, on_delete=models.CASCADE)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["task", "slot"], name="scheduler_unique_wait")]


class Execution(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="executions")
    state = models.CharField(max_length=9, choices=ExecutionState, default=ExecutionState.QUEUED)
    snapshot = models.JSONField()
    started_at = models.DateTimeField()
    deadline_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True)
    result = models.JSONField(null=True)
    workflow_accepted_at = models.DateTimeField(null=True)
    provider_jobs = models.JSONField(default=dict)
    provider_jobs_checked_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["task"], condition=Q(state__in=["queued", "accepted"]), name="scheduler_one_live_execution"
            ),
        ]
        indexes = [models.Index(fields=["state", "deadline_at"])]


class Dispatch(models.Model):
    execution = models.ForeignKey(Execution, on_delete=models.CASCADE, related_name="dispatches")
    kind = models.CharField(max_length=8, choices=DispatchKind)
    payload = models.JSONField(default=dict)
    available_at = models.DateTimeField()
    lease_until = models.DateTimeField(null=True)
    lease_id = models.UUIDField(null=True)
    delivered_at = models.DateTimeField(null=True)
    failures = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["execution", "kind"], name="scheduler_unique_dispatch_kind")]
        indexes = [models.Index(fields=["kind", "delivered_at", "available_at"])]
