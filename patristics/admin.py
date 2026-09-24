from django.contrib import admin

from patristics.models import PatPassage, PatWork


@admin.register(PatWork)
class PatWorkAdmin(admin.ModelAdmin):
    list_display = ("series", "volume", "author", "author_pl", "title", "title_pl")
    list_filter = ("series", "author")
    search_fields = ("author", "title", "title_pl")
    list_editable = ("author_pl", "title_pl")


@admin.register(PatPassage)
class PatPassageAdmin(admin.ModelAdmin):
    list_display = ("ref", "page")
    list_filter = ("work__series",)
    search_fields = ("text_en", "text_pl")
    raw_id_fields = ("work",)
