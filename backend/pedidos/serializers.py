
from decimal import Decimal

from django.db import IntegrityError, transaction
from rest_framework import serializers

from productos.models import Producto
from usuarios.models import Sector, User

from .models import (
    AvisoCargaBarra,
    AvisoRetiro,
    Cuenta,
    DetallePedido,
    Mesa,
    Pedido,
    PreparacionPedidoSector,
    SolicitudCancelacionDetalle,
)


class DetallePedidoSerializer(serializers.ModelSerializer):
    producto = serializers.PrimaryKeyRelatedField(
        queryset=Producto.objects.filter(
            activo=True,
            categoria__activo=True,
            sector_destino__activo=True,
        )
    )

    class Meta:
        model = DetallePedido
        fields = [
            "id",
            "producto",
            "cantidad",
            "precio_unitario",
            "sector_destino",
        ]

        read_only_fields = [
            "id",
            "precio_unitario",
            "sector_destino",
        ]


def crear_pedido_para_cuenta(cuenta, mesa, detalles_data, usuario):
    if cuenta.estado != Cuenta.Estado.ABIERTA:
        raise serializers.ValidationError(
            {"cuenta": "No se pueden agregar pedidos a una cuenta cerrada."}
        )
    if cuenta.mesa_id != mesa.pk:
        raise serializers.ValidationError(
            {"mesa": "La mesa no coincide con la cuenta indicada."}
        )

    if usuario.rol == User.Rol.MOZO:
        if cuenta.mozo_responsable_id is None:
            if cuenta.pedidos.exists():
                raise serializers.ValidationError(
                    {
                        "cuenta": (
                            "La cuenta histórica tiene pedidos pero no "
                            "responsable. Debe resolverse "
                            "administrativamente antes de continuar."
                        )
                    }
                )
            cuenta.mozo_responsable = usuario
            cuenta.save(update_fields=["mozo_responsable"])
        elif cuenta.mozo_responsable_id != usuario.pk:
            raise serializers.ValidationError(
                {
                    "cuenta": (
                        "Solo el mozo responsable de esta cuenta puede "
                        "enviar pedidos."
                    )
                }
            )
    elif cuenta.mozo_responsable_id is None:
        raise serializers.ValidationError(
            {
                "cuenta": (
                    "Barra solo puede cargar pedidos a una cuenta "
                    "con responsable."
                )
            }
        )

    responsable = cuenta.mozo_responsable
    pedido = Pedido.objects.create(
        mesa=mesa,
        cuenta=cuenta,
        mozo=responsable,
        creado_por=usuario,
    )

    for detalle_data in detalles_data:
        DetallePedido.objects.create(
            pedido=pedido,
            **detalle_data,
        )

    sectores_ids = pedido.detalles.values_list(
        "sector_destino_id",
        flat=True,
    ).distinct()
    PreparacionPedidoSector.objects.bulk_create(
        [
            PreparacionPedidoSector(
                pedido=pedido,
                sector_id=sector_id,
            )
            for sector_id in sectores_ids
        ]
    )

    if usuario.rol == User.Rol.BARRA:
        AvisoCargaBarra.objects.create(
            pedido=pedido,
            destinatario=responsable,
        )

    return pedido


