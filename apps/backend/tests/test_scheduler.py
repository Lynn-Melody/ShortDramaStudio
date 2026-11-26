from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier, Event
from uuid import uuid4

import pytest
from django.db import connections, transaction
from django.db.models.functions import Now
from django.utils import timezone

from scheduler.examples import BatchTask, ValueTask
from scheduler.models import Dependency, Dispatch, DispatchKind, Execution, ExecutionState, Schedule, Task, TaskState
from scheduler.service import PROVIDER_CHECK, Scheduler, StateConflict
from scheduler.tasks import BaseTask, Completed, Failed, PlanError, RetryPolicy, TaskRegistry
from scheduler.worker import DeliveryError, Worker, _decision_lock


pytestmark = pytest.mark.django_db(transaction=True)


class WorkflowTask(BaseTask):
    task_type = "test.workflow"
    timeout = timedelta(minutes=1)
    retry_policy = RetryPolicy(max_attempts=2, retryable_error_codes=frozenset({"provider_busy"}))

    def on_processing(self):
        return self.invoke_workflow("render_fixture", self.input)

    def on_workflow_result(self, result):
        if isinstance(result, Completed):
            return self.finish({"artifact": result.output["result"], "input": self.input["name"]})
        return result


class LimitedValue(ValueTask):
    task_type = "test.limited"
    max_running_per_project = 1


class NewValue(ValueTask):
    definition_version = "2"

    def on_processing(self):
        return self.finish({"value": self.input["value"] * 10})


@pytest.fixture
def scheduler():
    return Scheduler(TaskRegistry(BatchTask, ValueTask, NewValue, WorkflowTask, LimitedValue))


def schedule_task(scheduler, root_class=BatchTask, payload=None):
    schedule, task = scheduler.create_schedule(root_class, payload or {"seed": 7})
    scheduler.set_enabled(schedule.id, True)
    return schedule, task


def claim_local(scheduler, schedule):
    execution = scheduler.claim_next(schedule_id=schedule.id)
    assert execution is not None
    dispatch = scheduler.lease_dispatch([DispatchKind.LOCAL], duration=timedelta(seconds=30))
    assert dispatch.execution_id == execution.id
    return execution, dispatch


def expire(scheduler, execution):
    Execution.objects.filter(pk=execution.id).update(deadline_at=Now())
    assert scheduler.reap_one() == execution.id


def allow_retry(task):
    Task.objects.filter(pk=task.id).update(retry_at=Now())


def run_in_connection(function):
    try:
        return function()
    finally:
        connections.close_all()


def test_workflow_of_local_tasks_resumes_with_fixed_slots_and_equal_progress(scheduler):
    schedule, task = scheduler.create_schedule(BatchTask, {"seed": 7, "skip_right": True})
    worker = Worker(scheduler)
    assert not worker.tick()
    assert task.child_total == 3 and task.children.count() == 0
    assert task.slots.count() == 3
    scheduler.set_enabled(schedule.id, True)
    assert worker.tick()
    task.refresh_from_db()
    assert task.state == TaskState.WAITING
    assert task.checkpoint == {"step": 1}
    assert task.children.count() == 1
    assert worker.tick()
    task.refresh_from_db()
    assert abs(task.progress - Decimal(1) / 3) < Decimal("1e-26")

    # No worker memory survives; the new process's registry and stored snapshots are sufficient.
    worker = Worker(Scheduler(TaskRegistry(BatchTask, ValueTask)))
    assert worker.tick()
    assert task.children.count() == 3
    left = scheduler.claim_next(schedule_id=schedule.id)
    right = scheduler.claim_next(schedule_id=schedule.id)
    assert {left.task.slot.key, right.task.slot.key} == {"left", "right"}
    assert scheduler.claim_next(schedule_id=schedule.id) is None
    assert worker.tick() and worker.tick()
    task.refresh_from_db()
    assert task.progress == 1
    assert task.state == TaskState.WAITING
    assert task.children.get(slot__key="right").completion_reason == "No value requested"
    assert worker.tick()
    task.refresh_from_db()
    assert task.state == TaskState.COMPLETED
    assert task.output == {"first": {"value": 7}, "left": {"value": 8}, "right": {}}
    assert not worker.tick()


