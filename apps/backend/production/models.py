from django.db import models
from django.db.models import Q


class Project(models.Model):
    schedule = models.OneToOneField("scheduler.Schedule", on_delete=models.CASCADE, primary_key=True)
    idea = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    title = models.CharField(max_length=200, blank=True)
    plan = models.JSONField(null=True)


class Episode(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="episodes")
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    outline = models.TextField()
    script = models.TextField(blank=True)
    deliveries = models.JSONField(default=dict)

    class Meta:
        ordering = ("number",)
        constraints = [
            models.UniqueConstraint(fields=("project", "number"), name="production_unique_episode_number"),
            models.CheckConstraint(condition=Q(number__gt=0), name="production_positive_episode_number"),
        ]


class Shot(models.Model):
    episode = models.ForeignKey(Episode, on_delete=models.CASCADE, related_name="shots")
    number = models.PositiveIntegerField()
    spec = models.JSONField()
    image = models.JSONField(null=True)
    dialogue = models.JSONField(default=list)
    video = models.JSONField(null=True)
    sound = models.JSONField(default=list)

    class Meta:
        ordering = ("number",)
        constraints = [
            models.UniqueConstraint(fields=("episode", "number"), name="production_unique_shot_number"),
            models.CheckConstraint(condition=Q(number__gt=0), name="production_positive_shot_number"),
        ]
