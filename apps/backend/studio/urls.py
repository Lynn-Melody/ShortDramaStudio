from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

urlpatterns = [
    path("", include("studio.public_urls")),
    path("", include("studio.internal_urls")),
    path("admin/", admin.site.urls),
    path("api/schema", SpectacularAPIView.as_view(urlconf="studio.public_urls")),
    path("internal/schema", SpectacularAPIView.as_view(urlconf="studio.internal_urls")),
]
