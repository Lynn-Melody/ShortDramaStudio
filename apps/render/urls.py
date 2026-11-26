import shutil

from django.http import JsonResponse
from django.urls import path
from django.views.decorators.http import require_GET


@require_GET
def health(request):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


urlpatterns = [path("internal/v1/health", health)]
