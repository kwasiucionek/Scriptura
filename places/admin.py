from django.contrib import admin

from places.models import Place, PlaceLocation, PlaceRef


class LocationInline(admin.TabularInline):
    model = PlaceLocation
    extra = 0
    fields = ("order", "name", "lat", "lon", "geometry", "type", "score", "confidence")


@admin.register(Place)
class PlaceAdmin(admin.ModelAdmin):
    list_display = ("name", "name_pl", "kind", "verse_count", "openbible_id")
    search_fields = ("name", "name_pl", "aliases", "openbible_id")
    list_filter = ("kind",)
    inlines = [LocationInline]


@admin.register(PlaceRef)
class PlaceRefAdmin(admin.ModelAdmin):
    list_display = ("place", "ordinal")
    search_fields = ("place__name", "place__name_pl")
    raw_id_fields = ("place",)