class PedidoSerializer(serializers.ModelSerializer):
    detalles = DetallePedidoSerializer(
        many=True,
    )

    mesa = serializers.PrimaryKeyRelatedField(
        queryset=Mesa.objects.all(),
    )
    
    
    cuenta = serializers.PrimaryKeyRelatedField(
        queryset=Cuenta.objects.filter(
            estado=Cuenta.Estado.ABIERTA,
        ),
        write_only=True,
    )


    class Meta:
        model = Pedido
        fields = [
            "id",
            "mesa",
            "cuenta",
            "mozo",
            "fecha_creacion",
            "detalles",
            "retiro_habilitado_en",
            "retiro_habilitado_por",
        ]

        read_only_fields = [
            "id",
            "mozo",
            "fecha_creacion",
            "retiro_habilitado_en",
            "retiro_habilitado_por",
        ]

    def validate_detalles(self, value):
        if not value:
            raise serializers.ValidationError(
                "El pedido debe contener al menos un producto."
            )

        return value
    
    def validate(self, attrs):
        attrs = super().validate(attrs)

        cuenta = attrs["cuenta"]
        mesa = attrs["mesa"]

        if cuenta.mesa_id != mesa.pk:
            raise serializers.ValidationError(
                {
                    "mesa": "La mesa no coincide con la cuenta indicada."
                }
            )

        return attrs

    

    @transaction.atomic
    def create(self, validated_data):
        detalles_data = validated_data.pop("detalles")
        request = self.context["request"]
        usuario = request.user
        cuenta_enviada = validated_data.pop("cuenta")
        mesa_enviada = validated_data.pop("mesa")

        try:
            mesa = Mesa.objects.select_for_update().get(pk=mesa_enviada.pk)
        except Mesa.DoesNotExist as exc:
            raise serializers.ValidationError(
                {"mesa": "La mesa indicada ya no existe."}
            ) from exc

        try:
            cuenta = (
                Cuenta.objects.select_for_update(of=("self",))
                .select_related("mesa", "mozo_responsable")
                .get(pk=cuenta_enviada.pk)
            )
        except Cuenta.DoesNotExist as exc:
            raise serializers.ValidationError(
                {"cuenta": "La cuenta indicada ya no existe."}
            ) from exc

        return crear_pedido_para_cuenta(
            cuenta=cuenta,
            mesa=mesa,
            detalles_data=detalles_data,
            usuario=usuario,
        )


class PedidoPorMesaSerializer(serializers.Serializer):
    mesa = serializers.PrimaryKeyRelatedField(queryset=Mesa.objects.all())
    detalles = DetallePedidoSerializer(many=True)

    def validate_detalles(self, value):
        if not value:
            raise serializers.ValidationError(
                "El pedido debe contener al menos un producto."
            )
        return value

    @transaction.atomic
    def create(self, validated_data):
        mesa_enviada = validated_data["mesa"]
        detalles_data = validated_data["detalles"]
        usuario = self.context["request"].user

        try:
            mesa = Mesa.objects.select_for_update().get(pk=mesa_enviada.pk)
        except Mesa.DoesNotExist as exc:
            raise serializers.ValidationError(
                {"mesa": "La mesa indicada ya no existe."}
            ) from exc

        cuenta = (
            Cuenta.objects.select_for_update(of=("self",))
            .select_related("mesa", "mozo_responsable")
            .filter(mesa=mesa, estado=Cuenta.Estado.ABIERTA)
            .first()
        )
        if cuenta is None:
            cuenta = Cuenta.objects.create(mesa=mesa)

        return crear_pedido_para_cuenta(
            cuenta=cuenta,
            mesa=mesa,
            detalles_data=detalles_data,
            usuario=usuario,
        )

    def to_representation(self, instance):
        return PedidoSerializer(instance, context=self.context).data


