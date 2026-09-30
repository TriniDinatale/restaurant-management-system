
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from productos.models import Producto
from usuarios.models import Sector, User
from django.db.models import Q
from django.utils import timezone

class Mesa(models.Model):
    numero = models.PositiveIntegerField(unique=True)

    class Meta:
        ordering = ["numero"]

    def __str__(self):
        return f"Mesa {self.numero}"

class Cuenta(models.Model):
    class Estado(models.TextChoices):
        ABIERTA = "ABIERTA", "Abierta"
        CERRADA = "CERRADA", "Cerrada"

    mesa = models.ForeignKey(
        Mesa,
        on_delete=models.PROTECT,
        related_name="cuentas",
    )

    estado = models.CharField(
        max_length=10,
        choices=Estado.choices,
        default=Estado.ABIERTA,
    )

    fecha_apertura = models.DateTimeField(
        auto_now_add=True,
    )

    fecha_cierre = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-fecha_apertura"]
        constraints = [
            models.UniqueConstraint(
                fields=["mesa"],
                condition=Q(estado="ABIERTA"),
                name="unique_cuenta_abierta_por_mesa",
            ),
        ]

    def __str__(self):
        return f"Cuenta {self.pk} - {self.mesa} - {self.estado}"
    
    def cerrar(self):
        if self.estado == self.Estado.CERRADA:
            raise ValidationError(
                "La cuenta ya está cerrada."
            )

        self.estado = self.Estado.CERRADA
        self.fecha_cierre = timezone.now()
        self.save(
            update_fields=["estado", "fecha_cierre"],
        )


class Pedido(models.Model):
    mesa = models.ForeignKey(
        Mesa,
        on_delete=models.PROTECT,
        related_name="pedidos",
    )

    mozo = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pedidos",
    )

    fecha_creacion = models.DateTimeField(
        auto_now_add=True,
    )
    
    cuenta = models.ForeignKey(
        Cuenta,
        on_delete=models.PROTECT,
        related_name="pedidos",
        null=True,
        blank=True,
    )


    class Meta:
        ordering = ["-fecha_creacion"]

    
    def clean(self):
        super().clean()

        if self.mozo_id and self.mozo.rol != User.Rol.MOZO:
            raise ValidationError(
                {"mozo": "El responsable debe ser un mozo."}
            )

        if self.cuenta_id:
            if self.cuenta.estado != Cuenta.Estado.ABIERTA:
                raise ValidationError(
                    {"cuenta": "No se pueden agregar pedidos a una cuenta cerrada."}
                )

            if self.mesa_id != self.cuenta.mesa_id:
                raise ValidationError(
                    {"mesa": "La mesa no coincide con la cuenta indicada."}
                )

    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Pedido {self.pk} - {self.mesa}"


class DetallePedido(models.Model):
    pedido = models.ForeignKey(
        Pedido,
        on_delete=models.PROTECT,
        related_name="detalles",
    )

    producto = models.ForeignKey(
        Producto,
        on_delete=models.PROTECT,
        related_name="detalles_pedido",
    )

    cantidad = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
    )

    precio_unitario = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )

    sector_destino = models.ForeignKey(
        Sector,
        on_delete=models.PROTECT,
        related_name="detalles_pedido",
    )

    def clean(self):
        super().clean()

        if self.producto_id:
            if not self.producto.activo:
                raise ValidationError(
                    {"producto": "El producto está inactivo."}
                )

            if not self.producto.categoria.activo:
                raise ValidationError(
                    {
                        "producto": (
                            "La categoría del producto está inactiva."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        if self._state.adding and self.producto_id:
            self.precio_unitario = self.producto.precio
            self.sector_destino = self.producto.sector_destino

        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def subtotal(self):
        return self.cantidad * self.precio_unitario

    def __str__(self):
        return f"{self.cantidad} x {self.producto.nombre}"
