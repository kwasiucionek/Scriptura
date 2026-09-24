"""Baza powiązana: teksty starożytnego Bliskiego Wschodu (ANE) — osobno od Biblii i literatury.

Kompozycja (np. Poem of Gilgameš) -> rozdział (tabliczka/wersja: SB XI) -> linia (normalizacja
akadyjska + przekład angielski). Do retrievalu linie sklejane w pasaże (AnePassage) z odniesieniem
w stylu „Gilgamesz SB XI 11–22”. Źródło pierwsze: eBL (LMU), CC BY-NC-SA 4.0.
"""

from django.db import models

from corpus.models import TimeStampedModel


class AneText(TimeStampedModel):
    source = models.CharField(max_length=16, default="ebl")  # ebl | oracc | manual
    source_id = models.CharField(max_length=64)  # eBL: L/1/4
    name = models.CharField(max_length=200)  # Poem of Gilgameš
    name_pl = models.CharField(
        max_length=200, blank=True
    )  # Gilgamesz (do cytowań po polsku)
    language = models.CharField(
        max_length=8, default="akk"
    )  # akk | sux | uga | hit | egy
    intro = models.TextField(blank=True)
    license = models.CharField(max_length=200, blank=True)
    url = models.URLField(blank=True)

    class Meta:
        unique_together = [("source", "source_id")]
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name_pl or self.name


class AneChapter(models.Model):
    text = models.ForeignKey(AneText, on_delete=models.CASCADE, related_name="chapters")
    stage = models.CharField(
        max_length=64, blank=True
    )  # Standard Babylonian, Old Babylonian…
    name = models.CharField(max_length=32)  # I, XI, "Nippur"
    order = models.PositiveSmallIntegerField(default=0)
    title = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["text", "order"]
        unique_together = [("text", "stage", "name")]

    @property
    def label(self) -> str:
        stage = {
            "Standard Babylonian": "SB",
            "Old Babylonian": "OB",
            "Middle Babylonian": "MB",
            "Neo-Assyrian": "NA",
        }.get(self.stage, self.stage)
        return f"{self.text} {stage} {self.name}".strip()

    def __str__(self) -> str:
        return self.label


class AneLine(models.Model):
    chapter = models.ForeignKey(
        AneChapter, on_delete=models.CASCADE, related_name="lines"
    )
    order = models.PositiveIntegerField()
    number = models.CharField(max_length=16)  # "11", "12a", "1'"
    normalized = models.TextField(blank=True)  # šurippak ālu ša tīdûšu attā
    translation_en = models.TextField(blank=True)
    translation_pl = models.TextField(
        blank=True
    )  # maszynowy przekład — tylko do wyszukiwania
    note = models.CharField(max_length=300, blank=True)  # // cf. …

    class Meta:
        ordering = ["chapter", "order"]
        unique_together = [("chapter", "order")]


class AnePassage(models.Model):
    """Pasaż = kilkanaście kolejnych linii; jednostka retrievalu i cytowania."""

    chapter = models.ForeignKey(
        AneChapter, on_delete=models.CASCADE, related_name="passages"
    )
    order = models.PositiveIntegerField()
    line_start = models.CharField(max_length=16)
    line_end = models.CharField(max_length=16)
    normalized = models.TextField(blank=True)
    translation_en = models.TextField()
    translation_pl = models.TextField(blank=True)

    class Meta:
        ordering = ["chapter", "order"]
        unique_together = [("chapter", "order")]

    @property
    def ref(self) -> str:
        return f"{self.chapter.label} {self.line_start}–{self.line_end}"

    @property
    def os_id(self) -> str:
        return f"ane-{self.chapter_id}-{self.order}"
