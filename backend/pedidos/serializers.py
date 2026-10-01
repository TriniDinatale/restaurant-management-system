
from django.db import IntegrityError, transaction
from rest_framework import serializers

from productos.models import Producto
from usuarios.models import Sector, User

from .models import (
    Cuenta,
    DetallePedido,
    Mesa,
    Pedido,
    PreparacionPedidoSector,
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
        ]

        read_only_fields = [
            "id",
            "mozo",
            "fecha_creacion",
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

        pedido = Pedido.objects.create(
            mozo=self.context["request"].user,
            **validated_data,
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

        return pedido


class CuentaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cuenta
        fields = [
            "id",
            "mesa",
            "estado",
            "fecha_apertura",
            "fecha_cierre",
        ]
        read_only_fields = [
            "id",
            "estado",
            "fecha_apertura",
            "fecha_cierre",
        ]

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
        try:
            with transaction.atomic():
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


class TransicionPreparacionSerializer(serializers.Serializer):
    estado = serializers.ChoiceField(
        choices=PreparacionPedidoSector.Estado.choices,
    )