def test_pause_stops_claim_but_allows_already_claimed_work_to_finish(scheduler):
    schedule, task = schedule_task(scheduler)
    execution = scheduler.claim_next()
    assert execution.task_id == task.id
    started_at = scheduler.set_enabled(schedule.id, False).started_at
    assert scheduler.claim_next() is None
    worker = Worker(scheduler)
    assert worker.tick()
    assert task.children.count() == 1
    assert not worker.tick()
    assert scheduler.set_enabled(schedule.id, True).started_at == started_at
    assert worker.tick()


def test_wait_and_dependencies_are_atomic_and_scheduler_reads_child_output(scheduler):
    schedule, root = schedule_task(scheduler)
    execution, dispatch = claim_local(scheduler, schedule)
    task = scheduler.task_instance(execution)
    outcome = task.wait_for_children(
        [
            task.child("first", ValueTask, {"value": 1}),
            task.child("left", ValueTask, {"value": 2}, ["first"]),
        ],
        ["left"],
        next_step=1,
    )
    assert scheduler.submit_local(execution.id, dispatch.lease_id, outcome) == "applied"
    assert scheduler.submit_local(execution.id, dispatch.lease_id, outcome) == "already_processed"
    assert Dependency.objects.count() == 1
    first, lease = claim_local(scheduler, schedule)
    assert first.task.slot.key == "first"
    assert scheduler.claim_next() is None
    assert scheduler.submit_local(first.id, lease.lease_id, Completed({"value": 1})) == "applied"
    left, lease = claim_local(scheduler, schedule)
    assert left.task.slot.key == "left"
    instance = scheduler.task_instance(left)
    assert instance.get_parent_task().id == root.id
    assert instance.dependency_outputs == {str(first.task_id): {"value": 1}}
    assert scheduler.submit_local(left.id, lease.lease_id, Completed({"value": 2})) == "applied"
    resumed, lease = claim_local(scheduler, schedule)
    assert resumed.task_id == root.id
    assert resumed.id != execution.id
    assert scheduler.task_instance(resumed).child_output("first") == {"value": 1}
    with pytest.raises(PlanError, match="All child slots"):
        scheduler.submit_local(resumed.id, lease.lease_id, Completed())
    resumed.refresh_from_db()
    assert resumed.state == ExecutionState.ACCEPTED


@pytest.mark.parametrize("violation", ["foreign", "cycle", "unreserved", "undefined_wait"])
def test_rejected_plan_leaves_no_partial_graph_or_checkpoint(scheduler, violation):
    _, unrelated = scheduler.create_schedule(ValueTask, {"value": 9})
    schedule, root = schedule_task(scheduler)
    execution, dispatch = claim_local(scheduler, schedule)
    task = scheduler.task_instance(execution)
    key, dependencies, wait_for = "first", [], ["first"]
    if violation == "foreign":
        dependencies = [unrelated.id]
    if violation == "cycle":
        dependencies = [root.id]  # Implicit deadlock arises only when combined with the wait.
    if violation == "unreserved":
        key = "fourth"
    if violation == "undefined_wait":
        wait_for = ["right"]
    outcome = task.wait_for_children([task.child(key, ValueTask, {"value": 3}, dependencies)], wait_for, 1)
    with pytest.raises(PlanError):
        scheduler.submit_local(execution.id, dispatch.lease_id, outcome)
    root.refresh_from_db()
    assert root.children.count() == 0
    assert not root.waits.exists()
    assert not Dependency.objects.exists()
    assert root.state == TaskState.RUNNING and root.checkpoint == {}


def test_reusing_a_slot_requires_the_same_definition(scheduler):
    schedule, root = schedule_task(scheduler)
    worker = Worker(scheduler)
    assert worker.tick() and worker.tick()
    resumed, lease = claim_local(scheduler, schedule)
    task = scheduler.task_instance(resumed)
    outcome = task.wait_for_children([task.child("first", ValueTask, {"value": 987})], ["first"], 4)
    with pytest.raises(PlanError, match="Conflicting definition"):
        scheduler.submit_local(resumed.id, lease.lease_id, outcome)
    existing = root.children.get()
    same = task.wait_for_children([task.child("first", ValueTask, existing.input)], ["first"], 5)
    assert scheduler.submit_local(resumed.id, lease.lease_id, same) == "applied"
    assert root.children.get().id == existing.id
    assert scheduler.claim_next().snapshot["checkpoint"] == {"step": 5}


