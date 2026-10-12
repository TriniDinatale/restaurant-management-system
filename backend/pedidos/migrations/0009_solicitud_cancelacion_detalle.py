import django.db.models.deletion
import django.core.validators
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pedidos", "0008_responsable_cuenta_cargas_barra"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SolicitudCancelacionDetalle",
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
                (
                    "cantidad",
                    models.PositiveIntegerField(
                        validators=[django.core.validators.MinValueValidator(1)],
                    ),
                ),
                ("motivo", models.TextField()),
                ("fecha_solicitud", models.DateTimeField(auto_now_add=True)),
                (
                    "estado",
                    models.CharField(
                        choices=[
                            ("PENDIENTE", "Pendiente"),
                            ("ACEPTADA", "Aceptada"),
                            ("RECHAZADA", "Rechazada"),
                        ],
                        default="PENDIENTE",
                        max_length=10,
                    ),
                ),
                ("fecha_resolucion", models.DateTimeField(blank=True, null=True)),
                (
                    "detalle",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="solicitudes_cancelacion",
                        to="pedidos.detallepedido",
                    ),
                ),
                (
                    "resuelto_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="resoluciones_cancelacion_detalle",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "solicitante",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="solicitudes_cancelacion_detalle",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["fecha_solicitud", "pk"],
            },
        ),
        migrations.AddConstraint(
            model_name="solicitudcancelaciondetalle",
            constraint=models.CheckConstraint(
                condition=models.Q(("cantidad__gt", 0)),
                name="solicitud_cancelacion_cantidad_positiva",
            ),
        ),
    ]
