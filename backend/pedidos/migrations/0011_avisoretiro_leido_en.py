from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pedidos", "0010_clave_idempotencia_cancelacion"),
    ]

    operations = [
        migrations.AddField(
            model_name="avisoretiro",
            name="leido_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
