
from django.db import transaction
from rest_framework import serializers

from productos.models import Producto

from .models import Mesa, Cuenta, Pedido, DetallePedido


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


class DetallePedidoLecturaSerializer(serializers.ModelSerializer):
    producto_nombre = serializers.CharField(
        source="producto.nombre",
        read_only=True,
    )

    mesa_numero = serializers.IntegerField(
        source="pedido.mesa.numero",
        read_only=True,
    )

    pedido_fecha = serializers.DateTimeField(
        source="pedido.fecha_creacion",
        read_only=True,
    )

    class Meta:
        model = DetallePedido
        fields = [
            "id",
            "pedido",
            "mesa_numero",
            "pedido_fecha",
            "producto",
            "producto_nombre",
            "cantidad",
            "precio_unitario",
            "sector_destino",
        ]
        read_only_fields = fields
