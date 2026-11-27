from uuid import UUID, uuid4

from django.db import transaction

from contracts import production_v1 as v1
from scheduler.worker import configured_scheduler

from .models import Project
from .tasks import ShortDrama


@transaction.atomic
def create_project(idea, *, project_id=None, definition_version=ShortDrama.definition_version, scheduler=None):
    """Create a paused project and its root, or return the original rows on replay."""
    inputs = v1.validate_leaf_input("series_plan", {"idea": idea})
    project_id = uuid4() if project_id is None else UUID(str(project_id))
    scheduler = scheduler if scheduler is not None else configured_scheduler()
    root_class = scheduler.registry.resolve(ShortDrama.task_type, definition_version)
    schedule, root = scheduler.create_schedule(root_class, inputs, schedule_id=project_id)
    project, _ = Project.objects.get_or_create(schedule=schedule, defaults={"idea": inputs["idea"]})
    return project, root