def test_unawaited_child_still_cannot_depend_on_its_parents_completion(scheduler):
    schedule, root = schedule_task(scheduler)
    execution, lease = claim_local(scheduler, schedule)
    task = scheduler.task_instance(execution)
    outcome = task.wait_for_children(
        [
            task.child("first", ValueTask, {"value": 1}, [root.id]),
            task.child("left", ValueTask, {"value": 2}),
        ],
        ["left"], 1,
    )
    with pytest.raises(PlanError, match="acyclic"):
        scheduler.submit_local(execution.id, lease.lease_id, outcome)
    assert not root.children.exists()


def test_two_connections_cannot_claim_the_same_task(scheduler):
    schedule, root = schedule_task(scheduler)
    start = Barrier(2, timeout=5)

    def claim():
        start.wait()
        return scheduler.claim_next(schedule_id=schedule.id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run_in_connection(claim), range(2)))
    assert sum(result is not None for result in results) == 1
    assert root.executions.count() == 1
    assert Dispatch.objects.count() == 1


def test_claim_skips_locked_schedules_and_leaves_the_paused_one_waiting(scheduler):
    first, root = schedule_task(scheduler)
    other, other_root = schedule_task(scheduler, ValueTask, {"value": 6})
    locked, release = Event(), Event()

    def pause():
        with transaction.atomic():
            scheduler.set_enabled(first.id, False)
            locked.set()
            assert release.wait(timeout=10)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_in_connection, pause)
        try:
            assert locked.wait(timeout=5)
            assert scheduler.claim_next().task_id == other_root.id
        finally:
            release.set()
        future.result(timeout=5)
    assert scheduler.claim_next(schedule_id=first.id) is None
    root.refresh_from_db()
    assert root.state == TaskState.WAITING
    assert Schedule.objects.get(pk=other.id).enabled


def test_concurrency_limit_applies_only_within_type_and_schedule(scheduler):
    schedule, root = schedule_task(scheduler)
    execution, lease = claim_local(scheduler, schedule)
    task = scheduler.task_instance(execution)
    outcome = task.wait_for_children(
        [
            task.child("first", LimitedValue, {"value": 1}),
            task.child("left", LimitedValue, {"value": 2}),
            task.child("right", ValueTask, {"value": 3}),
        ],
        ["first", "left", "right"], 1,
    )
    scheduler.submit_local(execution.id, lease.lease_id, outcome)
    first = scheduler.claim_next(schedule_id=schedule.id)
    second = scheduler.claim_next(schedule_id=schedule.id)
    assert {first.task.task_type, second.task.task_type} == {LimitedValue.task_type, ValueTask.task_type}
    assert scheduler.claim_next(schedule_id=schedule.id) is None
    another, another_root = schedule_task(scheduler, LimitedValue, {"value": 4})
    assert scheduler.claim_next(schedule_id=another.id).task_id == another_root.id
    worker = Worker(scheduler)
    assert worker.tick()
    assert scheduler.claim_next(schedule_id=schedule.id) is not None


def test_local_outbox_recovery_fences_the_old_lease(scheduler):
    schedule, root = schedule_task(scheduler, ValueTask, {"value": 11})
    execution, first = claim_local(scheduler, schedule)
    assert scheduler.lease_dispatch([DispatchKind.LOCAL], duration=timedelta(seconds=30)) is None
    Dispatch.objects.filter(pk=first.pk).update(lease_until=Now())
    second = scheduler.lease_dispatch([DispatchKind.LOCAL], duration=timedelta(seconds=30))
    assert second.execution_id == execution.id and second.lease_id != first.lease_id
    assert scheduler.submit_local(execution.id, first.lease_id, Completed({"value": 99})) == "stale_lease"
    assert scheduler.submit_local(execution.id, second.lease_id, Completed({"value": 11})) == "applied"
    root.refresh_from_db()
    assert root.output == {"value": 11}


