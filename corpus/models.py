"""Warstwa tekstu kanonicznego.

Book      — księga (seedowana z corpus.books.BOOKS)
Work      — konkretne dzieło: tekst oryginalny (WLC, SBLGNT, LXX) lub przekład (BG, BT…)
Verse     — kanoniczny adres wersetu niezależny od dzieła (Mk 1,1 istnieje raz)
VerseText — tekst danego wersetu w danym dziele
Token     — słowo w tekście oryginalnym z lematem, Strongiem i morfologią

`Verse.ordinal` = order*1_000_000 + chapter*1000 + verse — pozwala robić zakresy
sigli jednym BETWEEN bez JOIN-ów (patrz corpus.sigla.ordinal_range).
"""

from django.db import models


class TimeStampedModel(models.Model):
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Testament(models.TextChoices):
    OT = "OT", "Stary Testament"
    NT = "NT", "Nowy Testament"


class Book(models.Model):
    osis = models.CharField(max_length=8, primary_key=True)
    order = models.PositiveSmallIntegerField(unique=True)
    testament = models.CharField(max_length=2, choices=Testament.choices)
    abbr = models.CharField("skrót BT", max_length=8)
    name_pl = models.CharField("nazwa polska", max_length=64)
    deuterocanonical = models.BooleanField(default=False)

    class Meta:
        ordering = ["order"]
        verbose_name = "księga"
        verbose_name_plural = "księgi"

    def __str__(self) -> str:
        return self.abbr


class WorkKind(models.TextChoices):
    ORIGINAL = "original", "tekst oryginalny"
    TRANSLATION = "translation", "przekład"


class Access(models.TextChoices):
    OPEN = "open", "otwarte (wolna licencja / domena publiczna)"
    LICENSED = "licensed", "za zgodą właściciela praw"
    PRIVATE = "private", "tylko prywatnie (użytek własny)"
    PERSONAL = "personal", "osobisty (materiał użytkownika, widoczny tylko dla niego)"


class Work(TimeStampedModel):
    code = models.SlugField(max_length=32, unique=True)  # np. WLC, SBLGNT, BG1632
    name = models.CharField(max_length=128)
    language = models.CharField(max_length=8)  # hbo, grc, pl, la
    kind = models.CharField(max_length=16, choices=WorkKind.choices)
    versification = models.CharField(max_length=16, default="mt")  # mt | lxx | vulgate
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    license = models.CharField(max_length=128, blank=True)
    source_url = models.URLField(blank=True)
    access = models.CharField(
        max_length=16, choices=Access.choices, default=Access.OPEN
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "dzieło"
        verbose_name_plural = "dzieła"

    def __str__(self) -> str:
        return self.code


class VerseQuerySet(models.QuerySet):
    def in_range(self, start: int, end: int):
        return self.filter(ordinal__gte=start, ordinal__lte=end)


class Verse(models.Model):
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="verses")
    chapter = models.PositiveSmallIntegerField()
    verse = models.PositiveSmallIntegerField()
    ordinal = models.PositiveIntegerField(unique=True, db_index=True)
    osis_id = models.CharField(max_length=24, unique=True)  # Mark.1.1

    objects = VerseQuerySet.as_manager()

    class Meta:
        ordering = ["ordinal"]
        constraints = [
            models.UniqueConstraint(
                fields=["book", "chapter", "verse"], name="uniq_verse_addr"
            ),
        ]
        verbose_name = "werset"
        verbose_name_plural = "wersety"

    def __str__(self) -> str:
        return f"{self.book.abbr} {self.chapter},{self.verse}"

    @staticmethod
    def make_ordinal(order: int, chapter: int, verse: int) -> int:
        return order * 1_000_000 + chapter * 1000 + verse


class VerseText(models.Model):
    verse = models.ForeignKey(Verse, on_delete=models.CASCADE, related_name="texts")
    work = models.ForeignKey(Work, on_delete=models.CASCADE, related_name="texts")
    text = models.TextField()
    text_norm = models.TextField(blank=True)  # do wyszukiwania leksykalnego

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["verse", "work"], name="uniq_versetext"),
        ]
        indexes = [models.Index(fields=["work", "verse"])]
        verbose_name = "tekst wersetu"
        verbose_name_plural = "teksty wersetów"

    def __str__(self) -> str:
        return f"{self.work.code}: {self.verse}"


class Token(models.Model):
    """Słowo tekstu oryginalnego. Klucz łączący korpusy = lemma (Strong opcjonalnie)."""

    verse_text = models.ForeignKey(
        VerseText, on_delete=models.CASCADE, related_name="tokens"
    )
    position = models.PositiveSmallIntegerField()
    surface = models.CharField(max_length=64)  # forma w tekście (z akcentami / nikud)
    surface_norm = models.CharField(max_length=64, db_index=True)
    lemma = models.CharField(max_length=64, blank=True, db_index=True)
    lemma_norm = models.CharField(max_length=64, blank=True, db_index=True)
    strong = models.CharField(max_length=12, blank=True, db_index=True)  # H7225 / G746
    morph = models.CharField(max_length=32, blank=True)  # kod morfologiczny źródła
    pos = models.CharField(max_length=8, blank=True)  # część mowy (MorphGNT)
    prefixes = models.CharField(max_length=16, blank=True)  # OSHB: b/, c/, d/…

    class Meta:
        ordering = ["verse_text", "position"]
        constraints = [
            models.UniqueConstraint(
                fields=["verse_text", "position"], name="uniq_token_pos"
            ),
        ]
        verbose_name = "token"
        verbose_name_plural = "tokeny"

    def __str__(self) -> str:
        return self.surface


class Lexeme(models.Model):
    """Hasło leksykonu (STEPBible TBESH/TBESG, CC BY 4.0): Strong -> lemat, transliteracja, glosa.
    Uzupełnia lematy WLC (OSHB daje tylko Strongi) i daje glosy do konkordancji i promptu."""

    strong = models.CharField(
        max_length=12, unique=True
    )  # znormalizowany: H1254a, G3056 (bez zer wiodących)
    language = models.CharField(max_length=3)  # hbo | arc | grc
    lemma = models.CharField(max_length=64)
    lemma_norm = models.CharField(max_length=64, db_index=True)
    transliteration = models.CharField(max_length=64, blank=True)
    translit_fold = models.CharField(
        max_length=64, blank=True, db_index=True
    )  # canonical_translit()
    morph = models.CharField(max_length=24, blank=True)  # STEP: H:N-M, G:V…
    gloss = models.CharField(max_length=200, blank=True)  # krótka glosa (EN)
    meaning = models.TextField(blank=True)  # skrócona definicja (EN, do 600 zn.)

    class Meta:
        ordering = ["strong"]

    def __str__(self) -> str:
        return f"{self.strong} {self.lemma} — {self.gloss}"


class VerseLink(models.Model):
    """Powiązanie wersetów (OpenBible.info cross-references, CC BY; rdzeń: Treasury of Scripture
    Knowledge). from_ordinal -> zakres [to_start, to_end], votes = waga ze społeczności."""

    from_ordinal = models.PositiveIntegerField(db_index=True)
    to_start = models.PositiveIntegerField()
    to_end = models.PositiveIntegerField()
    votes = models.IntegerField(default=0)

    class Meta:
        ordering = ["from_ordinal", "-votes"]
        indexes = [models.Index(fields=["from_ordinal", "-votes"])]

    def __str__(self) -> str:
        return f"{self.from_ordinal} -> {self.to_start}-{self.to_end} ({self.votes})"