class CuentaSerializer(serializers.ModelSerializer):
    mesa_identificacion = serializers.CharField(
        source="mesa.identificacion",
        read_only=True,
    )
    mozo_responsable_id = serializers.IntegerField(read_only=True)
    mozo_responsable_nombre = serializers.SerializerMethodField()

    class Meta:
        model = Cuenta
        fields = [
            "id",
            "mesa",
            "mesa_identificacion",
            "mozo_responsable_id",
            "mozo_responsable_nombre",
            "estado",
            "fecha_apertura",
            "fecha_cierre",
        ]
        read_only_fields = [
            "id",
            "estado",
            "fecha_apertura",
            "fecha_cierre",
            "mesa_identificacion",
            "mozo_responsable_id",
            "mozo_responsable_nombre",
        ]

    def get_mozo_responsable_nombre(self, cuenta):
        responsable = cuenta.mozo_responsable
        if responsable is None:
            return None
        return responsable.get_full_name().strip() or responsable.username

    def validate_mesa(self, mesa):
        if Cuenta.objects.filter(
            mesa=mesa,
            estado=Cuenta.Estado.ABIERTA,
        ).exists():
            raise serializers.ValidationError(
                "Esta mesa ya tiene una cuenta abierta."
            )

        return mesa

    def create(self, validated_data):
        mesa_enviada = validated_data["mesa"]
        try:
            with transaction.atomic():
                try:
                    mesa = Mesa.objects.select_for_update().get(
                        pk=mesa_enviada.pk,
                    )
                except Mesa.DoesNotExist as exc:
                    raise serializers.ValidationError(
                        {"mesa": "La mesa indicada ya no existe."}
                    ) from exc

                if Cuenta.objects.filter(
                    mesa=mesa,
                    estado=Cuenta.Estado.ABIERTA,
                ).exists():
                    raise serializers.ValidationError(
                        {"mesa": "Esta mesa ya tiene una cuenta abierta."}
                    )
                return super().create(validated_data)
        except IntegrityError as exc:
            constraint_name = getattr(
                getattr(exc.__cause__, "diag", None),
                "constraint_name",
                None,
            )
            if constraint_name != "unique_cuenta_abierta_por_mesa":
                raise

            raise serializers.ValidationError(
                {"mesa": "Esta mesa ya tiene una cuenta abierta."}
            ) from exc


class SolicitudCancelacionSerializer(serializers.ModelSerializer):
    solicitante_id = serializers.IntegerField(read_only=True)
    solicitante_nombre = serializers.SerializerMethodField()
    resuelto_por_id = serializers.IntegerField(read_only=True)
    resuelto_por_nombre = serializers.SerializerMethodField()

    class Meta:
        model = SolicitudCancelacionDetalle
        fields = [
            "id",
            "clave_idempotencia",
            "cantidad",
            "motivo",
            "fecha_solicitud",
            "estado",
            "solicitante_id",
            "solicitante_nombre",
            "resuelto_por_id",
            "resuelto_por_nombre",
            "fecha_resolucion",
        ]
        read_only_fields = fields

    def get_solicitante_nombre(self, solicitud):
        return (
            solicitud.solicitante.get_full_name().strip()
            or solicitud.solicitante.username
        )

    def get_resuelto_por_nombre(self, solicitud):
        if solicitud.resuelto_por is None:
            return None
        return (
            solicitud.resuelto_por.get_full_name().strip()
            or solicitud.resuelto_por.username
        )


class SolicitudCancelacionCreateSerializer(serializers.Serializer):
    clave_idempotencia = serializers.UUIDField()
    cantidad = serializers.IntegerField(min_value=1)
    motivo = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=True,
    )


class ResolverCancelacionSerializer(serializers.Serializer):
    estado = serializers.ChoiceField(
        choices=[SolicitudCancelacionDetalle.Estado.ACEPTADA],
    )


class SolicitudCancelacionPendienteSerializer(SolicitudCancelacionSerializer):
    cuenta_id = serializers.IntegerField(source="detalle.pedido.cuenta_id")
    mesa_identificacion = serializers.CharField(
        source="detalle.pedido.mesa.identificacion",
    )
    zona = serializers.SerializerMethodField()
    mesa_numero = serializers.IntegerField(source="detalle.pedido.mesa.numero")
    pedido_id = serializers.IntegerField(source="detalle.pedido_id")
    detalle_id = serializers.IntegerField(read_only=True)
    producto_nombre = serializers.CharField(source="detalle.producto.nombre")
    cantidad_original = serializers.IntegerField(source="detalle.cantidad")
    cantidad_cancelada = serializers.SerializerMethodField()
    cantidad_vigente = serializers.SerializerMethodField()
    precio_unitario = serializers.DecimalField(
        source="detalle.precio_unitario",
        max_digits=10,
        decimal_places=2,
    )

    class Meta(SolicitudCancelacionSerializer.Meta):
        fields = SolicitudCancelacionSerializer.Meta.fields + [
            "cuenta_id",
            "mesa_identificacion",
            "zona",
            "mesa_numero",
            "pedido_id",
            "detalle_id",
            "producto_nombre",
            "cantidad_original",
            "cantidad_cancelada",
            "cantidad_vigente",
            "precio_unitario",
        ]

    def get_zona(self, solicitud):
        return solicitud.detalle.pedido.mesa.get_zona_display()

    def get_cantidad_cancelada(self, solicitud):
        detalle = solicitud.detalle
        return sum(
            otra.cantidad
            for otra in detalle.solicitudes_cancelacion.all()
            if otra.estado == SolicitudCancelacionDetalle.Estado.ACEPTADA
        )

    def get_cantidad_vigente(self, solicitud):
        return (
            solicitud.detalle.cantidad
            - self.get_cantidad_cancelada(solicitud)
        )


