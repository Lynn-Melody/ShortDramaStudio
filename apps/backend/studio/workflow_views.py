from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import PolymorphicProxySerializer, extend_schema
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from contracts import workflow_v1 as v1
from contracts.production_v1 import Identifier, validated
from media.uploads import authorize_upload
from media.workflow_outputs import prepare_workflow_outputs
from scheduler.models import Execution
from scheduler.service import PROVIDER_CHECK, StateConflict
from scheduler.tasks import Completed, Failed
from scheduler.worker import configured_scheduler

from .workflow_auth import WorkflowServiceAuthentication, WorkflowVerifierAuthentication


class WorkflowConflict(APIException):
    status_code = 409


ERRORS = {400: OpenApiTypes.OBJECT, 401: v1.WorkflowError, 404: v1.WorkflowError}


class WorkflowView(APIView):
    authentication_classes = [WorkflowServiceAuthentication]
    parser_classes = [JSONParser]
    permission_classes = [IsAuthenticated]

    def execution(self, execution_id, scope):
        if "executionId" in scope and scope["executionId"] != str(execution_id):
            raise ValidationError({"executionId": "Must match the path execution ID."})
        return get_object_or_404(
            Execution.objects.select_related("task"),
            pk=execution_id,
            task_id=scope["taskId"],
            task__schedule_id=scope["projectId"],
            task__definition_version=scope["schemaVersion"],
        )


class AcceptWorkflow(WorkflowView):
    @extend_schema(request=v1.ExecutionIdentity, responses={200: v1.WorkflowAcceptance, **ERRORS})
    def post(self, request, executionId):
        scope = validated(v1.ExecutionIdentity, request.data)
        execution = self.execution(executionId, scope)
        acceptance = configured_scheduler().accept_workflow(execution.id)
        response = {"status": acceptance.status}
        if acceptance.status == "accepted":
            response["input"] = acceptance.payload
        return Response(response)


class CheckWorkflowStatus(WorkflowView):
    @extend_schema(parameters=[v1.ExecutionScope], responses={200: v1.WorkflowStatus, **ERRORS})
    def get(self, request, executionId):
        scope = validated(v1.ExecutionScope, request.query_params)
        execution = self.execution(executionId, scope)
        return Response({"active": configured_scheduler().workflow_is_current(execution.id)})


class AuthorizeWorkflowUpload(WorkflowView):
    @extend_schema(
        request=v1.WorkflowUploadRequest,
        responses={200: v1.WorkflowUploadGrant, 409: v1.WorkflowError, 503: v1.WorkflowError, **ERRORS},
    )
    def post(self, request, executionId):
        data = validated(v1.WorkflowUploadRequest, request.data)
        execution = self.execution(executionId, data)
        return Response(authorize_upload(
            execution, slot=data["slot"], size_bytes=data["sizeBytes"], content_type=data["contentType"],
        ))


class SubmitWorkflowResult(WorkflowView):
    @extend_schema(
        request=PolymorphicProxySerializer(
            component_name="WorkflowResult",
            serializers={"completed": v1.WorkflowCompleted, "failed": v1.WorkflowFailed},
            resource_type_field_name="outcome",
            many=False,
        ),
        responses={200: v1.WorkflowResultStatus, 409: v1.WorkflowError, 503: v1.WorkflowError, **ERRORS},
    )
    def post(self, request, executionId):
        if not isinstance(request.data, dict):
            raise ValidationError({"non_field_errors": "Expected a JSON object."})
        match request.data.get("outcome"):
            case "completed":
                data = validated(v1.WorkflowCompleted, request.data)
                outcome = Completed({"data": data["data"], "outputs": data["outputs"]})
            case "failed":
                data = validated(v1.WorkflowFailed, request.data)
                if data["errorCode"] == PROVIDER_CHECK:
                    raise ValidationError({"errorCode": "This code is reserved for the scheduler."})
                outcome = Failed(data["errorCode"], data["message"])
            case _:
                raise ValidationError({"outcome": "Expected completed or failed."})
        execution = self.execution(executionId, data)
        status = configured_scheduler().submit_workflow_result(
            execution.id, outcome, prepare_outputs=prepare_workflow_outputs,
        )
        return Response({"status": status})


class RegisterProviderJob(WorkflowView):
    @extend_schema(request=v1.ProviderJobRegistration, responses={204: None, 409: v1.WorkflowError, **ERRORS})
    def put(self, request, executionId, stepKey):
        data = validated(v1.ProviderJobRegistration, request.data)
        execution = self.execution(executionId, data)
        # The URL converter limits characters and length to the wire identifier grammar.
        try:
            configured_scheduler().register_provider_job(execution.id, stepKey, data["provider"], data["jobId"])
        except StateConflict as exc:
            raise WorkflowConflict(str(exc)) from exc
        return Response(status=204)


class ProviderCheck(WorkflowView):
    authentication_classes = [WorkflowVerifierAuthentication]

    @extend_schema(parameters=[v1.ExecutionScope], responses={200: v1.ProviderCheckSnapshot, **ERRORS})
    def get(self, request, executionId):
        scope = validated(v1.ExecutionScope, request.query_params)
        execution = self.execution(executionId, scope)
        return Response({
            "pending": (
                execution.task.current_execution_id == execution.id and execution.task.blocked_reason == PROVIDER_CHECK
            ),
            "jobs": [
                {"stepKey": key, "provider": job["provider"], "jobId": job["job_id"]}
                for key, job in sorted(execution.provider_jobs.items())
            ],
        })

    @extend_schema(request=v1.ProviderCheckConfirmation, responses={204: None, 409: v1.WorkflowError, **ERRORS})
    def post(self, request, executionId):
        data = validated(v1.ProviderCheckConfirmation, request.data)
        execution = self.execution(executionId, data)
        jobs = {job["stepKey"]: {"provider": job["provider"], "job_id": job["jobId"]} for job in data["jobs"]}
        try:
            configured_scheduler().confirm_provider_jobs_inactive(execution.id, jobs=jobs)
        except StateConflict as exc:
            raise WorkflowConflict(str(exc)) from exc
        return Response(status=204)
