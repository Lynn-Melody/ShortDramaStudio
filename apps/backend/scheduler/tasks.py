from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID


class PlanError(ValueError):
    """A task definition or outcome violates the scheduler protocol."""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_delay: timedelta = timedelta(seconds=5)
    max_delay: timedelta = timedelta(minutes=5)
    retryable_error_codes: frozenset[str] = frozenset()

    def __post_init__(self):
        if not 1 <= self.max_attempts <= 31 or not timedelta(0) < self.initial_delay <= self.max_delay:
            raise PlanError("Invalid retry policy")

    def delay(self, attempt: int) -> timedelta:
        return min(self.initial_delay * (2 ** min(30, max(0, attempt - 1))), self.max_delay)


@dataclass(frozen=True)
class TaskRef:
    id: UUID
    task_type: str
    definition_version: str
    input: dict


@dataclass(frozen=True)
class Child:
    key: str
    task_class: type["BaseTask"]
    input: dict
    depends_on: tuple[str | UUID, ...] = ()


@dataclass(frozen=True)
class WaitForChildren:
    children: tuple[Child, ...]
    wait_for: tuple[str, ...]
    checkpoint: dict


@dataclass(frozen=True)
class Completed:
    output: dict = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class Failed:
    error_code: str
    message: str = ""


@dataclass(frozen=True)
class InvokeWorkflow:
    name: str
    input: dict


Outcome = WaitForChildren | Completed | Failed | InvokeWorkflow


class BaseTask(ABC):
    task_type: str
    definition_version = "1"
    timeout = timedelta(minutes=5)
    max_running_per_project: int | None = None
    retry_policy = RetryPolicy()
    # Optional database-only handler called inside workflow completion's transaction.
    workflow_completion_handler = None

    def __init__(self, input, *, checkpoint=None, parent=None, child_outputs=None, dependency_outputs=None):
        self.input = deepcopy(input)
        self.checkpoint = deepcopy(checkpoint) if checkpoint is not None else {}
        self._parent = deepcopy(parent)
        self._child_outputs = deepcopy(child_outputs) if child_outputs is not None else {}
        self.dependency_outputs = deepcopy(dependency_outputs) if dependency_outputs is not None else {}
        self.child_total = 0
        self.child_keys = ()

    @property
    def cur_step(self):
        return self.checkpoint.get("step", 0)

    @cur_step.setter
    def cur_step(self, value):
        self.checkpoint["step"] = value

    def on_init(self):
        """Declare fixed child slots and the initial checkpoint from input."""

    @abstractmethod
    def on_processing(self) -> Outcome:
        """Make a decision without writing storage or calling external services."""

    def on_workflow_result(self, result: Completed | Failed) -> Completed | Failed:
        return result

    def get_parent_task(self) -> TaskRef | None:
        return self._parent

    def child_output(self, key):
        try:
            return self._child_outputs[key]
        except KeyError as exc:
            raise PlanError(f"No completed child in slot {key!r}") from exc

    def child(self, key, task_class, input, depends_on=()):
        return Child(key, task_class, input, tuple(depends_on))

    def wait_for_children(self, children, wait_for, next_step):
        return WaitForChildren(tuple(children), tuple(wait_for), {**self.checkpoint, "step": next_step})

    def finish(self, output=None, *, reason=""):
        return Completed({} if output is None else output, reason)

    def fail(self, error_code, message=""):
        return Failed(error_code, message)

    def invoke_workflow(self, name, input):
        return InvokeWorkflow(name, input)


class TaskRegistry:
    def __init__(self, *task_classes):
        self._classes = {}
        for task_class in task_classes:
            self.register(task_class)

    def register(self, task_class):
        if not isinstance(task_class, type) or not issubclass(task_class, BaseTask):
            raise PlanError("Registered task classes must inherit BaseTask")
        key = (getattr(task_class, "task_type", None), task_class.definition_version)
        if any(not isinstance(value, str) or not value or len(value) > limit for value, limit in zip(key, (100, 64))):
            raise PlanError("A task type and definition version are required")
        if key in self._classes and self._classes[key] is not task_class:
            raise PlanError(f"Duplicate task definition: {key}")
        if not isinstance(task_class.timeout, timedelta) or task_class.timeout <= timedelta(0):
            raise PlanError("Task timeout must be positive")
        limit = task_class.max_running_per_project
        if limit is not None and (type(limit) is not int or limit < 1):
            raise PlanError("Task concurrency limit must be positive")
        if not isinstance(task_class.retry_policy, RetryPolicy):
            raise PlanError("A RetryPolicy is required")
        self._classes[key] = task_class
        return task_class

    def resolve(self, task_type, definition_version):
        try:
            return self._classes[task_type, definition_version]
        except KeyError as exc:
            raise PlanError(f"Unknown task definition: {task_type}@{definition_version}") from exc

    def __iter__(self):
        return iter(self._classes.values())
