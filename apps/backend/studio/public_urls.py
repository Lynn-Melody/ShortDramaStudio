from django.urls import path

from . import workbench_views as views
from .health import health

project = "api/v1/projects/<uuid:projectId>"
urlpatterns = [
    path("api/v1/health", health, name="health"),
    path("api/v1/session", views.SessionView.as_view(), name="session"),
    path("api/v1/projects", views.ProjectsView.as_view(), name="projects"),
    path(project, views.ProjectView.as_view(), name="project"),
    path(f"{project}/pause", views.PauseProject.as_view(), name="project-pause"),
    path(f"{project}/resume", views.ResumeProject.as_view(), name="project-resume"),
    path(f"{project}/episodes", views.EpisodesView.as_view(), name="episodes"),
    path(f"{project}/episodes/<int:episodeId>", views.EpisodeView.as_view(), name="episode"),
    path(f"{project}/episodes/<int:episodeId>/shots", views.ShotsView.as_view(), name="shots"),
    path(f"{project}/task-tree", views.TaskTreeView.as_view(), name="task-tree"),
    path(f"{project}/tasks", views.TasksView.as_view(), name="tasks"),
    path(f"{project}/tasks/<uuid:taskId>", views.TaskView.as_view(), name="task"),
    path(f"{project}/tasks/<uuid:taskId>/retry", views.RetryTask.as_view(), name="task-retry"),
    path(f"{project}/tasks/<uuid:taskId>/outputs", views.TaskOutputsView.as_view(), name="task-outputs"),
    path(f"{project}/assets", views.AssetsView.as_view(), name="assets"),
    path(f"{project}/assets/<uuid:assetId>/versions", views.AssetVersionsView.as_view(), name="asset-versions"),
    path(f"{project}/asset-versions/<uuid:versionId>/preview", views.AssetPreviewView.as_view(), name="asset-preview"),
    path(f"{project}/deliveries", views.DeliveriesView.as_view(), name="deliveries"),
]