def test_expired_lease_does_not_run_a_second_local_decision_concurrently(scheduler):
    schedule, root = schedule_task(scheduler, ValueTask, {"value": 11})
    execution, first = claim_local(scheduler, schedule)
    Dispatch.objects.filter(pk=first.pk).update(lease_until=Now())
    worker = Worker(scheduler)
    with _decision_lock(execution.id) as acquired:
        assert acquired
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(run_in_connection, worker.tick).result(timeout=5)
    root.refresh_from_db()
    assert root.state == TaskState.RUNNING
    Dispatch.objects.filter(pk=first.pk).update(available_at=Now())
    assert worker.tick()
    root.refresh_from_db()
    assert root.state == TaskState.COMPLETED


def make_external(scheduler, root_name="first"):
    schedule, task = schedule_task(scheduler, WorkflowTask, {"name": root_name})
    assert Worker(scheduler).tick()
    task.refresh_from_db()
    return schedule, task, task.current_execution


def test_delivery_retries_same_execution_until_workflow_accepts(scheduler):
    _, task, execution = make_external(scheduler)
    sent = []

    def send(request):
        sent.append(request)
        if len(sent) == 1:
            raise DeliveryError("response_lost")
        assert scheduler.accept_workflow(request.execution_id).payload == {"name": "first"}
        # The sender never received a response, but accept committed and sent the payload already.
        raise DeliveryError("response_lost")

    worker = Worker(scheduler, dispatch_workflow=send)
    assert worker.tick()
    assert not worker.tick()
    workflow = execution.dispatches.get(kind=DispatchKind.WORKFLOW)
    Dispatch.objects.filter(pk=workflow.pk).update(available_at=Now())
    assert worker.tick()
    assert not worker.tick()
    assert sent[0] == sent[1]
    assert sent[0].idempotency_key == str(execution.id)
    assert scheduler.accept_workflow(execution.id).status == "already_accepted"
    assert scheduler.workflow_is_current(execution.id)
    recorded = []

    def record_outputs(execution, output):
        recorded.append(execution.id)
        return {**output, "asset_ref": "artifact-1"}

    outcome = Completed({"result": "raw"})
    assert scheduler.submit_workflow_result(execution.id, outcome, record_outputs=record_outputs) == "applied"
    assert scheduler.submit_workflow_result(execution.id, outcome, record_outputs=record_outputs) == "already_processed"
    assert recorded == [execution.id]
    task.refresh_from_db()
    assert task.output == {"artifact": "raw", "input": "first", "asset_ref": "artifact-1"}
    assert not scheduler.workflow_is_current(execution.id)


def test_output_registration_rolls_back_with_workflow_completion(scheduler):
    _, task, execution = make_external(scheduler)
    scheduler.accept_workflow(execution.id)

    def register_then_fail(attempt, output):
        Task.objects.filter(pk=attempt.task_id).update(output={"partial": True})
        raise RuntimeError("storage metadata failed")

    outcome = Completed({"result": "ready"})
    with pytest.raises(RuntimeError, match="storage metadata"):
        scheduler.submit_workflow_result(execution.id, outcome, record_outputs=register_then_fail)
    task.refresh_from_db()
    assert task.state == TaskState.RUNNING and task.output is None
    assert scheduler.submit_workflow_result(execution.id, outcome) == "applied"


@pytest.mark.parametrize("claim_retry", [False, True])
def test_late_workflow_success_only_applies_before_next_execution(scheduler, claim_retry):
    schedule, task, execution = make_external(scheduler)
    assert scheduler.accept_workflow(execution.id).status == "accepted"
    expire(scheduler, execution)
    assert not scheduler.workflow_is_current(execution.id)
    assert scheduler.submit_workflow_result(execution.id, Failed("provider_busy")) == "expired"
    task.refresh_from_db()
    assert task.state == TaskState.WAITING and task.retry_at is not None
    assert scheduler.claim_next(schedule_id=schedule.id) is None
    if claim_retry:
        allow_retry(task)
        retry = scheduler.claim_next(schedule_id=schedule.id)
        assert retry.id != execution.id and retry.task_id == task.id
    result = scheduler.submit_workflow_result(execution.id, Completed({"result": "late"}))
    task.refresh_from_db()
    if claim_retry:
        assert result == "expired" and task.current_execution_id == retry.id and task.output is None
    else:
        assert result == "applied" and task.state == TaskState.COMPLETED and task.retry_at is None
        assert scheduler.claim_next(schedule_id=schedule.id) is None


