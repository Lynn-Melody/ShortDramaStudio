from django.http import JsonResponse
from django.urls import path
from django.views.decorators.http import require_GET


@require_GET
def health(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [path("internal/v1/health", health)]
