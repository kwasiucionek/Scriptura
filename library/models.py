"""Warstwa literatury: dokumenty (artykuły, skrypty, blog, wideo) i chunki.

Document.access steruje widocznością na poziomie retrievalu (nie tylko UI):
open — wolna licencja / OA; licensed — za zgodą autora; private — tylko
demo prywatne dla właściciela praw. Chunk trzyma sigla wyciągnięte przez
corpus.sigla.extract jako listę zakresów ordinal, żeby pytanie „co X pisze
o Mk 16,9-20" było filtrem, nie podobieństwem wektorowym.
"""

from django.conf import settings
from django.db import models

from corpus.models import Access, TimeStampedModel


class DocType(models.TextChoices):
    ARTICLE = "article", "artykuł naukowy"
    BOOK = "book", "monografia / książka"
    SCRIPT = "script", "skrypt dydaktyczny"
    BLOG = "blog", "wpis popularnonaukowy"
    VIDEO = "video", "transkrypcja wykładu / wideo"
    LEXICON = "lexicon", "słownik / leksykon"
    REVIEW = "review", "recenzja książki"


class Register(models.TextChoices):
    SCIENTIFIC = "scientific", "naukowy"
    POPULAR = "popular", "popularnonaukowy"
    MIXED = "mixed", "mieszany (podręcznik, skrypt)"


DEFAULT_REGISTER = {  # domyślny rejestr wg typu dokumentu; nadpisywalny przy ingestii
    DocType.ARTICLE: Register.SCIENTIFIC,
    DocType.BOOK: Register.SCIENTIFIC,
    DocType.SCRIPT: Register.MIXED,
    DocType.BLOG: Register.POPULAR,
    DocType.VIDEO: Register.POPULAR,
    DocType.LEXICON: Register.SCIENTIFIC,
    DocType.REVIEW: Register.SCIENTIFIC,
}


class Author(models.Model):
    name = models.CharField(max_length=128, unique=True)  # "Mariusz Rosik"
    slug = models.SlugField(max_length=128, unique=True)
    affiliation = models.CharField(max_length=128, blank=True)  # "PWT Wrocław"
    tradition = models.CharField(
        max_length=64, blank=True
    )  # "katolicka" / "protestancka"

    class Meta:
        ordering = ["name"]
        verbose_name = "autor"
        verbose_name_plural = "autorzy"

    def __str__(self) -> str:
        return self.name


class Document(TimeStampedModel):
    title = models.CharField(max_length=300)
    authors = models.ManyToManyField(Author, related_name="documents", blank=True)
    doc_type = models.CharField(
        max_length=16, choices=DocType.choices, default=DocType.ARTICLE
    )
    journal = models.CharField(max_length=200, blank=True)  # czasopismo / wydawnictwo
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    volume = models.CharField(max_length=32, blank=True)
    pages = models.CharField(max_length=32, blank=True)
    doi = models.CharField(max_length=128, blank=True)
    url = models.URLField(blank=True)
    language = models.CharField(max_length=8, default="pl")
    license = models.CharField(max_length=128, blank=True)  # "CC BY 4.0"
    access = models.CharField(
        max_length=16, choices=Access.choices, default=Access.OPEN
    )
    register = models.CharField(
        max_length=16, choices=Register.choices, default=Register.SCIENTIFIC
    )  # naukowy / popularnonaukowy — steruje trybami odpowiedzi
    source_path = models.CharField(max_length=500, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="documents",
    )  # materiał osobisty użytkownika (access=personal) — widoczny tylko dla niego
    content_hash = models.CharField(
        max_length=32, blank=True, db_index=True
    )  # MD5 pliku źródłowego — dedup tego samego PDF-u pod różnymi rekordami
    abstract = models.TextField(blank=True)
    chunk_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-year", "title"]
        verbose_name = "dokument"
        verbose_name_plural = "dokumenty"

    def __str__(self) -> str:
        return self.title

    @property
    def citation(self) -> str:
        """Zapis bibliograficzny do wyświetlenia przy odpowiedzi."""
        authors = ", ".join(a.name for a in self.authors.all())
        parts = [p for p in [authors, f"„{self.title}”"] if p]
        if self.journal:
            parts.append(self.journal)
        if self.volume:
            parts.append(self.volume)
        if self.year:
            parts.append(str(self.year))
        if self.pages:
            parts.append(f"s. {self.pages}")
        return ", ".join(parts)


class Chunk(models.Model):
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="chunks"
    )
    order = models.PositiveIntegerField()
    section = models.CharField(max_length=300, blank=True)
    text = models.TextField()
    page_start = models.PositiveSmallIntegerField(null=True, blank=True)
    page_end = models.PositiveSmallIntegerField(null=True, blank=True)
    time_start = models.PositiveIntegerField(null=True, blank=True)  # wideo: sekundy
    text_pl = models.TextField(
        blank=True
    )  # maszynowy przekład na polski (dokumenty obce) — TYLKO do wyszukiwania, nie do wyświetlania
    sigla = models.JSONField(
        default=list, blank=True
    )  # [{"ref": "Mk 1,1-8", "start": 48001001, "end": 48001008}]

    class Meta:
        ordering = ["document", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "order"], name="uniq_chunk_order"
            )
        ]
        verbose_name = "chunk"
        verbose_name_plural = "chunki"

    def __str__(self) -> str:
        return f"{self.document_id}#{self.order}"

    @property
    def os_id(self) -> str:
        return f"{self.document_id}-{self.order}"


class Consent(models.Model):
    """Zgoda właściciela materiału osobistego na włączenie go do wspólnego korpusu (access=licensed).
    Zapis dowodowy: kto, kiedy, treść oświadczenia, licencja/zakres. Wycofanie = revoked_at."""

    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="consents"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="consents"
    )
    statement = (
        models.TextField()
    )  # treść oświadczenia zaakceptowanego przez użytkownika
    license = models.CharField(max_length=200)
    register = models.CharField(max_length=16, choices=Register.choices)
    granted_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-granted_at"]
        verbose_name = "zgoda"
        verbose_name_plural = "zgody"

    def __str__(self) -> str:
        return f"{self.user} -> {self.document} ({'wycofana' if self.revoked_at else 'aktywna'})"
