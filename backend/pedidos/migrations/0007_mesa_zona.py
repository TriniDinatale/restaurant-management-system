from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pedidos", "0006_pedido_retiro_habilitado_en_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="mesa",
            name="zona",
            field=models.CharField(
                choices=[
                    ("SALON", "Salón"),
                    ("VEREDA_A", "Vereda A"),
                    ("VEREDA_B", "Vereda B"),
                ],
                default="SALON",
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="mesa",
            name="numero",
            field=models.PositiveIntegerField(),
        ),
        migrations.AlterModelOptions(
            name="mesa",
            options={"ordering": ["zona", "numero"]},
        ),
        migrations.AddConstraint(
            model_name="mesa",
            constraint=models.UniqueConstraint(
                fields=("zona", "numero"),
                name="unique_mesa_zona_numero",
            ),
        ),
    ]
