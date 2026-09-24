from django.contrib import admin

from corpus.models import Book, Token, Verse, VerseText, Work


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ("order", "abbr", "name_pl", "testament", "deuterocanonical")
    list_display_links = ("abbr",)
    list_filter = ("testament", "deuterocanonical")
    search_fields = ("abbr", "name_pl", "osis")


@admin.register(Work)
class WorkAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "language", "kind", "access", "year", "verse_count")
    list_filter = ("kind", "language", "access")
    search_fields = ("code", "name")

    @admin.display(description="wersetów")
    def verse_count(self, obj):
        return obj.texts.count()


class VerseTextInline(admin.TabularInline):
    model = VerseText
    extra = 0
    fields = ("work", "text")
    readonly_fields = ("work",)
    can_delete = False


@admin.register(Verse)
class VerseAdmin(admin.ModelAdmin):
    list_display = ("__str__", "osis_id", "ordinal")
    list_select_related = ("book",)
    list_filter = ("book",)
    search_fields = ("osis_id",)
    inlines = [VerseTextInline]


@admin.register(Token)
class TokenAdmin(admin.ModelAdmin):
    list_display = ("surface", "lemma", "strong", "morph", "verse_ref", "position")
    list_select_related = ("verse_text__verse__book", "verse_text__work")
    list_filter = ("verse_text__work",)
    search_fields = ("surface_norm", "lemma_norm", "strong")

    @admin.display(description="werset")
    def verse_ref(self, obj):
        return f"{obj.verse_text.work.code} {obj.verse_text.verse}"
