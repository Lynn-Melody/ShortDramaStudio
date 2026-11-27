from django.urls import path, register_converter

from .health import readiness
from .workflow_views import AcceptWorkflow, CheckWorkflowStatus, ProviderCheck, RegisterProviderJob, SubmitWorkflowResult


class StepKeyConverter:
    regex = r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,99}"

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value


register_converter(StepKeyConverter, "step_key")

execution = "internal/v1/executions/<uuid:executionId>"
urlpatterns = [
    path("internal/v1/health", readiness, name="readiness"),
    path(f"{execution}/accept", AcceptWorkflow.as_view(), name="workflow-accept"),
    path(f"{execution}/status", CheckWorkflowStatus.as_view(), name="workflow-status"),
    path(f"{execution}/result", SubmitWorkflowResult.as_view(), name="workflow-result"),
    path(f"{execution}/provider-jobs/<step_key:stepKey>", RegisterProviderJob.as_view(), name="workflow-provider-job"),
    path(f"{execution}/provider-check", ProviderCheck.as_view(), name="workflow-provider-check"),
]
