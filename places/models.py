"""Baza powiązana: geografia biblijna (OpenBible.info Bible Geocoding Data, CC BY 4.0).

Place — miejsce, jak wymienia je tekst (Betel, Charan); PlaceLocation — kandydaci na
współczesną lokalizację z oceną pewności (wiele kandydatów to norma: Ai ma kilka);
PlaceRef — werset, w którym miejsce występuje (ordinal jak w corpus.models), czyli
odwrotność sigla: pytanie o Rdz 12 -> miejsca wędrówki Abrahama.

Granic królestw, tras i podziałów na pokolenia tu nie ma celowo — to interpretacje,
nie dane; na mapie są tylko punkty z pewnością identyfikacji i odsyłaczem do źródła.
"""

from django.db import models

from corpus.models import TimeStampedModel


class Place(TimeStampedModel):
    openbible_id = models.CharField(max_length=16, unique=True)  # a64f355
    slug = models.SlugField(max_length=80, db_index=True)  # bethel-1
    name = models.CharField(max_length=120, db_index=True)  # Bethel (EN, z friendly_id)
    name_pl = models.CharField(max_length=120, blank=True, db_index=True)  # Betel
    aliases = models.JSONField(
        default=list, blank=True
    )  # inne pisownie z przekładów EN
    kind = models.CharField(
        max_length=16, blank=True
    )  # human | natural | special | human,natural
    types = models.JSONField(
        default=list, blank=True
    )  # ["settlement"], ["mountain", "region"]
    wikidata = models.CharField(max_length=24, blank=True)  # Q3876767
    verse_count = models.PositiveIntegerField(default=0)
    url = models.URLField(blank=True)  # strona miejsca w atlasie OpenBible

    class Meta:
        ordering = ["name"]
        verbose_name = "miejsce"
        verbose_name_plural = "miejsca"

    def __str__(self) -> str:
        return self.name_pl or self.name

    @property
    def label(self) -> str:
        return self.name_pl or self.name


class PlaceLocation(models.Model):
    """Kandydat na lokalizację: punkt (lub środek regionu) z udziałem w ocenie źródeł."""

    place = models.ForeignKey(Place, on_delete=models.CASCADE, related_name="locations")
    order = models.PositiveSmallIntegerField(default=0)  # 0 = najbardziej prawdopodobna
    modern_id = models.CharField(max_length=16, blank=True)  # me58522
    name = models.CharField(max_length=160)  # Beitin; „near Gibeah”
    lon = models.FloatField()
    lat = models.FloatField()
    geometry = models.CharField(max_length=16, default="point")  # point | region | path
    radius_m = models.PositiveIntegerField(
        null=True, blank=True
    )  # przybliżony zasięg regionu
    type = models.CharField(max_length=40, blank=True)  # settlement, mountain, river…
    score = models.IntegerField(default=0)  # suma głosów źródeł (OpenBible vote_total)
    confidence = models.FloatField(default=0.0)  # udział w sumie głosów miejsca, 0–1

    class Meta:
        ordering = ["place", "order"]
        unique_together = [("place", "order")]

    def __str__(self) -> str:
        return f"{self.place} -> {self.name} ({self.confidence:.0%})"


class PlaceRef(models.Model):
    """Wystąpienie miejsca w wersecie."""

    place = models.ForeignKey(Place, on_delete=models.CASCADE, related_name="refs")
    ordinal = models.PositiveIntegerField(db_index=True)

    class Meta:
        unique_together = [("place", "ordinal")]
        indexes = [models.Index(fields=["ordinal", "place"])]

    def __str__(self) -> str:
        return f"{self.place} @ {self.ordinal}"
