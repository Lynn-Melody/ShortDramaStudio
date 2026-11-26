from django.urls import path

from .health import readiness

urlpatterns = [path("internal/v1/health", readiness, name="readiness")]
