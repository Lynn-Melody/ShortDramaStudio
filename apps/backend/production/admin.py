from django import forms
from django.contrib import admin, messages
from rest_framework.exceptions import ValidationError

from contracts import production_v1 as v1
from contracts.public_v1 import IDEA_MAX_LENGTH
from studio.admin_tools import ReadOnlyAdmin, ReadOnlyInline

from . import workbench
from .models import Episode, Project, Shot


class ProjectCreationForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ("idea",)

    def clean_idea(self):
        idea = self.cleaned_data["idea"]
        if len(idea.strip()) > IDEA_MAX_LENGTH:
            raise forms.ValidationError(f"Ideas are limited to {IDEA_MAX_LENGTH} characters.")
        try:
            return v1.validate_leaf_input("series_plan", {"idea": idea})["idea"]
        except ValidationError as exc:
            raise forms.ValidationError("Enter a nonblank idea.") from exc


class EpisodeInline(ReadOnlyInline):
    model = Episode
    fields = ("number", "title", "outline")


class ShotInline(ReadOnlyInline):
    model = Shot
    fields = ("number", "spec")


@admin.register(Project)
class ProjectAdmin(ReadOnlyAdmin):
    list_display = ("project_name", "status", "progress_percent", "running_count", "timed_out_count", "created_at")
    readonly_fields = ("schedule", "idea", "title", "plan", "created_at")
    search_fields = ("=schedule__id", "title", "idea")
    ordering = ("-created_at",)
    inlines = (EpisodeInline,)
    actions = ("resume_projects", "pause_projects")

    def get_queryset(self, request):
        return workbench.projects()

    def has_add_permission(self, request, obj=None):
        return request.user.has_perm("production.add_project")

    def has_control_permission(self, request):
        return request.user.has_perm("production.change_project")

    def get_form(self, request, obj=None, change=False, **kwargs):
        if obj is None:
            kwargs["form"] = ProjectCreationForm
            kwargs["fields"] = ("idea",)
        return super().get_form(request, obj, change=change, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        return () if obj is None else self.readonly_fields

    def get_inlines(self, request, obj):
        return () if obj is None else self.inlines

    def save_model(self, request, obj, form, change):
        # Creation goes through the production service so the paused schedule and root task exist.
        project, _ = workbench.create(form.cleaned_data["idea"])
        obj.schedule_id, obj.created_at, obj.idea = project.pk, project.created_at, project.idea
        obj._state.adding = False

    @admin.display(description="name")
    def project_name(self, project):
        return workbench.project_name(project)

    @admin.display(description="status")
    def status(self, project):
        return workbench.project_status(project)

    @admin.display(description="progress")
    def progress_percent(self, project):
        return f"{float(project.root_progress or 0):.1%}"

    @admin.display(description="running")
    def running_count(self, project):
        return project.running_count

    @admin.display(description="timed out")
    def timed_out_count(self, project):
        return project.timed_out_count

    def _switch(self, request, queryset, running):
        for project in queryset:
            try:
                workbench.set_running(project, running)
            except workbench.ActionConflict as exc:
                self.message_user(request, f"{workbench.project_name(project)}: {exc}", messages.WARNING)

    @admin.action(description="Start or resume production", permissions=["control"])
    def resume_projects(self, request, queryset):
        self._switch(request, queryset, True)

    @admin.action(description="Pause new task claims", permissions=["control"])
    def pause_projects(self, request, queryset):
        self._switch(request, queryset, False)


@admin.register(Episode)
class EpisodeAdmin(ReadOnlyAdmin):
    list_display = ("project", "number", "title")
    list_select_related = ("project",)
    search_fields = ("=project__schedule__id", "title")
    inlines = (ShotInline,)


@admin.register(Shot)
class ShotAdmin(ReadOnlyAdmin):
    list_display = ("episode", "number")
    list_select_related = ("episode",)
