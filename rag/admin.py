from django.contrib import admin

from rag.models import Conversation, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    fields = ("role", "content", "created")
    readonly_fields = fields
    can_delete = False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "title", "mode", "modified", "message_count")
    list_filter = ("mode", "user")
    search_fields = ("title", "messages__content")
    inlines = [MessageInline]

    @admin.display(description="wiadomości")
    def message_count(self, obj):
        return obj.messages.count()
