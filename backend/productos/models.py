from django.core.validators import MinValueValidator
from django.db import models

from usuarios.models import Sector
from django.core.exceptions import ValidationError

class Categoria(models.Model):
    nombre = models.CharField(
        max_length=100,
        unique=True,
    )
    activo = models.BooleanField(
        default=True,
    )

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Producto(models.Model):
    nombre = models.CharField(
        max_length=150,
    )
    precio = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    categoria = models.ForeignKey(
        Categoria,
        on_delete=models.PROTECT,
        related_name="productos",
    )
    sector_destino = models.ForeignKey(
        Sector,
        on_delete=models.PROTECT,
        related_name="productos",
    )
    activo = models.BooleanField(
        default=True,
    )

    class Meta:
        ordering = ["nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["nombre", "categoria"],
                name="unique_producto_por_categoria",
            ),
        ]
    def clean(self):
        super().clean()

        if not self.sector_destino_id:
            return

        if self.sector_destino.nombre == Sector.Nombre.SALON:
            raise ValidationError(
                {
                    "sector_destino": (
                        "SALON no puede ser el sector de destino "
                        "de un producto."
                    )
                }
            )
            
    def __str__(self):
        return self.nombre