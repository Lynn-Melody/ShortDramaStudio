from django.contrib import admin

from studio.admin_tools import ReadOnlyAdmin, ReadOnlyInline

from .models import AssetItem, AssetVersion, EpisodeDelivery


class VersionInline(ReadOnlyInline):
    model = AssetVersion
    fields = ("id", "title", "upload", "created_at")
    ordering = ("-created_at",)


@admin.register(AssetItem)
class AssetItemAdmin(ReadOnlyAdmin):
    list_display = ("asset_key", "kind", "title", "project", "episode", "shot")
    list_filter = ("kind",)
    list_select_related = ("project", "episode", "shot")
    search_fields = ("=project__schedule__id", "asset_key", "title")
    inlines = (VersionInline,)


@admin.register(AssetVersion)
class AssetVersionAdmin(ReadOnlyAdmin):
    list_display = ("id", "asset", "title", "created_at")
    list_select_related = ("asset",)
    search_fields = ("=id", "=asset__id", "asset__asset_key")


@admin.register(EpisodeDelivery)
class EpisodeDeliveryAdmin(ReadOnlyAdmin):
    list_display = ("id", "episode", "execution", "created_at")
    list_select_related = ("episode",)
