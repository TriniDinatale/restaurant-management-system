
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


class CobroCuenta(models.Model):
    class Medio(models.TextChoices):
        EFECTIVO = "EFECTIVO", "Efectivo"
        TRANSFERENCIA = "TRANSFERENCIA", "Transferencia"
        TARJETA = "TARJETA", "Tarjeta"

    cuenta = models.OneToOneField(
        Cuenta,
        on_delete=models.PROTECT,
        related_name="cobro",
    )
    mozo = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cobros_cuenta",
    )
    fecha_cobro = models.DateTimeField(auto_now_add=True)
    total_cobrado = models.DecimalField(max_digits=12, decimal_places=2)
    total_esperado = models.DecimalField(max_digits=12, decimal_places=2)
    clave_idempotencia = models.UUIDField(unique=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(total_cobrado__gte=0),
                name="cobro_cuenta_total_no_negativo",
            ),
            models.CheckConstraint(
                condition=Q(total_esperado__gte=0),
                name="cobro_cuenta_esperado_no_negativo",
            ),
        ]


class ImportePagoCuenta(models.Model):
    cobro = models.ForeignKey(
        CobroCuenta,
        on_delete=models.PROTECT,
        related_name="importes",
    )
    medio = models.CharField(max_length=20, choices=CobroCuenta.Medio.choices)
    importe = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["cobro", "medio"],
                name="unique_medio_por_cobro",
            ),
            models.CheckConstraint(
                condition=Q(importe__gt=0),
                name="importe_pago_cuenta_positivo",
            ),
        ]


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

    @transaction.atomic
    def crear_aviso_retiro(self, origen, usuario):
        Cuenta.objects.select_for_update(of=("self",)).get(
            pk=self.cuenta_id,
        )
        pedido = Pedido.objects.select_for_update(of=("self",)).select_related(
            "mesa",
            "mozo",
        ).get(pk=self.pk)
        from .services import detalles_vigentes_de_origen

        if origen == AvisoRetiro.Origen.BARRA:
            descripcion = "Barra lista"
        elif origen == AvisoRetiro.Origen.COCINA:
            descripcion = "Cocina lista"
        else:
            raise ValidationError({"origen": "El origen del aviso no es válido."})

        detalles = detalles_vigentes_de_origen(pedido, origen, lock=True)
        if not detalles:
            raise ValidationError(
                "El pedido no contiene productos vigentes del origen indicado."
            )

        aviso, _ = AvisoRetiro.objects.get_or_create(
            pedido=pedido,
            origen=origen,
            defaults={
                "destinatario": pedido.mozo,
                "generado_por": usuario,
                "mensaje": f"{pedido.mesa.identificacion} — {descripcion}",
            },
        )
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


class SolicitudCancelacionDetalle(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        ACEPTADA = "ACEPTADA", "Aceptada"
        RECHAZADA = "RECHAZADA", "Rechazada"

    detalle = models.ForeignKey(
        DetallePedido,
        on_delete=models.PROTECT,
        related_name="solicitudes_cancelacion",
    )
    solicitante = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="solicitudes_cancelacion_detalle",
    )
    cantidad = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
    )
    clave_idempotencia = models.UUIDField(unique=True, editable=False)
    motivo = models.TextField()
    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(
        max_length=10,
        choices=Estado.choices,
        default=Estado.PENDIENTE,
    )
    resuelto_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="resoluciones_cancelacion_detalle",
    )
    fecha_resolucion = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["fecha_solicitud", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(cantidad__gt=0),
                name="solicitud_cancelacion_cantidad_positiva",
            ),
        ]

    def __str__(self):
        return (
            f"Solicitud {self.pk} · detalle {self.detalle_id} "
            f"· {self.cantidad} · {self.estado}"
        )


class LecturaNotificacionCancelacion(models.Model):
    solicitud = models.ForeignKey(
        SolicitudCancelacionDetalle,
        on_delete=models.CASCADE,
        related_name="lecturas_notificacion",
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="lecturas_notificaciones_cancelacion",
    )
    leido_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["solicitud", "usuario"],
                name="unique_lectura_notificacion_cancelacion",
            ),
        ]


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

        if (
            self.estado == self.Estado.LISTO
            and nuevo_estado == self.Estado.LISTO
        ):
            return

        if (
            self.sector.nombre == Sector.Nombre.BARRA
            and self.estado == self.Estado.PENDIENTE
            and nuevo_estado == self.Estado.LISTO
        ):
            self.estado = nuevo_estado
            self.save(update_fields=["estado"])
            return

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
    class Origen(models.TextChoices):
        BARRA = "BARRA", "Barra"
        COCINA = "COCINA", "Cocina"

    pedido = models.ForeignKey(
        Pedido,
        on_delete=models.CASCADE,
        related_name="avisos_retiro",
    )

    origen = models.CharField(
        max_length=10,
        choices=Origen.choices,
        null=True,
        blank=True,
    )

    destinatario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="avisos_retiro",
    )

    generado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="avisos_retiro_generados",
    )

    mensaje = models.CharField(max_length=100)

    fecha_creacion = models.DateTimeField(auto_now_add=True)

    leido_en = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-fecha_creacion"]
        constraints = [
            models.UniqueConstraint(
                fields=["pedido", "origen"],
                name="unique_aviso_retiro_por_pedido_origen",
            ),
            models.UniqueConstraint(
                fields=["pedido"],
                condition=models.Q(origen__isnull=True),
                name="unique_aviso_retiro_ambiguo_por_pedido",
            ),
        ]