class DetalleConsumoSerializer(serializers.ModelSerializer):
    producto_nombre = serializers.CharField(
        source="producto.nombre",
        read_only=True,
    )
    cantidad_cancelada = serializers.SerializerMethodField()
    cantidad_vigente = serializers.SerializerMethodField()
    cantidad_pendiente = serializers.SerializerMethodField()
    cantidad_disponible_cancelacion = serializers.SerializerMethodField()
    subtotal_original = serializers.SerializerMethodField()
    subtotal = serializers.SerializerMethodField()
    solicitudes_cancelacion = SolicitudCancelacionSerializer(
        many=True,
        read_only=True,
    )

    class Meta:
        model = DetallePedido
        fields = [
            "id",
            "producto",
            "producto_nombre",
            "cantidad",
            "cantidad_cancelada",
            "cantidad_vigente",
            "cantidad_pendiente",
            "cantidad_disponible_cancelacion",
            "precio_unitario",
            "subtotal_original",
            "subtotal",
            "solicitudes_cancelacion",
        ]
        read_only_fields = fields

    def get_cantidad_cancelada(self, detalle):
        return sum(
            solicitud.cantidad
            for solicitud in detalle.solicitudes_cancelacion.all()
            if solicitud.estado == SolicitudCancelacionDetalle.Estado.ACEPTADA
        )

    def get_cantidad_pendiente(self, detalle):
        return sum(
            solicitud.cantidad
            for solicitud in detalle.solicitudes_cancelacion.all()
            if solicitud.estado == SolicitudCancelacionDetalle.Estado.PENDIENTE
        )

    def get_cantidad_vigente(self, detalle):
        return detalle.cantidad - self.get_cantidad_cancelada(detalle)

    def get_cantidad_disponible_cancelacion(self, detalle):
        return (
            detalle.cantidad
            - self.get_cantidad_cancelada(detalle)
            - self.get_cantidad_pendiente(detalle)
        )

    def get_subtotal_original(self, detalle):
        return detalle.subtotal

    def get_subtotal(self, detalle):
        return detalle.precio_unitario * self.get_cantidad_vigente(detalle)


class PedidoConsumoSerializer(serializers.ModelSerializer):
    autor_id = serializers.SerializerMethodField()
    autor_nombre = serializers.SerializerMethodField()
    agregado_por_barra = serializers.SerializerMethodField()
    detalles = DetalleConsumoSerializer(many=True, read_only=True)

    class Meta:
        model = Pedido
        fields = [
            "id",
            "fecha_creacion",
            "autor_id",
            "autor_nombre",
            "agregado_por_barra",
            "detalles",
        ]
        read_only_fields = fields

    def get_autor_nombre(self, pedido):
        autor = pedido.creado_por or pedido.mozo
        return autor.get_full_name().strip() or autor.username

    def get_autor_id(self, pedido):
        return pedido.creado_por_id or pedido.mozo_id

    def get_agregado_por_barra(self, pedido):
        return getattr(pedido, "aviso_carga_barra", None) is not None


