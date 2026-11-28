"""Public workbench API. Only Django sessions authenticate here; service tokens are not accepted."""

from contextlib import contextmanager
from functools import wraps

from django.contrib.auth import authenticate, login, logout
from django.db import connection, transaction
from django.http import Http404
from django.middleware.csrf import get_token
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from contracts import public_v1 as v1
from contracts.production_v1 import validated
from media import library
from media.models import AssetItem, AssetVersion
from production import workbench
from production.models import Episode, Project
from scheduler.models import Task


class Conflict(APIException):
    status_code = 409

    def __init__(self, code, message):
        super().__init__()
        self.detail = {"detail": message, "code": code}


SCOPED_ROWS = (Project.DoesNotExist, Episode.DoesNotExist, Task.DoesNotExist, AssetItem.DoesNotExist,
               AssetVersion.DoesNotExist)
ERRORS = {403: v1.Error, 404: v1.Error}
NO_STORE = {"Cache-Control": "no-store"}
CSRF = [OpenApiParameter(
    "X-CSRFToken", OpenApiTypes.STR, OpenApiParameter.HEADER, required=True,
    description="csrfToken from GET /api/v1/session.",
)]


@contextmanager
def consistent_read():
    """Serve every query of one response from a single PostgreSQL snapshot."""
    nested = connection.in_atomic_block
    with transaction.atomic():
        if not nested:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        yield


def scoped(method):
    """Map rows outside the requested project to 404 and domain conflicts to 409."""
    @wraps(method)
    def wrapper(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except SCOPED_ROWS as exc:
            raise Http404() from exc
        except workbench.ActionConflict as exc:
            raise Conflict(exc.code, str(exc)) from exc
    return wrapper


def read(method):
    @wraps(method)
    def wrapper(*args, **kwargs):
        with consistent_read():
            return method(*args, **kwargs)
    return scoped(wrapper)


class WorkbenchView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    @staticmethod
    def project(project_id):
        return workbench.get_project(project_id)


def _query(serializer_class, request):
    return validated(serializer_class, request.query_params)


class SessionView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]
    parser_classes = [JSONParser]

    @staticmethod
    def session(request, user=None):
        user = user or request.user
        authenticated = bool(user and user.is_authenticated)
        return Response(v1.output(v1.Session, {
            "authenticated": authenticated,
            "username": user.get_username() if authenticated else None,
            "csrfToken": get_token(request),
        }), headers=NO_STORE)

    @extend_schema(operation_id="session_read", responses={200: v1.Session}, summary="Read the browser session and issue a CSRF token")
    def get(self, request):
        return self.session(request)

    @extend_schema(operation_id="session_login", parameters=CSRF, request=v1.SessionLogin, responses={200: v1.Session, 400: v1.FieldErrors, 403: v1.Error},
                   summary="Sign in with a Django account")
    def post(self, request):
        SessionAuthentication().enforce_csrf(request)
        data = validated(v1.SessionLogin, request.data)
        user = authenticate(request._request, username=data["username"], password=data["password"])
        if user is None:
            raise ValidationError({"non_field_errors": ["Invalid username or password."]})
        login(request._request, user)
        return self.session(request, user)

    @extend_schema(operation_id="session_logout", parameters=CSRF, responses={204: None, 403: v1.Error}, summary="Sign out")
    def delete(self, request):
        SessionAuthentication().enforce_csrf(request)
        logout(request._request)
        return Response(status=204)


class ProjectsView(WorkbenchView):
    @extend_schema(operation_id="projects_list", parameters=[v1.ListQuery], responses={200: v1.ProjectList, 400: v1.FieldErrors, **ERRORS},
                   summary="List projects, newest first")
    @read
    def get(self, request):
        query = _query(v1.ListQuery, request)
        return Response(v1.output(v1.ProjectList, workbench.project_list(
            cursor=query.get("cursor"), limit=query["limit"],
        )))

    @extend_schema(operation_id="projects_create", parameters=CSRF, request=v1.ProjectCreate, responses={201: v1.ProjectDetail, 200: v1.ProjectDetail,
                   400: v1.FieldErrors, 409: v1.Error, **ERRORS},
                   summary="Create a paused project and its root task, or replay a creation")
    @scoped
    def post(self, request):
        data = validated(v1.ProjectCreate, request.data)
        project, created = workbench.create(data["idea"], data.get("projectId"))
        return Response(v1.output(v1.ProjectDetail, workbench.project_detail(project)), status=201 if created else 200)


class ProjectView(WorkbenchView):
    @extend_schema(operation_id="project_read", responses={200: v1.ProjectDetail, **ERRORS})
    @read
    def get(self, request, projectId):
        return Response(v1.output(v1.ProjectDetail, workbench.project_detail(self.project(projectId))))


def _switch(project_id, running):
    summary = workbench.set_running(workbench.get_project(project_id), running)
    return Response(v1.output(v1.ProjectSummary, summary))


SWITCH_RESPONSES = {200: v1.ProjectSummary, 409: v1.Error, **ERRORS}


class PauseProject(WorkbenchView):
    @extend_schema(operation_id="project_pause", parameters=CSRF, request=None, responses=SWITCH_RESPONSES,
                   summary="Stop new claims. Claimed executions may still finish.")
    @scoped
    def post(self, request, projectId):
        return _switch(projectId, False)


class ResumeProject(WorkbenchView):
    @extend_schema(operation_id="project_resume", parameters=CSRF, request=None, responses=SWITCH_RESPONSES,
                   summary="Allow claims again. The first resume starts production.")
    @scoped
    def post(self, request, projectId):
        return _switch(projectId, True)