def test_retry_and_late_success_are_serialized(scheduler):
    schedule, task, execution = make_external(scheduler)
    scheduler.accept_workflow(execution.id)
    expire(scheduler, execution)
    allow_retry(task)
    barrier = Barrier(2, timeout=5)

    def run(action):
        barrier.wait()
        return action()

    with ThreadPoolExecutor(max_workers=2) as pool:
        claim = pool.submit(run_in_connection, lambda: run(lambda: scheduler.claim_next(schedule_id=schedule.id)))
        callback = pool.submit(run_in_connection, lambda: run(
            lambda: scheduler.submit_workflow_result(execution.id, Completed({"result": "late"}))))
        claimed, status = claim.result(timeout=10), callback.result(timeout=10)
    task.refresh_from_db()
    if status == "applied":
        assert claimed is None and task.state == TaskState.COMPLETED
    else:
        assert status == "expired" and claimed is not None and task.current_execution_id == claimed.id


def test_automatic_retry_exhaustion_and_manual_retry_preserve_task_state_data(scheduler):
    schedule, task, first = make_external(scheduler)
    scheduler.accept_workflow(first.id)
    assert scheduler.submit_workflow_result(first.id, Failed("provider_busy")) == "applied"
    task.refresh_from_db()
    assert task.retry_at > timezone.now()
    allow_retry(task)
    assert Worker(scheduler).tick()
    task.refresh_from_db()
    second = task.current_execution
    assert second.id != first.id
    assert second.snapshot == first.snapshot
    expire(scheduler, second)
    task.refresh_from_db()
    assert task.state == TaskState.TIMED_OUT and task.attempt_count == 2
    scheduler.retry(schedule.id, task.id)
    task.refresh_from_db()
    assert task.state == TaskState.WAITING and task.attempt_count == 0
    scheduler.claim_next(schedule_id=schedule.id)
    with pytest.raises(StateConflict, match="Only failed or timed-out"):
        scheduler.retry(schedule.id, task.id)


def test_business_failure_blocks_immediately_and_provider_job_blocks_uncertain_retry(scheduler):
    schedule, task, first = make_external(scheduler)
    scheduler.accept_workflow(first.id)
    assert scheduler.submit_workflow_result(first.id, Failed("business_rule")) == "applied"
    task.refresh_from_db()
    assert task.blocked_reason == "business_rule" and task.retry_at is None
    scheduler.retry(schedule.id, task.id)
    assert Worker(scheduler).tick()
    task.refresh_from_db()
    execution = task.current_execution
    scheduler.accept_workflow(execution.id)
    scheduler.register_provider_job(execution.id, "render", "example", "remote-1")
    scheduler.register_provider_job(execution.id, "render", "example", "remote-1")
    with pytest.raises(StateConflict, match="different job"):
        scheduler.register_provider_job(execution.id, "render", "example", "remote-2")
    expire(scheduler, execution)
    task.refresh_from_db()
    assert task.blocked_reason == PROVIDER_CHECK
    assert scheduler.claim_next() is None
    with pytest.raises(StateConflict, match="Verify"):
        scheduler.retry(schedule.id, task.id)
    with pytest.raises(StateConflict, match="no longer match"):
        scheduler.confirm_provider_jobs_inactive(execution.id, jobs={})
    execution.refresh_from_db()
    scheduler.confirm_provider_jobs_inactive(execution.id, jobs=execution.provider_jobs)
    task.refresh_from_db()
    assert task.blocked_reason == "" and task.retry_at is not None


def test_worker_command_uses_registered_task_version(scheduler, settings):
    from django.core.management import call_command

    # Paths are resolved by Django settings, just as the production worker does.
    settings.SCHEDULER_TASK_CLASSES = ("scheduler.examples.ValueTask",)
    old, old_root = schedule_task(scheduler, ValueTask, {"value": 5})
    new, new_root = schedule_task(scheduler, NewValue, {"value": 5})
    call_command("run_scheduler", once=True)
    old_root.refresh_from_db()
    new_root.refresh_from_db()
    assert old_root.output == {"value": 5} and old.definition_version == "1"
    assert new_root.state == TaskState.WAITING and new.definition_version == "2"
    assert Worker(scheduler).tick()
    new_root.refresh_from_db()
    assert new_root.output == {"value": 50}