class CuentaConsumoSerializer(serializers.ModelSerializer):
    mesa_identificacion = serializers.CharField(
        source="mesa.identificacion",
        read_only=True,
    )
    responsable = serializers.SerializerMethodField()
    pedidos = PedidoConsumoSerializer(many=True, read_only=True)
    total = serializers.SerializerMethodField()

    class Meta:
        model = Cuenta
        fields = [
            "id",
            "mesa",
            "mesa_identificacion",
            "responsable",
            "pedidos",
            "total",
        ]
        read_only_fields = fields

    def get_responsable(self, cuenta):
        responsable = cuenta.mozo_responsable
        if responsable is None:
            return None
        return {
            "id": responsable.pk,
            "nombre": (
                responsable.get_full_name().strip() or responsable.username
            ),
        }

    def get_total(self, cuenta):
        return sum(
            (
                detalle.precio_unitario
                * (
                    detalle.cantidad
                    - sum(
                        solicitud.cantidad
                        for solicitud in detalle.solicitudes_cancelacion.all()
                        if solicitud.estado
                        == SolicitudCancelacionDetalle.Estado.ACEPTADA
                    )
                )
                for pedido in cuenta.pedidos.all()
                for detalle in pedido.detalles.all()
            ),
            Decimal("0.00"),
        )


class MesaSerializer(serializers.ModelSerializer):
    identificacion = serializers.CharField(read_only=True)

    class Meta:
        model = Mesa
        fields = [
            "id",
            "numero",
            "zona",
            "identificacion",
        ]
        read_only_fields = fields


class DetallePreparacionProductoSerializer(serializers.ModelSerializer):
    producto_nombre = serializers.CharField(
        source="producto.nombre",
        read_only=True,
    )

    class Meta:
        model = DetallePedido
        fields = [
            "id",
            "producto",
            "producto_nombre",
            "cantidad",
            "precio_unitario",
        ]
        read_only_fields = fields


class PreparacionPedidoSectorSerializer(serializers.ModelSerializer):
    pedido = serializers.IntegerField(
        source="pedido_id",
        read_only=True,
    )
    sector = serializers.CharField(
        source="sector.nombre",
        read_only=True,
    )
    detalles = serializers.SerializerMethodField()
    coordinacion_cocina = serializers.SerializerMethodField()

    class Meta:
        model = PreparacionPedidoSector
        fields = [
            "id",
            "pedido",
            "sector",
            "estado",
            "fecha_creacion",
            "detalles",
            "coordinacion_cocina",
        ]
        read_only_fields = fields

    def get_detalles(self, preparacion):
        detalles = preparacion.pedido.detalles.filter(
            sector_destino_id=preparacion.sector_id,
        ).select_related("producto")
        return DetallePreparacionProductoSerializer(
            detalles,
            many=True,
        ).data

    def get_coordinacion_cocina(self, preparacion):
        request = self.context.get("request")
        if (
            request is None
            or request.user.rol != User.Rol.COCINA
            or preparacion.sector.nombre
            not in {Sector.Nombre.PIZZA, Sector.Nombre.PLATOS}
        ):
            return []

        preparaciones = preparacion.pedido.preparaciones_sectoriales.filter(
            sector__nombre__in={Sector.Nombre.PIZZA, Sector.Nombre.PLATOS},
        ).exclude(
            pk=preparacion.pk,
        ).select_related("sector")

        return [
            {
                "sector": otra.sector.nombre,
                "estado": otra.estado,
            }
            for otra in preparaciones
        ]


