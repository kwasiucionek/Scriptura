from django.db import migrations, models

# Noty przy cytatach dla dzieł już zaimportowanych (nowe instalacje: import_corpus)
NOTES = {
    "BG1632": (
        "Biblia Gdańska (1632) — domena publiczna; plik cyfrowy: bible-api-io (MIT-0)",
        "https://github.com/bible-api-io/bible-api-version-bg",
    ),
    "WLC": (
        "Westminster Leningrad Codex — domena publiczna; lematy i morfologia: "
        "Open Scriptures Hebrew Bible (CC BY 4.0)",
        "",
    ),
    "SBLGNT": (
        "SBL Greek New Testament © 2010 Society of Biblical Literature i Logos Bible Software "
        "(CC BY 4.0); morfologia: MorphGNT (CC BY-SA 3.0)",
        "",
    ),
    "LXX": (
        "Septuaginta, wyd. A. Rahlfs (1935); opracowanie cyfrowe i lematy: E. Wong, OSSP "
        "(CC BY-NC-SA 4.0)",
        "",
    ),
}


def fill(apps, schema_editor):
    Work = apps.get_model("corpus", "Work")
    for code, (note, url) in NOTES.items():
        for w in Work.objects.filter(code=code):
            w.attribution = w.attribution or note
            w.source_url = w.source_url or url
            w.save(update_fields=["attribution", "source_url"])


class Migration(migrations.Migration):
    dependencies = [("corpus", "0007_lexicon_strong_interlinear")]

    operations = [
        migrations.AddField(
            model_name="work",
            name="attribution",
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]
