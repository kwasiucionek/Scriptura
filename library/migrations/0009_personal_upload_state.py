from django.db import migrations, models
from django.db.models import Count


def prepare_owned_documents(apps, schema_editor):
    Document = apps.get_model("library", "Document")
    documents = Document.objects.using(schema_editor.connection.alias)
    documents.filter(owner__isnull=False).update(index_status="pending")
    duplicates = list(
        documents.filter(owner__isnull=False)
        .exclude(content_hash="")
        .values("owner_id", "content_hash")
        .annotate(total=Count("pk"))
        .filter(total__gt=1)
    )
    for group in duplicates:
        matching = documents.filter(
            owner_id=group["owner_id"], content_hash=group["content_hash"]
        )
        keeper = matching.order_by("pk").values_list("pk", flat=True).first()
        # Nie usuwamy dokumentów, plików, chunków ani historii zgód. Najstarszy
        # rekord zachowuje hash i blokuje kolejne uploady tej samej zawartości.
        matching.exclude(pk=keeper).update(content_hash="")


class Migration(migrations.Migration):
    dependencies = [("library", "0008_consent")]

    operations = [
        migrations.AddField(
            model_name="document",
            name="note",
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.AddField(
            model_name="document",
            name="index_status",
            field=models.CharField(
                choices=[
                    ("not_required", "indeks zewnętrzny niewymagany"),
                    ("pending", "oczekuje na indeksowanie"),
                    ("indexed", "zaindeksowany"),
                    ("failed", "błąd indeksowania — można ponowić"),
                ],
                default="not_required",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="index_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="document",
            name="indexed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="document",
            name="index_revision",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.RunPython(prepare_owned_documents, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="document",
            constraint=models.UniqueConstraint(
                fields=("owner", "content_hash"),
                condition=models.Q(owner__isnull=False) & ~models.Q(content_hash=""),
                name="uniq_owned_document_hash",
            ),
        ),
    ]