class ControlRetiroBarraSerializer(serializers.ModelSerializer):
    mesa = serializers.IntegerField(
        source="mesa.numero",
        read_only=True,
    )
    zona = serializers.CharField(
        source="mesa.zona",
        read_only=True,
    )
    identificacion = serializers.CharField(
        source="mesa.identificacion",
        read_only=True,
    )
    pedido = serializers.IntegerField(
        source="pk",
        read_only=True,
    )
    mozo_id = serializers.IntegerField(
        read_only=True,
    )
    mozo_nombre = serializers.SerializerMethodField()
    productos_cocina = serializers.SerializerMethodField()
    estados_sectores = serializers.SerializerMethodField()
    retiro_habilitado = serializers.SerializerMethodField()
    puede_habilitar_retiro = serializers.BooleanField(read_only=True)

    class Meta:
        model = Pedido
        fields = [
            "mesa",
            "zona",
            "identificacion",
            "pedido",
            "mozo_id",
            "mozo_nombre",
            "productos_cocina",
            "estados_sectores",
            "retiro_habilitado",
            "puede_habilitar_retiro",
        ]
        read_only_fields = fields

    def get_mozo_nombre(self, pedido):
        return pedido.mozo.get_full_name().strip() or pedido.mozo.username

    def get_productos_cocina(self, pedido):
        sectores_cocina = {Sector.Nombre.PIZZA, Sector.Nombre.PLATOS}
        return [
            {
                "producto_nombre": detalle.producto.nombre,
                "cantidad": detalle.cantidad,
                "sector": detalle.sector_destino.nombre,
            }
            for detalle in pedido.detalles.all()
            if detalle.sector_destino.nombre in sectores_cocina
        ]

    def get_estados_sectores(self, pedido):
        return [
            {
                "sector": preparacion.sector.nombre,
                "estado": preparacion.estado,
            }
            for preparacion in pedido.preparaciones_sectoriales.select_related(
                "sector",
            )
        ]

    def get_retiro_habilitado(self, pedido):
        return pedido.retiro_habilitado_en is not None


class DetalleCargaBarraSerializer(serializers.ModelSerializer):
    producto_nombre = serializers.CharField(
        source="producto.nombre",
        read_only=True,
    )

    class Meta:
        model = DetallePedido
        fields = [
            "producto",
            "producto_nombre",
            "cantidad",
            "precio_unitario",
            "sector_destino",
        ]
        read_only_fields = fields


class AvisoCargaBarraSerializer(serializers.ModelSerializer):
    pedido = serializers.IntegerField(source="pedido_id", read_only=True)
    mesa_identificacion = serializers.CharField(
        source="pedido.mesa.identificacion",
        read_only=True,
    )
    detalles = DetalleCargaBarraSerializer(
        source="pedido.detalles",
        many=True,
        read_only=True,
    )

    class Meta:
        model = AvisoCargaBarra
        fields = [
            "id",
            "pedido",
            "mesa_identificacion",
            "detalles",
            "fecha_creacion",
        ]
        read_only_fields = fields


class RegistroCargaBarraSerializer(serializers.ModelSerializer):
    autor_id = serializers.IntegerField(source="creado_por_id", read_only=True)
    autor_nombre = serializers.SerializerMethodField()
    cuenta = serializers.IntegerField(source="cuenta_id", read_only=True)
    mesa_identificacion = serializers.CharField(
        source="mesa.identificacion",
        read_only=True,
    )
    responsable_id = serializers.IntegerField(
        source="cuenta.mozo_responsable_id",
        read_only=True,
    )
    responsable_nombre = serializers.SerializerMethodField()
    detalles = DetalleCargaBarraSerializer(
        many=True,
        read_only=True,
    )

    class Meta:
        model = Pedido
        fields = [
            "id",
            "fecha_creacion",
            "autor_id",
            "autor_nombre",
            "cuenta",
            "mesa_identificacion",
            "responsable_id",
            "responsable_nombre",
            "detalles",
        ]
        read_only_fields = fields

    def get_autor_nombre(self, pedido):
        autor = pedido.creado_por
        if autor is None:
            return None
        return autor.get_full_name().strip() or autor.username

    def get_responsable_nombre(self, pedido):
        responsable = pedido.cuenta.mozo_responsable
        if responsable is None:
            return None
        return responsable.get_full_name().strip() or responsable.username


class AvisoRetiroSerializer(serializers.ModelSerializer):
    pedido = serializers.IntegerField(
        source="pedido_id",
        read_only=True,
    )

    class Meta:
        model = AvisoRetiro
        fields = [
            "id",
            "pedido",
            "mensaje",
            "fecha_creacion",
        ]
        read_only_fields = fields


class TransicionPreparacionSerializer(serializers.Serializer):
    estado = serializers.ChoiceField(
        choices=PreparacionPedidoSector.Estado.choices,
    )
