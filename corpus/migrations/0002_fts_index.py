"""Indeks GIN dla wyszukiwania pełnotekstowego — tylko na PostgreSQL.

Lookup `text_norm__search` z config='simple' korzysta z to_tsvector('simple', text_norm);
ten indeks wyrażeniowy pozwala Postgresowi go użyć. Na SQLite migracja nic nie robi.
"""

from django.db import migrations

CREATE = """
CREATE INDEX IF NOT EXISTS corpus_versetext_text_norm_fts
ON corpus_versetext USING GIN (to_tsvector('simple', text_norm));
"""
DROP = "DROP INDEX IF EXISTS corpus_versetext_text_norm_fts;"


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(CREATE)


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(DROP)


class Migration(migrations.Migration):
    dependencies = [("corpus", "0001_initial")]
    operations = [migrations.RunPython(forwards, backwards)]
