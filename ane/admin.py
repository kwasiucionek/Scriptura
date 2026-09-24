from django.contrib import admin

from ane.models import AneChapter, AneLine, AnePassage, AneText


class ChapterInline(admin.TabularInline):
    model = AneChapter
    extra = 0
    fields = ("order", "stage", "name", "title")


@admin.register(AneText)
class AneTextAdmin(admin.ModelAdmin):
    list_display = ("name", "name_pl", "source", "source_id", "language", "license")
    inlines = [ChapterInline]


@admin.register(AnePassage)
class AnePassageAdmin(admin.ModelAdmin):
    list_display = ("ref", "line_start", "line_end", "has_pl")
    list_filter = ("chapter__text",)
    search_fields = ("translation_en", "translation_pl", "normalized")

    @admin.display(boolean=True, description="PL")
    def has_pl(self, obj):
        return bool(obj.translation_pl)


admin.site.register(AneLine)
