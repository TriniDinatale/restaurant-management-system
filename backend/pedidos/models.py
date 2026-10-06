
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction

from productos.models import Producto
from usuarios.models import Sector, User
from django.db.models import Q
from django.utils import timezone

class Mesa(models.Model):
    class Zona(models.TextChoices):
        SALON = "SALON", "Salón"
        VEREDA_A = "VEREDA_A", "Vereda A"
        VEREDA_B = "VEREDA_B", "Vereda B"

    numero = models.PositiveIntegerField()
    zona = models.CharField(
        max_length=10,
        choices=Zona.choices,
        default=Zona.SALON,
    )

    class Meta:
        ordering = ["zona", "numero"]
        constraints = [
            models.UniqueConstraint(
                fields=["zona", "numero"],
                name="unique_mesa_zona_numero",
            ),
        ]

    @property
    def identificacion(self):
        return f"{self.get_zona_display()} · Mesa {self.numero}"

    def __str__(self):
        return self.identificacion

class Cuenta(models.Model):
    class Estado(models.TextChoices):
        ABIERTA = "ABIERTA", "Abierta"
        CERRADA = "CERRADA", "Cerrada"

    mesa = models.ForeignKey(
        Mesa,
        on_delete=models.PROTECT,
        related_name="cuentas",
    )

    mozo_responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cuentas_responsable",
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

    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="pedidos_creados",
    )

    fecha_creacion = models.DateTimeField(
        auto_now_add=True,
    )
    
    cuenta = models.ForeignKey(
        Cuenta,
        on_delete=models.PROTECT,
        related_name="pedidos",
    )

    retiro_habilitado_en = models.DateTimeField(
        null=True,
        blank=True,
    )

    retiro_habilitado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="retiros_habilitados",
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

    @property
    def cocina_lista(self):
        preparaciones_cocina = self.preparaciones_sectoriales.filter(
            sector__nombre__in={
                Sector.Nombre.PIZZA,
                Sector.Nombre.PLATOS,
            },
        )
        return preparaciones_cocina.exists() and not preparaciones_cocina.exclude(
            estado=PreparacionPedidoSector.Estado.LISTO,
        ).exists()

    def preparaciones_listas_para_retiro(self):
        preparaciones = list(self.preparaciones_sectoriales.all())
        return bool(preparaciones) and all(
            preparacion.estado == PreparacionPedidoSector.Estado.LISTO
            for preparacion in preparaciones
        )

    @property
    def puede_habilitar_retiro(self):
        return (
            self.retiro_habilitado_en is None
            and self.preparaciones_listas_para_retiro()
        )

    @transaction.atomic
    def habilitar_retiro(self, usuario):
        pedido = Pedido.objects.select_for_update().select_related(
            "mesa",
            "mozo",
        ).get(pk=self.pk)

        if pedido.retiro_habilitado_en is not None:
            self.retiro_habilitado_en = pedido.retiro_habilitado_en
            self.retiro_habilitado_por = pedido.retiro_habilitado_por
            return pedido.avisos_retiro.get()

        if not pedido.preparaciones_listas_para_retiro():
            raise ValidationError(
                "Todas las preparaciones deben estar listas para habilitar el retiro."
            )

        fecha_habilitacion = timezone.now()
        Pedido.objects.filter(pk=pedido.pk).update(
            retiro_habilitado_en=fecha_habilitacion,
            retiro_habilitado_por=usuario,
        )
        aviso = AvisoRetiro.objects.create(
            pedido=pedido,
            destinatario=pedido.mozo,
            mensaje=pedido.mesa.identificacion,
        )

        self.retiro_habilitado_en = fecha_habilitacion
        self.retiro_habilitado_por = usuario
        return aviso


class AvisoCargaBarra(models.Model):
    pedido = models.OneToOneField(
        Pedido,
        on_delete=models.CASCADE,
        related_name="aviso_carga_barra",
    )

    destinatario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="avisos_carga_barra",
    )

    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_creacion"]


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


class PreparacionPedidoSector(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        EN_PREPARACION = "EN_PREPARACION", "En preparación"
        LISTO = "LISTO", "Listo"

    pedido = models.ForeignKey(
        Pedido,
        on_delete=models.CASCADE,
        related_name="preparaciones_sectoriales",
    )

    sector = models.ForeignKey(
        Sector,
        on_delete=models.PROTECT,
        related_name="preparaciones_pedido",
    )

    estado = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.PENDIENTE,
    )

    fecha_creacion = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["pedido_id", "sector__nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["pedido", "sector"],
                name="unique_preparacion_pedido_sector",
            ),
        ]

    def transicionar_a(self, nuevo_estado):
        transiciones = {
            self.Estado.PENDIENTE: self.Estado.EN_PREPARACION,
            self.Estado.EN_PREPARACION: self.Estado.LISTO,
        }

        if transiciones.get(self.estado) != nuevo_estado:
            raise ValidationError(
                {
                    "estado": (
                        f"No se puede pasar de {self.estado} "
                        f"a {nuevo_estado}."
                    )
                }
            )

        self.estado = nuevo_estado
        self.save(update_fields=["estado"])

    def __str__(self):
        return f"Preparación del pedido {self.pedido_id} - {self.sector}"


class AvisoRetiro(models.Model):
    pedido = models.ForeignKey(
        Pedido,
        on_delete=models.CASCADE,
        related_name="avisos_retiro",
    )

    destinatario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="avisos_retiro",
    )

    mensaje = models.CharField(max_length=100)

    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_creacion"]
        constraints = [
            models.UniqueConstraint(
                fields=["pedido"],
                name="unique_aviso_retiro_por_pedido",
            ),
        ]
