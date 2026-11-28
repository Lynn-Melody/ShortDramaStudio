from django.contrib import admin, messages

from studio.admin_tools import ReadOnlyAdmin, ReadOnlyInline

from .models import Execution, Task
from .service import StateConflict
from .worker import configured_scheduler


class ExecutionInline(ReadOnlyInline):
    model = Execution
    fields = ("id", "state", "started_at", "deadline_at", "ended_at", "workflow_accepted_at", "result")
    ordering = ("-started_at",)


@admin.register(Task)
class TaskAdmin(ReadOnlyAdmin):
    list_display = ("id", "task_type", "schedule_id", "state", "blocked_reason", "progress", "attempt_count")
    list_filter = ("state", "task_type")
    search_fields = ("=id", "=schedule__id", "=parent__id")
    inlines = (ExecutionInline,)
    actions = ("retry_tasks",)

    def has_control_permission(self, request):
        return request.user.has_perm("scheduler.change_task")

    @admin.action(description="Retry failed-blocked or timed-out tasks", permissions=["control"])
    def retry_tasks(self, request, queryset):
        scheduler = configured_scheduler()
        retried = 0
        for task in queryset.only("pk", "schedule_id"):
            try:
                scheduler.retry(task.schedule_id, task.pk)
            except StateConflict as exc:
                self.message_user(request, f"{task.pk}: {exc}", messages.WARNING)
            else:
                retried += 1
        if retried:
            self.message_user(request, f"Retried {retried} task(s).", messages.SUCCESS)


@admin.register(Execution)
class ExecutionAdmin(ReadOnlyAdmin):
    list_display = ("id", "task", "state", "started_at", "deadline_at", "ended_at")
    list_filter = ("state",)
    list_select_related = ("task",)
    search_fields = ("=id", "=task__id")
