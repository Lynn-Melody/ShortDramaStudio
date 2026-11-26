import json
import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, localcontext
from uuid import UUID, uuid4

from django.db import connection, transaction
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.db.models.functions import Greatest, Now

from .models import ChildSlot, Dependency, Dispatch, DispatchKind, Execution, ExecutionState, Schedule, Task, TaskState, TaskWait
from .tasks import BaseTask, Child, Completed, Failed, InvokeWorkflow, PlanError, TaskRef, WaitForChildren


logger = logging.getLogger(__name__)
LIVE = (ExecutionState.QUEUED, ExecutionState.ACCEPTED)
PROVIDER_CHECK = "provider_jobs_unverified"


class StateConflict(ValueError):
    """A requested action is unavailable in the current task state."""


@dataclass(frozen=True)
class WorkflowAcceptance:
    status: str
    payload: dict | None = None


def _clock():
    with connection.cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        return cursor.fetchone()[0]


def _json_object(value):
    if not isinstance(value, dict):
        raise PlanError("Expected a JSON object")
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise PlanError("Invalid JSON object") from exc


def _valid_key(value, limit=100):
    return isinstance(value, str) and 0 < len(value) <= limit


def _task_ref(task):
    return TaskRef(task.id, task.task_type, task.definition_version, task.input)


def _initialize(task_class, input, *, parent=None):
    try:
        instance = task_class(input, parent=parent)
        instance.on_init()
    except Exception as exc:
        raise PlanError(f"Task initialization failed: {task_class.task_type}") from exc
    try:
        keys = tuple(instance.child_keys)
    except TypeError as exc:
        raise PlanError("Child keys must be a sequence") from exc
    if any(not _valid_key(key) for key in keys) or len(set(keys)) != len(keys):
        raise PlanError("Child keys must be nonempty and unique")
    if isinstance(instance.child_keys, (str, bytes)) or type(instance.child_total) is not int or instance.child_total != len(keys):
        raise PlanError("child_total must match the reserved child keys")
    return keys, _json_object(instance.checkpoint)


def _new_task(schedule, task_class, input, definition, *, parent=None):
    keys, checkpoint = definition
    task = Task.objects.create(
        schedule=schedule, parent=parent, task_type=task_class.task_type,
        definition_version=task_class.definition_version, child_total=len(keys),
        input=input, checkpoint=checkpoint,
    )
    ChildSlot.objects.bulk_create(ChildSlot(parent=task, key=key) for key in keys)
    return task


def _snapshot(task):
    parent = task.parent
    child_outputs = task.slots.filter(child__state=TaskState.COMPLETED).values_list("key", "child__output")
    dependency_outputs = task.dependencies.values_list("prerequisite_id", "prerequisite__output")
    return {
        "input": task.input,
        "checkpoint": task.checkpoint,
        "parent": {"id": str(parent.id), "task_type": parent.task_type,
                   "definition_version": parent.definition_version, "input": parent.input} if parent else None,
        "child_outputs": dict(child_outputs),
        "dependency_outputs": {str(key): output for key, output in dependency_outputs},
    }


def _validate_acyclic(schedule_id):
    prerequisites = defaultdict(set)
    dependents = defaultdict(set)
    edges = list(Dependency.objects.filter(task__schedule_id=schedule_id).values_list("task_id", "prerequisite_id"))
    # A parent's final completion requires every bound child, even if its current wait is narrower.
    edges.extend(ChildSlot.objects.filter(parent__schedule_id=schedule_id, child__isnull=False)
                 .values_list("parent_id", "child_id"))
    for task_id, prerequisite_id in edges:
        prerequisites[task_id].add(prerequisite_id)
        dependents[prerequisite_id].add(task_id)
    ready = deque(dependents.keys() - prerequisites.keys())
    while ready:
        prerequisite = ready.popleft()
        for task_id in dependents[prerequisite]:
            pending = prerequisites[task_id]
            pending.remove(prerequisite)
            if not pending:
                del prerequisites[task_id]
                ready.append(task_id)
    if prerequisites:
        raise PlanError("Task dependencies and waits must be acyclic")