class EpisodesView(WorkbenchView):
    @extend_schema(operation_id="episodes_list", responses={200: v1.EpisodeList, **ERRORS})
    @read
    def get(self, request, projectId):
        return Response(v1.output(v1.EpisodeList, workbench.episode_list(self.project(projectId))))


class EpisodeView(WorkbenchView):
    @extend_schema(operation_id="episode_read", responses={200: v1.EpisodeDetail, **ERRORS})
    @read
    def get(self, request, projectId, episodeId):
        project = self.project(projectId)
        episode = workbench.get_episode(project, episodeId)
        return Response(v1.output(v1.EpisodeDetail, workbench.episode_detail(project, episode)))


class ShotsView(WorkbenchView):
    @extend_schema(operation_id="shots_list", responses={200: v1.ShotList, **ERRORS})
    @read
    def get(self, request, projectId, episodeId):
        episode = workbench.get_episode(self.project(projectId), episodeId)
        return Response(v1.output(v1.ShotList, workbench.shot_list(episode)))


class TaskTreeView(WorkbenchView):
    @extend_schema(operation_id="task_tree_read", parameters=[v1.TaskTreeQuery], responses={200: v1.TaskTree, 400: v1.FieldErrors, **ERRORS},
                   summary="Running paths with shared ancestors, or a subtree two levels deep")
    @read
    def get(self, request, projectId):
        query = _query(v1.TaskTreeQuery, request)
        project = self.project(projectId)
        return Response(v1.output(v1.TaskTree, workbench.task_tree(project, query["view"], query.get("rootTaskId"))))


class TasksView(WorkbenchView):
    @extend_schema(operation_id="tasks_list", parameters=[v1.TaskListQuery], responses={200: v1.TaskList, 400: v1.FieldErrors, **ERRORS},
                   summary="Locate tasks by state, with their ancestor paths")
    @read
    def get(self, request, projectId):
        query = _query(v1.TaskListQuery, request)
        return Response(v1.output(v1.TaskList, workbench.task_list(
            self.project(projectId), state=query.get("state"), blocked=query.get("blocked"),
            task_type=query.get("taskType"), cursor=query.get("cursor"), limit=query["limit"],
        )))


def _task_detail(project, task_id):
    return v1.output(v1.TaskDetail, workbench.task_detail(
        project, task_id, output_count=lambda pk: library.output_count(project, pk),
    ))


class TaskView(WorkbenchView):
    @extend_schema(operation_id="task_read", responses={200: v1.TaskDetail, **ERRORS})
    @read
    def get(self, request, projectId, taskId):
        return Response(_task_detail(self.project(projectId), taskId))


class RetryTask(WorkbenchView):
    @extend_schema(operation_id="task_retry", parameters=CSRF, request=None, responses={200: v1.TaskDetail, 409: v1.Error, **ERRORS},
                   summary="Retry a failed-blocked or timed-out task")
    @scoped
    def post(self, request, projectId, taskId):
        project = self.project(projectId)
        detail = workbench.retry(project, taskId, output_count=lambda pk: library.output_count(project, pk))
        return Response(v1.output(v1.TaskDetail, detail))


class TaskOutputsView(WorkbenchView):
    @extend_schema(operation_id="task_outputs_list", responses={200: v1.TaskOutputs, **ERRORS}, summary="Exact versions produced by one task")
    @read
    def get(self, request, projectId, taskId):
        return Response(v1.output(v1.TaskOutputs, library.task_outputs(self.project(projectId), taskId)))


class AssetsView(WorkbenchView):
    @extend_schema(operation_id="assets_list", parameters=[v1.AssetQuery], responses={200: v1.AssetList, 400: v1.FieldErrors, **ERRORS},
                   summary="Asset cards in one scope, grouped by asset with the latest version")
    @read
    def get(self, request, projectId):
        query = _query(v1.AssetQuery, request)
        project = self.project(projectId)
        return Response(v1.output(v1.AssetList, library.asset_list(
            project, workbench.current_episode(project), scope=query["scope"], episode_id=query.get("episodeId"),
            shot_id=query.get("shotId"), kind=query.get("kind"), q=query.get("q"),
            cursor=query.get("cursor"), limit=query["limit"],
        )))


class AssetVersionsView(WorkbenchView):
    @extend_schema(operation_id="asset_versions_list", responses={200: v1.AssetVersionList, **ERRORS})
    @read
    def get(self, request, projectId, assetId):
        project = self.project(projectId)
        return Response(v1.output(v1.AssetVersionList, library.asset_versions(project, library.get_asset(project, assetId))))


class DeliveriesView(WorkbenchView):
    @extend_schema(operation_id="deliveries_list", parameters=[v1.DeliveryQuery], responses={200: v1.DeliveryList, 400: v1.FieldErrors, **ERRORS},
                   summary="Episode deliveries with the exact video, subtitles and timeline versions")
    @read
    def get(self, request, projectId):
        query = _query(v1.DeliveryQuery, request)
        return Response(v1.output(v1.DeliveryList, library.delivery_list(
            self.project(projectId), episode_id=query.get("episodeId"),
        )))


class AssetPreviewView(WorkbenchView):
    @extend_schema(operation_id="asset_version_preview", responses={200: v1.AssetPreview, 503: v1.Error, **ERRORS},
                   summary="Sign a short-lived read URL for one private version")
    @read
    def get(self, request, projectId, versionId):
        preview = library.preview(self.project(projectId), versionId)
        return Response(v1.output(v1.AssetPreview, preview), headers=NO_STORE)
