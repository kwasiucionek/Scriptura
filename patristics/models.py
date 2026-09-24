"""Baza powiązana: pisma Ojców Kościoła (ANF/NPNF w przekładzie angielskim, domena publiczna, CCEL).

Dzieło (autor, tytuł, tom serii) -> pasaż (kilka akapitów; odniesienie: ANF 1, s. 123, „Ireneusz,
Adversus haereses III 21,1”) -> odsyłacze biblijne z <scripRef> (ordinale wersetów), które łączą
pasaż z tekstem biblijnym: „co Ojcowie mówią o Rdz 1,26” to zapytanie po PatRef, nie po treści.
"""

from django.db import models

from corpus.models import TimeStampedModel


class PatWork(TimeStampedModel):
    series = models.CharField(max_length=8)  # ANF | NPNF1 | NPNF2
    volume = models.PositiveSmallIntegerField()
    ccel_id = models.CharField(max_length=32)  # anf01
    author = models.CharField(max_length=120)  # Irenaeus of Lyons
    author_pl = models.CharField(max_length=120, blank=True)  # Ireneusz z Lyonu
    title = models.CharField(max_length=300)  # Against Heresies
    title_pl = models.CharField(max_length=300, blank=True)
    url = models.URLField(blank=True)
    license = models.CharField(
        max_length=120, default="domena publiczna (przekład XIX w.; CCEL)"
    )

    class Meta:
        ordering = ["series", "volume", "id"]
        unique_together = [("ccel_id", "author", "title")]

    def __str__(self) -> str:
        return f"{self.author_pl or self.author}, {self.title_pl or self.title}"


class PatPassage(models.Model):
    work = models.ForeignKey(PatWork, on_delete=models.CASCADE, related_name="passages")
    order = models.PositiveIntegerField()
    section = models.CharField(max_length=300, blank=True)  # „Book III, Chapter XXI”
    page = models.CharField(
        max_length=16, blank=True
    )  # numer strony wydania drukowanego (<pb>)
    text_en = models.TextField()
    text_pl = models.TextField(blank=True)  # maszynowy przekład — tylko do wyszukiwania

    class Meta:
        ordering = ["work", "order"]
        unique_together = [("work", "order")]

    @property
    def ref(self) -> str:
        w = self.work
        loc = f"{w.series} {w.volume}" + (f", s. {self.page}" if self.page else "")
        return f"{w}{', ' + self.section if self.section else ''} ({loc})"

    @property
    def os_id(self) -> str:
        return f"pat-{self.work_id}-{self.order}"


class PatRef(models.Model):
    """Odsyłacz biblijny z pasażu (zakres ordinali) — kanał „Tradycja o tym wersecie”."""

    passage = models.ForeignKey(
        PatPassage, on_delete=models.CASCADE, related_name="refs"
    )
    start = models.PositiveIntegerField(db_index=True)
    end = models.PositiveIntegerField()

    class Meta:
        indexes = [models.Index(fields=["start", "end"])]
