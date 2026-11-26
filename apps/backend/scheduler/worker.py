import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from django.conf import settings
from django.db import DatabaseError
from django.utils.module_loading import import_string

from .locks import decision_lock
from .models import DispatchKind
from .service import Scheduler
from .tasks import Failed, PlanError, TaskRegistry


logger = logging.getLogger(__name__)


class DeliveryError(Exception):
    def __init__(self, code, *, retryable=True):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True)
class WorkflowRequest:
    name: str
    schedule_id: UUID
    task_id: UUID
    task_type: str
    definition_version: str
    execution_id: UUID
    deadline_at: datetime

    @property
    def idempotency_key(self):
        return str(self.execution_id)


def configured_scheduler():
    task_classes = getattr(settings, "SCHEDULER_TASK_CLASSES", ())
    return Scheduler(TaskRegistry(*(import_string(path) for path in task_classes)))


class Worker:
    def __init__(self, scheduler, *, dispatch_workflow=None, lease_duration=timedelta(seconds=30)):
        if lease_duration <= timedelta(0):
            raise ValueError("Lease duration must be positive")
        self.scheduler = scheduler
        self.dispatch_workflow = dispatch_workflow
        self.lease_duration = lease_duration

    def tick(self):
        expired = self.scheduler.reap_one()
        kinds = [DispatchKind.LOCAL]
        if self.dispatch_workflow is not None:
            kinds.append(DispatchKind.WORKFLOW)
        dispatch = self.scheduler.lease_dispatch(kinds, duration=self.lease_duration)
        claimed = None
        if dispatch is None:
            claimed = self.scheduler.claim_next()
            if claimed is not None:
                dispatch = self.scheduler.lease_dispatch([DispatchKind.LOCAL], duration=self.lease_duration)
        if dispatch is None:
            return expired is not None or claimed is not None
        try:
            if dispatch.kind == DispatchKind.LOCAL:
                self._run_local(dispatch)
            else:
                self._send_workflow(dispatch)
        except Exception:
            # Leave the lease to expire if the dispatcher could not persist its decision.
            logger.exception("Could not finish dispatch", extra=self._log_context(dispatch))
        return True

    @staticmethod
    def _log_context(dispatch):
        return {
            "project_id": dispatch.execution.task.schedule_id,
            "task_id": dispatch.execution.task_id,
            "execution_id": dispatch.execution_id,
        }

    def _run_local(self, dispatch):
        execution = dispatch.execution
        with decision_lock(execution.id) as acquired:
            if not acquired:
                self.scheduler.defer_local(dispatch)
                return
            if not self.scheduler.local_lease_is_current(dispatch):
                return
            try:
                instance = self.scheduler.task_instance(execution)
                outcome = instance.on_processing()
            except DatabaseError:
                raise
            except Exception:
                logger.exception("Task handler failed", extra=self._log_context(dispatch))
                outcome = Failed("handler_failed", "Task handler raised an exception")
            try:
                self.scheduler.submit_local(execution.id, dispatch.lease_id, outcome)
            except PlanError:
                logger.exception("Task decision rejected", extra=self._log_context(dispatch))
                self.scheduler.submit_local(
                    execution.id, dispatch.lease_id, Failed("invalid_plan", "Task decision violated the scheduler contract"),
                )

    def _send_workflow(self, dispatch):
        execution = dispatch.execution
        task = execution.task
        request = WorkflowRequest(
            dispatch.payload["name"], task.schedule_id, task.id, task.task_type, task.definition_version,
            execution.id, execution.deadline_at,
        )
        try:
            self.dispatch_workflow(request)
        except DeliveryError as exc:
            self.scheduler.finish_delivery(dispatch, error_code=exc.code, retryable=exc.retryable)
        except Exception:
            logger.exception("Workflow delivery failed", extra=self._log_context(dispatch))
            self.scheduler.finish_delivery(dispatch, error_code="delivery_failed")
        else:
            self.scheduler.finish_delivery(dispatch)