def _update_ancestor_progress(task):
    parent_id = task.parent_id
    with localcontext() as context:
        context.prec = 48
        while parent_id is not None:
            parent = Task.objects.get(pk=parent_id)
            values = parent.slots.filter(child__isnull=False).values_list("child__progress", flat=True)
            parent.progress = sum(values, Decimal(0)) / parent.child_total
            parent.save(update_fields=["progress"])
            parent_id = parent.parent_id


class Scheduler:
    def __init__(self, registry):
        self.registry = registry

    def create_schedule(self, root_class, input, *, schedule_id=None):
        """Create the disabled schedule and root. May join the domain project's transaction."""
        self._require_registered(root_class)
        input = _json_object(input)
        definition = _initialize(root_class, input)
        with transaction.atomic():
            kwargs = {} if schedule_id is None else {"id": schedule_id}
            schedule = Schedule.objects.create(definition_version=root_class.definition_version, **kwargs)
            root = _new_task(schedule, root_class, input, definition)
        return schedule, root

    def _require_registered(self, task_class):
        if not isinstance(task_class, type) or not issubclass(task_class, BaseTask):
            raise PlanError("Task definitions must inherit BaseTask")
        if self.registry.resolve(task_class.task_type, task_class.definition_version) is not task_class:
            raise PlanError("Task class does not match the registered definition")

    @transaction.atomic
    def set_enabled(self, schedule_id, enabled):
        schedule = Schedule.objects.select_for_update().get(pk=schedule_id)
        if schedule.enabled != enabled:
            schedule.enabled = enabled
            if enabled and schedule.started_at is None:
                schedule.started_at = _clock()
            schedule.save(update_fields=["enabled", "started_at"])
        return schedule

    def _ready_tasks(self):
        dependencies = Dependency.objects.filter(task_id=OuterRef("pk"))
        waits = TaskWait.objects.filter(task_id=OuterRef("pk"))
        running = (
            Task.objects.filter(schedule_id=OuterRef("schedule_id"), task_type=OuterRef("task_type"), state=TaskState.RUNNING)
            .order_by().values("task_type").annotate(total=Count("pk")).values("total")
        )
        eligible_type = Q(pk__in=[])
        for task_class in self.registry:
            kind = Q(task_type=task_class.task_type, definition_version=task_class.definition_version)
            limit = task_class.max_running_per_project
            if limit is not None:
                kind &= Q(running_peers__isnull=True) | Q(running_peers__lt=limit)
            eligible_type |= kind
        return (
            Task.objects.filter(state=TaskState.WAITING, blocked_reason="")
            .filter(Q(retry_at__isnull=True) | Q(retry_at__lte=Now()))
            .filter(
                ~Exists(dependencies.exclude(prerequisite__state=TaskState.COMPLETED)),
                ~Exists(waits.filter(Q(slot__child__isnull=True) | ~Q(slot__child__state=TaskState.COMPLETED))),
            )
            .alias(running_peers=Subquery(running)).filter(eligible_type)
            .annotate(ready_since=Greatest(
                "waiting_since", "retry_at",
                Subquery(dependencies.order_by("-prerequisite__current_execution__ended_at").values(
                    "prerequisite__current_execution__ended_at")[:1]),
                Subquery(waits.order_by("-slot__child__current_execution__ended_at").values(
                    "slot__child__current_execution__ended_at")[:1]),
            )).order_by("ready_since", "pk")
        )

    @transaction.atomic
    def claim_next(self, *, schedule_id=None):
        ready = self._ready_tasks()
        schedules = Schedule.objects.filter(enabled=True)
        if schedule_id is not None:
            schedules = schedules.filter(pk=schedule_id)
        next_ready = ready.filter(schedule_id=OuterRef("pk")).values("ready_since")[:1]
        schedule = (
            schedules.annotate(next_ready=Subquery(next_ready)).filter(next_ready__isnull=False)
            .order_by("next_ready", "pk").select_for_update(skip_locked=True, of=("self",)).first()
        )
        if schedule is None:
            return None
        task = ready.filter(schedule=schedule).select_related("parent").first()
        if task is None:
            return None
        task_class = self.registry.resolve(task.task_type, task.definition_version)
        now = _clock()
        execution = Execution.objects.create(
            task=task, snapshot=_snapshot(task), started_at=now, deadline_at=now + task_class.timeout,
        )
        task.waits.all().delete()
        task.state = TaskState.RUNNING
        task.current_execution = execution
        task.attempt_count += 1
        task.retry_at = None
        task.save(update_fields=["state", "current_execution", "attempt_count", "retry_at"])
        Dispatch.objects.create(execution=execution, kind=DispatchKind.LOCAL, available_at=now)
        logger.info("Task claimed", extra={"project_id": schedule.id, "task_id": task.id, "execution_id": execution.id})
        return execution

    def task_instance(self, execution):
        task_class = self.registry.resolve(execution.task.task_type, execution.task.definition_version)
        snapshot = dict(execution.snapshot)
        if snapshot["parent"] is not None:
            parent = snapshot["parent"]
            snapshot["parent"] = TaskRef(**{**parent, "id": UUID(parent["id"])})
        instance = task_class(**snapshot)
        instance.child_total = execution.task.child_total
        instance.child_keys = tuple(execution.task.slots.order_by("id").values_list("key", flat=True))
        return instance

    def _prepare_children(self, parent, outcome):
        slots = {slot.key: slot for slot in parent.slots.all()}
        definitions = {}
        if (not isinstance(outcome.wait_for, (list, tuple)) or not outcome.wait_for
                or any(not _valid_key(key) for key in outcome.wait_for)):
            raise PlanError("A wait must name at least one valid child slot")
        if len(set(outcome.wait_for)) != len(outcome.wait_for):
            raise PlanError("Wait slot keys must be unique")
        if not isinstance(outcome.children, (list, tuple)):
            raise PlanError("Expected a sequence of child definitions")
        for child in outcome.children:
            if not isinstance(child, Child):
                raise PlanError("Expected a child definition")
            if not _valid_key(child.key) or child.key not in slots or child.key in definitions:
                raise PlanError(f"Unreserved or duplicate child slot: {child.key!r}")
            self._require_registered(child.task_class)
            if not isinstance(child.depends_on, (list, tuple)) or any(
                not isinstance(ref, UUID) and not _valid_key(ref) for ref in child.depends_on
            ):
                raise PlanError("Dependencies must be child keys or task UUIDs")
            input = _json_object(child.input)
            definition = None if slots[child.key].child_id else _initialize(child.task_class, input, parent=_task_ref(parent))
            definitions[child.key] = (child, input, definition)
        return definitions

    def _apply_wait(self, task, outcome, definitions):
        slots = {slot.key: slot for slot in task.slots.select_related("child")}
        new_keys = set()
        for key, (child, input, definition) in definitions.items():
            slot = slots[key]
            existing = slot.child
            if existing:
                if (existing.task_type, existing.definition_version, existing.input) != (
                    child.task_class.task_type, child.task_class.definition_version, input,
                ):
                    raise PlanError(f"Conflicting definition for slot {key!r}")
            else:
                slot.child = _new_task(task.schedule, child.task_class, input, definition, parent=task)
                slot.save(update_fields=["child"])
                new_keys.add(key)

        for key, (child, _, _) in definitions.items():
            child_id = slots[key].child_id
            prerequisites = set()
            for ref in child.depends_on:
                if isinstance(ref, str):
                    if ref not in slots or slots[ref].child_id is None:
                        raise PlanError(f"Unknown prerequisite slot: {ref!r}")
                    ref = slots[ref].child_id
                prerequisites.add(ref)
            if child_id in prerequisites or Task.objects.filter(schedule=task.schedule, pk__in=prerequisites).count() != len(prerequisites):
                raise PlanError("Dependency endpoints must be different tasks in the same schedule")
            if key in new_keys:
                Dependency.objects.bulk_create(Dependency(task_id=child_id, prerequisite_id=ref) for ref in prerequisites)
            elif set(Dependency.objects.filter(task_id=child_id).values_list("prerequisite_id", flat=True)) != prerequisites:
                raise PlanError(f"Conflicting dependencies for slot {key!r}")

        if any(key not in slots or slots[key].child_id is None for key in outcome.wait_for):
            raise PlanError("Waits must reference bound direct children")
        TaskWait.objects.bulk_create(TaskWait(task=task, slot=slots[key]) for key in outcome.wait_for)
        _validate_acyclic(task.schedule_id)
        task.checkpoint = _json_object(outcome.checkpoint)

    def _locked_execution(self, execution_id):
        schedule_id = Execution.objects.values_list("task__schedule_id", flat=True).get(pk=execution_id)
        Schedule.objects.select_for_update().get(pk=schedule_id)
        return Execution.objects.select_related("task").get(pk=execution_id)

    @staticmethod
    def _current(execution):
        return execution.task.current_execution_id == execution.id

    def _end(self, execution, state, result, now):
        execution.state = state
        execution.result = result
        execution.ended_at = now
        execution.save(update_fields=["state", "result", "ended_at"])
        context = {"project_id": execution.task.schedule_id, "task_id": execution.task_id,
                   "execution_id": execution.id, "execution_state": state,
                   "duration_seconds": (now - execution.started_at).total_seconds()}
        transaction.on_commit(lambda: logger.info("Execution ended", extra=context))

    def _complete(self, execution, outcome, now, record_outputs=None):
        task = execution.task
        if task.slots.filter(Q(child__isnull=True) | ~Q(child__state=TaskState.COMPLETED)).exists():
            raise PlanError("All child slots must be completed before the parent")
        if not isinstance(outcome.reason, str) or len(outcome.reason) > 200:
            raise PlanError("Invalid completion reason")
        output = _json_object(outcome.output)
        if record_outputs is not None:
            output = _json_object(record_outputs(execution, output))
        self._end(execution, ExecutionState.SUCCEEDED, {"outcome": "completed", "output": output}, now)
        task.state, task.output, task.progress = TaskState.COMPLETED, output, Decimal(1)
        task.retry_at, task.blocked_reason, task.completion_reason = None, "", outcome.reason
        task.save(update_fields=["state", "output", "progress", "retry_at", "blocked_reason", "completion_reason"])
        _update_ancestor_progress(task)

    def _retry_or_block(self, execution, now, *, code, retryable, expired=False):
        task = execution.task
        policy = self.registry.resolve(task.task_type, task.definition_version).retry_policy
        task.state = TaskState.TIMED_OUT if expired else TaskState.WAITING
        task.blocked_reason, task.retry_at = ("" if expired else code), None
        if retryable and execution.provider_jobs and execution.provider_jobs_checked_at is None:
            task.blocked_reason = PROVIDER_CHECK
        elif retryable and task.attempt_count < policy.max_attempts:
            task.state = TaskState.WAITING
            task.blocked_reason = ""
            task.retry_at = now + policy.delay(task.attempt_count)
            task.waiting_since = now
        task.save(update_fields=["state", "blocked_reason", "retry_at", "waiting_since"])

    def _fail(self, execution, outcome, now, *, retryable=None):
        if not _valid_key(outcome.error_code) or not isinstance(outcome.message, str):
            raise PlanError("Invalid failure result")
        task = execution.task
        policy = self.registry.resolve(task.task_type, task.definition_version).retry_policy
        if retryable is None:
            retryable = outcome.error_code in policy.retryable_error_codes
        self._end(execution, ExecutionState.FAILED, {
            "outcome": "failed", "error_code": outcome.error_code, "message": outcome.message,
        }, now)
        self._retry_or_block(execution, now, code=outcome.error_code, retryable=retryable)

    def _expire(self, execution, now):
        self._end(execution, ExecutionState.EXPIRED, {"outcome": "expired"}, now)
        self._retry_or_block(execution, now, code="timeout", retryable=True, expired=True)

    @transaction.atomic
    def retry(self, schedule_id, task_id):
        Schedule.objects.select_for_update().get(pk=schedule_id)
        task = Task.objects.get(pk=task_id, schedule_id=schedule_id)
        if task.blocked_reason == PROVIDER_CHECK:
            raise StateConflict("Verify the recorded provider jobs before retrying")
        if task.state != TaskState.TIMED_OUT and not (task.state == TaskState.WAITING and task.blocked_reason):
            raise StateConflict("Only failed or timed-out tasks can be retried")
        task.state, task.blocked_reason, task.retry_at, task.attempt_count = TaskState.WAITING, "", None, 0
        task.waiting_since = _clock()
        task.save(update_fields=["state", "blocked_reason", "retry_at", "attempt_count", "waiting_since"])
        return task

    @transaction.atomic
    def reap_one(self):
        due = Execution.objects.filter(state__in=LIVE, deadline_at__lte=Now()).order_by("deadline_at", "pk")
        next_due = due.filter(task__schedule_id=OuterRef("pk")).values("deadline_at")[:1]
        schedule = (
            Schedule.objects.annotate(next_due=Subquery(next_due)).filter(next_due__isnull=False)
            .order_by("next_due", "pk").select_for_update(skip_locked=True, of=("self",)).first()
        )
        if schedule is None:
            return None
        execution = due.filter(task__schedule=schedule).select_related("task").first()
        now = _clock()
        if execution is None or not self._current(execution) or execution.deadline_at > now:
            return None
        self._expire(execution, now)
        return execution.id

    @transaction.atomic
    def lease_dispatch(self, kinds, *, duration):
        claimable = (
            Dispatch.objects.filter(kind__in=kinds, delivered_at__isnull=True, available_at__lte=Now(),
                                    execution__state__in=LIVE, execution__deadline_at__gt=Now())
            .filter(Q(lease_until__isnull=True) | Q(lease_until__lte=Now()))
            .order_by("available_at", "pk")
        )
        first = claimable.filter(execution__task__schedule_id=OuterRef("pk")).values("available_at")[:1]
        schedule = (
            Schedule.objects.annotate(first_dispatch=Subquery(first)).filter(first_dispatch__isnull=False)
            .order_by("first_dispatch", "pk").select_for_update(skip_locked=True, of=("self",)).first()
        )
        if schedule is None:
            return None
        dispatch = claimable.filter(execution__task__schedule=schedule).select_related("execution", "execution__task").first()
        if dispatch is None or not self._current(dispatch.execution):
            return None
        now = _clock()
        if dispatch.execution.deadline_at <= now:
            self._expire(dispatch.execution, now)
            return None
        dispatch.lease_id, dispatch.lease_until = uuid4(), min(now + duration, dispatch.execution.deadline_at)
        dispatch.save(update_fields=["lease_id", "lease_until"])
        if dispatch.kind == DispatchKind.LOCAL and dispatch.execution.state == ExecutionState.QUEUED:
            dispatch.execution.state = ExecutionState.ACCEPTED
            dispatch.execution.save(update_fields=["state"])
        return dispatch

    @staticmethod
    def _deliver(dispatch, now):
        dispatch.delivered_at, dispatch.lease_until, dispatch.lease_id = now, None, None
        dispatch.save(update_fields=["delivered_at", "lease_until", "lease_id"])

    @staticmethod
    def _leased(dispatch, lease_id):
        return lease_id is not None and dispatch.lease_id == lease_id and dispatch.delivered_at is None

    def submit_local(self, execution_id, lease_id, outcome):
        """Apply a decision from the local dispatcher. Child initialization runs before locking."""
        definitions = None
        if isinstance(outcome, WaitForChildren):
            dispatch = Dispatch.objects.select_related("execution__task").get(execution_id=execution_id, kind=DispatchKind.LOCAL)
            if self._leased(dispatch, lease_id):
                definitions = self._prepare_children(dispatch.execution.task, outcome)
        with transaction.atomic():
            execution = self._locked_execution(execution_id)
            dispatch = execution.dispatches.get(kind=DispatchKind.LOCAL)
            if not self._current(execution) or execution.state == ExecutionState.EXPIRED:
                return "expired"
            if execution.state in (ExecutionState.SUCCEEDED, ExecutionState.FAILED) or dispatch.delivered_at:
                return "already_processed"
            if not self._leased(dispatch, lease_id) or execution.state != ExecutionState.ACCEPTED:
                return "stale_lease"
            now = _clock()
            if execution.deadline_at <= now:
                self._expire(execution, now)
                return "expired"
            task = execution.task
            if isinstance(outcome, WaitForChildren):
                self._apply_wait(task, outcome, definitions)
                task.state, task.waiting_since, task.attempt_count = TaskState.WAITING, now, 0
                task.save(update_fields=["state", "waiting_since", "attempt_count", "checkpoint"])
                self._end(execution, ExecutionState.SUCCEEDED, {"outcome": "wait_for_children"}, now)
            elif isinstance(outcome, Completed):
                self._complete(execution, outcome, now)
            elif isinstance(outcome, Failed):
                self._fail(execution, outcome, now)
            elif isinstance(outcome, InvokeWorkflow):
                if not _valid_key(outcome.name):
                    raise PlanError("Invalid workflow name")
                Dispatch.objects.create(
                    execution=execution, kind=DispatchKind.WORKFLOW, available_at=now,
                    payload={"name": outcome.name, "input": _json_object(outcome.input)},
                )
            else:
                raise PlanError("Unknown task outcome")
            self._deliver(dispatch, now)
            return "applied"

    @transaction.atomic
    def defer_local(self, dispatch):
        """Leave a reclaimed decision available when its former worker is still running."""
        execution = self._locked_execution(dispatch.execution_id)
        current = execution.dispatches.get(kind=DispatchKind.LOCAL)
        if self._leased(current, dispatch.lease_id):
            current.lease_id, current.lease_until = None, None
            current.available_at = _clock() + timedelta(seconds=1)
            current.save(update_fields=["lease_id", "lease_until", "available_at"])

    @transaction.atomic
    def finish_delivery(self, dispatch, *, error_code=None, retryable=True):
        execution = self._locked_execution(dispatch.execution_id)
        current = execution.dispatches.get(kind=DispatchKind.WORKFLOW)
        if not self._current(execution) or execution.state not in LIVE or not self._leased(current, dispatch.lease_id):
            return
        now = _clock()
        if execution.deadline_at <= now:
            self._expire(execution, now)
        elif error_code is None:
            self._deliver(current, now)
        elif not retryable:
            self._fail(execution, Failed(error_code), now, retryable=False)
            self._deliver(current, now)
        else:
            policy = self.registry.resolve(execution.task.task_type, execution.task.definition_version).retry_policy
            current.failures += 1
            current.available_at = min(now + policy.delay(current.failures), execution.deadline_at)
            current.lease_id, current.lease_until = None, None
            current.save(update_fields=["failures", "available_at", "lease_id", "lease_until"])

    @transaction.atomic
    def accept_workflow(self, execution_id):
        execution = self._locked_execution(execution_id)
        now = _clock()
        if not self._current(execution) or execution.state == ExecutionState.EXPIRED:
            return WorkflowAcceptance("expired")
        if execution.state not in LIVE:
            return WorkflowAcceptance("already_processed")
        if execution.deadline_at <= now:
            self._expire(execution, now)
            return WorkflowAcceptance("expired")
        dispatch = execution.dispatches.filter(kind=DispatchKind.WORKFLOW).first()
        if dispatch is None:
            return WorkflowAcceptance("not_ready")
        if execution.workflow_accepted_at is not None:
            return WorkflowAcceptance("already_accepted")
        execution.workflow_accepted_at = now
        execution.save(update_fields=["workflow_accepted_at"])
        if dispatch.delivered_at is None:
            self._deliver(dispatch, now)
        return WorkflowAcceptance("accepted", dispatch.payload["input"])

    def workflow_is_current(self, execution_id):
        return Execution.objects.filter(
            pk=execution_id, task__current_execution_id=execution_id, state=ExecutionState.ACCEPTED,
            workflow_accepted_at__isnull=False, deadline_at__gt=Now(),
        ).exists()

    def _workflow_result_status(self, execution, outcome):
        if not self._current(execution):
            return "expired"
        if execution.state in (ExecutionState.SUCCEEDED, ExecutionState.FAILED):
            return "already_processed"
        if execution.workflow_accepted_at is None:
            return "not_accepted"
        if execution.state == ExecutionState.EXPIRED and not isinstance(outcome, Completed):
            return "expired"
        return None

    def submit_workflow_result(self, execution_id, outcome, *, record_outputs=None):
        """Map a workflow result outside the lock, then atomically record it.

        record_outputs, when provided, writes database rows only and returns the
        final output references. Validation involving the network belongs to the caller.
        """
        if not isinstance(outcome, (Completed, Failed)):
            raise PlanError("Workflow results must complete or fail")
        execution = Execution.objects.select_related("task").get(pk=execution_id)
        status = self._workflow_result_status(execution, outcome)
        if status is not None:
            return status
        mapped = self.task_instance(execution).on_workflow_result(outcome)
        if not isinstance(mapped, (Completed, Failed)):
            raise PlanError("Workflow hooks must return completion or failure")
        with transaction.atomic():
            execution = self._locked_execution(execution_id)
            status = self._workflow_result_status(execution, mapped)
            if status is not None:
                return status
            now = _clock()
            if execution.state in LIVE and execution.deadline_at <= now:
                self._expire(execution, now)
            if execution.state == ExecutionState.EXPIRED and not isinstance(mapped, Completed):
                return "expired"
            if isinstance(mapped, Completed):
                self._complete(execution, mapped, now, record_outputs)
            else:
                self._fail(execution, mapped, now)
            return "applied"

    @transaction.atomic
    def register_provider_job(self, execution_id, step_key, provider, job_id):
        if not _valid_key(step_key) or not _valid_key(provider) or not _valid_key(job_id, 300):
            raise PlanError("Invalid provider job information")
        execution = self._locked_execution(execution_id)
        job = {"provider": provider, "job_id": job_id}
        if step_key in execution.provider_jobs:
            if execution.provider_jobs[step_key] != job:
                raise StateConflict("A different job already exists for this step")
            return
        if not self._current(execution) or execution.workflow_accepted_at is None or execution.state not in (
            ExecutionState.ACCEPTED, ExecutionState.EXPIRED,
        ):
            raise StateConflict("Provider jobs require the current, accepted workflow")
        execution.provider_jobs[step_key] = job
        execution.provider_jobs_checked_at = None
        execution.save(update_fields=["provider_jobs", "provider_jobs_checked_at"])
        if execution.state == ExecutionState.EXPIRED:
            self._retry_or_block(execution, _clock(), code="timeout", retryable=True, expired=True)

    @transaction.atomic
    def confirm_provider_jobs_inactive(self, execution_id, *, jobs):
        """T03 calls after checking that the exact recorded provider jobs cannot produce a result."""
        execution = self._locked_execution(execution_id)
        if not self._current(execution) or execution.task.blocked_reason != PROVIDER_CHECK or jobs != execution.provider_jobs:
            raise StateConflict("Provider jobs no longer match the blocked execution")
        now = _clock()
        execution.provider_jobs_checked_at = now
        execution.save(update_fields=["provider_jobs_checked_at"])
        expired = execution.state == ExecutionState.EXPIRED
        code = "timeout" if expired else execution.result["error_code"]
        self._retry_or_block(execution, now, code=code, retryable=True, expired=expired)
