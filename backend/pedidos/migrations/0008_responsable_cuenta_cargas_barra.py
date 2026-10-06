import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pedidos", "0007_mesa_zona"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="cuenta",
            name="mozo_responsable",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="cuentas_responsable",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="pedido",
            name="creado_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="pedidos_creados",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name="AvisoCargaBarra",
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
                ("fecha_creacion", models.DateTimeField(auto_now_add=True)),
                (
                    "destinatario",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="avisos_carga_barra",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "pedido",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="aviso_carga_barra",
                        to="pedidos.pedido",
                    ),
                ),
            ],
            options={
                "ordering": ["-fecha_creacion"],
            },
        ),
    ]
