from rest_framework import serializers as s
from rest_framework.exceptions import ValidationError

from .production_v1 import VERSION, Identifier, Object, Success, Text, UUID, ordered_unique


class ExecutionScope(Object):
    projectId = UUID()
    taskId = UUID()
    schemaVersion = s.ChoiceField(choices=[VERSION])

    def validate_schemaVersion(self, value):
        if not isinstance(self.initial_data["schemaVersion"], str):
            raise ValidationError("Expected a version string.")
        return value


class ExecutionIdentity(ExecutionScope):
    executionId = UUID()


class WorkflowAcceptance(Object):
    status = s.ChoiceField(choices=["accepted", "already_accepted", "already_processed", "expired", "not_ready"])
    input = s.DictField(required=False, help_text="Fixed input. Present only on first acceptance.")


class WorkflowStatus(Object):
    active = s.BooleanField(help_text="True while the workflow is accepted, current and before its deadline.")


class WorkflowCompleted(ExecutionIdentity, Success):
    outcome = s.ChoiceField(choices=["completed"])


class WorkflowFailed(ExecutionIdentity):
    outcome = s.ChoiceField(choices=["failed"])
    errorCode = Identifier()
    message = Text(max_length=4000, allow_blank=True)


class WorkflowResultStatus(Object):
    status = s.ChoiceField(choices=["applied", "already_processed", "expired", "not_accepted"])


class ProviderJob(Object):
    provider = Identifier()
    jobId = Text(max_length=300, trim_whitespace=False)


class ProviderJobRegistration(ExecutionIdentity, ProviderJob):
    pass


class ProviderJobSnapshot(ProviderJob):
    stepKey = Identifier()


class ProviderCheckSnapshot(Object):
    pending = s.BooleanField()
    jobs = ProviderJobSnapshot(many=True)


class ProviderCheckConfirmation(ExecutionIdentity):
    workflowStopped = s.BooleanField()
    requestsSettled = s.BooleanField()
    jobsInactive = s.BooleanField()
    jobs = ProviderJobSnapshot(many=True)

    def validate_jobs(self, jobs):
        return ordered_unique(jobs, "stepKey")

    def validate(self, attrs):
        for key in ("workflowStopped", "requestsSettled", "jobsInactive"):
            if self.initial_data.get(key) is not True:
                raise ValidationError({key: "Confirmation requires affirmative evidence."})
        return attrs


class WorkflowError(Object):
    detail = s.CharField()
