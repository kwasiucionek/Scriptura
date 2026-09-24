from django.contrib import admin

from library.models import Author, Chunk, Consent, Document


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ("name", "affiliation", "tradition")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


class ChunkInline(admin.TabularInline):
    model = Chunk
    extra = 0
    fields = ("order", "section", "page_start", "sigla")
    readonly_fields = fields
    can_delete = False
    show_change_link = True


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "author_names",
        "doc_type",
        "journal",
        "year",
        "access",
        "chunk_count",
    )
    list_filter = ("doc_type", "access", "authors")
    search_fields = ("title", "journal", "doi")
    filter_horizontal = ("authors",)
    inlines = [ChunkInline]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("authors")

    @admin.display(description="autorzy")
    def author_names(self, obj):
        return ", ".join(a.name for a in obj.authors.all())


@admin.register(Chunk)
class ChunkAdmin(admin.ModelAdmin):
    list_display = ("__str__", "document", "section", "page_start", "sigla_short")
    list_select_related = ("document",)
    search_fields = ("text", "section")
    list_filter = ("document",)

    @admin.display(description="sigla")
    def sigla_short(self, obj):
        return ", ".join(s["ref"] for s in obj.sigla[:5])


@admin.register(Consent)
class ConsentAdmin(admin.ModelAdmin):
    list_display = (
        "document",
        "user",
        "register",
        "license",
        "granted_at",
        "revoked_at",
    )
    list_filter = ("register", "revoked_at")
    readonly_fields = ("statement", "granted_at")
