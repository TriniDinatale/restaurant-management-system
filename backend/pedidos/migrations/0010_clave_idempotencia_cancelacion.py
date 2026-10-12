import uuid

from django.db import migrations, models


def populate_idempotency_keys(apps, schema_editor):
    solicitud_model = apps.get_model("pedidos", "SolicitudCancelacionDetalle")
    for solicitud in solicitud_model.objects.filter(
        clave_idempotencia__isnull=True,
    ).iterator():
        solicitud_model.objects.filter(pk=solicitud.pk).update(
            clave_idempotencia=uuid.uuid4(),
        )


class Migration(migrations.Migration):

    dependencies = [
        ("pedidos", "0009_solicitud_cancelacion_detalle"),
    ]

    operations = [
        migrations.AddField(
            model_name="solicitudcancelaciondetalle",
            name="clave_idempotencia",
            field=models.UUIDField(null=True),
        ),
        migrations.RunPython(
            populate_idempotency_keys,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="solicitudcancelaciondetalle",
            name="clave_idempotencia",
            field=models.UUIDField(editable=False, unique=True),
        ),
    ]
