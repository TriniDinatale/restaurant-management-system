
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
            cuenta = (
                Cuenta.objects.select_for_update(of=("self",))
                .select_related("mesa", "mozo_responsable")
                .get(pk=cuenta_enviada.pk)
            )
        except Cuenta.DoesNotExist as exc:
            raise serializers.ValidationError(
                {"cuenta": "La cuenta indicada ya no existe."}
            ) from exc

        if cuenta.estado != Cuenta.Estado.ABIERTA:
            raise serializers.ValidationError(
                {"cuenta": "No se pueden agregar pedidos a una cuenta cerrada."}
            )
        if cuenta.mesa_id != mesa_enviada.pk:
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
            mesa=mesa_enviada,
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
            "estados_sectores",
            "retiro_habilitado",
            "puede_habilitar_retiro",
        ]
        read_only_fields = fields

    def get_mozo_nombre(self, pedido):
        return pedido.mozo.get_full_name().strip() or pedido.mozo.username

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
