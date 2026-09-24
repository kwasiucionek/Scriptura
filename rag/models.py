"""Historia rozmów: Conversation (właściciel, tryb) -> Message (pytanie / odpowiedź z metadanymi)."""

from django.conf import settings
from django.db import models

from corpus.models import TimeStampedModel


class Conversation(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversations"
    )
    title = models.CharField(max_length=200, blank=True)  # pierwsze pytanie, skrócone
    mode = models.CharField(max_length=16, default="scientific")

    class Meta:
        ordering = ["-modified"]
        verbose_name = "rozmowa"
        verbose_name_plural = "rozmowy"

    def __str__(self) -> str:
        return self.title or f"rozmowa #{self.pk}"


class Message(models.Model):
    ROLE_CHOICES = [("user", "użytkownik"), ("assistant", "asystent")]

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    content = models.TextField()
    meta = models.JSONField(
        default=dict, blank=True
    )  # user: {authors, works, mode}; assistant: AskResult + sources
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created", "id"]
        verbose_name = "wiadomość"
        verbose_name_plural = "wiadomości"

    def __str__(self) -> str:
        return f"{self.role}: {self.content[:60]}"
