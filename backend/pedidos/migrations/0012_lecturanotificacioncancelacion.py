from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("pedidos", "0011_avisoretiro_leido_en"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LecturaNotificacionCancelacion",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("leido_en", models.DateTimeField(auto_now_add=True)),
                (
                    "solicitud",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lecturas_notificacion",
                        to="pedidos.solicitudcancelaciondetalle",
                    ),
                ),
                (
                    "usuario",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lecturas_notificaciones_cancelacion",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("solicitud", "usuario"),
                        name="unique_lectura_notificacion_cancelacion",
                    ),
                ],
            },
        ),
    ]
