
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum, prefetch_related_objects
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, serializers, status
from rest_framework.exceptions import APIException
from rest_framework.permissions import BasePermission, SAFE_METHODS
from rest_framework.response import Response

from usuarios.models import Sector, User
from usuarios.permissions import IsAdministrador, IsBarra, IsMozo, IsPreparador

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
from .serializers import (
    AvisoCargaBarraSerializer,
    AvisoRetiroSerializer,
    ControlRetiroBarraSerializer,
    CuentaConsumoSerializer,
    CuentaSerializer,
    RegistroCargaBarraSerializer,
    MesaSerializer,
    PedidoSerializer,
    PedidoPorMesaSerializer,
    PreparacionPedidoSectorSerializer,
    TransicionPreparacionSerializer,
    ResolverCancelacionSerializer,
    SolicitudCancelacionCreateSerializer,
    SolicitudCancelacionPendienteSerializer,
    SolicitudCancelacionSerializer,
)


class IsMozoOrBarra(BasePermission):
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.rol in {User.Rol.MOZO, User.Rol.BARRA}
        )


class IsCancellationNotificationUser(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated:
            return False
        if user.rol == User.Rol.MOZO:
            return True
        return (
            user.rol == User.Rol.BARRA
            and user.sector_id is not None
            and user.sector.nombre == Sector.Nombre.BARRA
        )


class IsCancellationReviewer(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return (
            user.is_authenticated
            and user.rol == User.Rol.BARRA
            and user.sector_id is not None
            and user.sector.nombre == Sector.Nombre.BARRA
        )


class IdempotencyKeyConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "La clave de idempotencia ya fue usada con otros datos."
    default_code = "idempotency_key_conflict"


class PedidoCreateView(generics.CreateAPIView):
    serializer_class = PedidoSerializer
    permission_classes = [IsMozo]
    queryset = Pedido.objects.all()

class PedidoPorMesaCreateView(generics.CreateAPIView):
    serializer_class = PedidoPorMesaSerializer
    permission_classes = [IsMozo]
    queryset = Pedido.objects.all()


class MesaListView(generics.ListAPIView):
    serializer_class = MesaSerializer
    permission_classes = [IsMozoOrBarra]
    queryset = Mesa.objects.all().order_by("zona", "numero")


class CuentaListCreateView(generics.ListCreateAPIView):
    serializer_class = CuentaSerializer

    def get_queryset(self):
        return (
            Cuenta.objects.filter(estado=Cuenta.Estado.ABIERTA)
            .select_related("mesa", "mozo_responsable")
        )

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsMozoOrBarra()]
        return [IsMozo()]


class CuentaConsumoView(generics.RetrieveAPIView):
    serializer_class = CuentaConsumoSerializer
    permission_classes = [IsMozoOrBarra]
    lookup_url_kwarg = "pk"

    def get_queryset(self):
        return (
            Cuenta.objects.filter(estado=Cuenta.Estado.ABIERTA)
            .select_related("mesa", "mozo_responsable")
        )

    def get_object(self):
        cuenta = super().get_object()
        usuario = self.request.user
        if (
            usuario.rol == User.Rol.MOZO
            and cuenta.mozo_responsable_id != usuario.pk
        ):
            self.permission_denied(self.request)
        prefetch_related_objects(
            [cuenta],
            "pedidos__creado_por",
            "pedidos__mozo",
            "pedidos__aviso_carga_barra",
            "pedidos__detalles__producto",
            "pedidos__detalles__solicitudes_cancelacion__solicitante",
            "pedidos__detalles__solicitudes_cancelacion__resuelto_por",
        )
        return cuenta


class SolicitudCancelacionCreateView(generics.GenericAPIView):
    permission_classes = [IsMozo]
    serializer_class = SolicitudCancelacionCreateSerializer

    def obtener_existente(self, datos):
        solicitud = (
            SolicitudCancelacionDetalle.objects.select_related(
                "solicitante",
                "detalle__pedido",
                "detalle__pedido__cuenta",
            )
            .filter(
                clave_idempotencia=datos["clave_idempotencia"],
            )
            .first()
        )
        if solicitud is None:
            return None

        mismo_pedido = (
            solicitud.solicitante_id == self.request.user.pk
            and solicitud.detalle.pedido.cuenta.mozo_responsable_id
            == self.request.user.pk
            and solicitud.detalle.pedido.cuenta_id
            == self.kwargs["cuenta_id"]
            and solicitud.detalle_id == self.kwargs["detalle_id"]
            and solicitud.cantidad == datos["cantidad"]
            and solicitud.motivo == datos["motivo"]
        )
        if not mismo_pedido:
            raise IdempotencyKeyConflict()
        return Response(
            SolicitudCancelacionSerializer(
                solicitud,
                context=self.get_serializer_context(),
            ).data,
            status=status.HTTP_200_OK,
        )

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        datos = serializer.validated_data

        existente = self.obtener_existente(datos)
        if existente is not None:
            return existente

        with transaction.atomic():
            cuenta = get_object_or_404(
                Cuenta.objects.select_for_update(of=("self",)),
                pk=kwargs["cuenta_id"],
                estado=Cuenta.Estado.ABIERTA,
            )
            if cuenta.mozo_responsable_id != request.user.pk:
                self.permission_denied(request)

            detalle = get_object_or_404(
                DetallePedido.objects.select_for_update(of=("self",)),
                pk=kwargs["detalle_id"],
                pedido__cuenta=cuenta,
            )
            existente = self.obtener_existente(datos)
            if existente is not None:
                return existente

            reservadas = detalle.solicitudes_cancelacion.filter(
                estado__in=[
                    SolicitudCancelacionDetalle.Estado.PENDIENTE,
                    SolicitudCancelacionDetalle.Estado.ACEPTADA,
                ],
            ).aggregate(total=Sum("cantidad"))["total"] or 0
            cantidad_disponible = detalle.cantidad - reservadas
            if serializer.validated_data["cantidad"] > cantidad_disponible:
                raise serializers.ValidationError(
                    {
                        "cantidad": (
                            "La cantidad supera las unidades disponibles "
                            "para cancelar."
                        )
                    }
                )
            try:
                with transaction.atomic():
                    solicitud = SolicitudCancelacionDetalle.objects.create(
                        detalle=detalle,
                        solicitante=request.user,
                        clave_idempotencia=datos["clave_idempotencia"],
                        cantidad=datos["cantidad"],
                        motivo=datos["motivo"],
                    )
            except IntegrityError:
                existente = self.obtener_existente(datos)
                if existente is not None:
                    return existente
                raise

        return Response(
            SolicitudCancelacionSerializer(
                solicitud,
                context=self.get_serializer_context(),
            ).data,
            status=status.HTTP_201_CREATED,
        )


class SolicitudesCancelacionPendientesView(generics.ListAPIView):
    permission_classes = [IsCancellationReviewer]
    serializer_class = SolicitudCancelacionPendienteSerializer

    def get_queryset(self):
        return (
            SolicitudCancelacionDetalle.objects.filter(
                estado=SolicitudCancelacionDetalle.Estado.PENDIENTE,
                detalle__pedido__cuenta__estado=Cuenta.Estado.ABIERTA,
            )
            .select_related(
                "solicitante",
                "detalle",
                "detalle__producto",
                "detalle__pedido",
                "detalle__pedido__cuenta",
                "detalle__pedido__mesa",
            )
            .prefetch_related(
                "detalle__solicitudes_cancelacion",
            )
        )


class NotificacionesCancelacionListView(generics.ListAPIView):
    permission_classes = [IsCancellationNotificationUser]
    serializer_class = SolicitudCancelacionPendienteSerializer

    def get_queryset(self):
        queryset = SolicitudCancelacionDetalle.objects.all()
        usuario = self.request.user
        if usuario.rol == User.Rol.MOZO:
            queryset = queryset.filter(solicitante=usuario)
        else:
            queryset = queryset.filter(
                ~Q(estado=SolicitudCancelacionDetalle.Estado.PENDIENTE)
                | Q(
                    estado=SolicitudCancelacionDetalle.Estado.PENDIENTE,
                    detalle__pedido__cuenta__estado=Cuenta.Estado.ABIERTA,
                ),
            )
        return queryset.select_related(
            "solicitante",
            "resuelto_por",
            "detalle",
            "detalle__producto",
            "detalle__pedido",
            "detalle__pedido__cuenta",
            "detalle__pedido__mesa",
        ).prefetch_related(
            "detalle__solicitudes_cancelacion",
        ).order_by("-fecha_solicitud", "-pk")


class ResolverSolicitudCancelacionView(generics.GenericAPIView):
    permission_classes = [IsCancellationReviewer]
    serializer_class = ResolverCancelacionSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        solicitud_id = kwargs["pk"]
        solicitud_referencia = get_object_or_404(
            SolicitudCancelacionDetalle.objects.select_related(
                "detalle__pedido",
            ),
            pk=solicitud_id,
        )
        cuenta_id = solicitud_referencia.detalle.pedido.cuenta_id

        with transaction.atomic():
            cuenta = get_object_or_404(
                Cuenta.objects.select_for_update(of=("self",)),
                pk=cuenta_id,
                estado=Cuenta.Estado.ABIERTA,
            )
            detalle_id = (
                SolicitudCancelacionDetalle.objects.filter(
                    pk=solicitud_id,
                    detalle__pedido__cuenta=cuenta,
                )
                .values_list("detalle_id", flat=True)
                .first()
            )
            if detalle_id is None:
                raise serializers.ValidationError(
                    {"detail": "La solicitud de cancelación no existe."},
                )
            detalle = get_object_or_404(
                DetallePedido.objects.select_for_update(of=("self",)),
                pk=detalle_id,
                pedido__cuenta=cuenta,
            )
            solicitud = get_object_or_404(
                SolicitudCancelacionDetalle.objects.select_for_update(
                    of=("self",),
                ).select_related(
                    "solicitante",
                    "resuelto_por",
                ),
                pk=solicitud_id,
                detalle=detalle,
                estado=SolicitudCancelacionDetalle.Estado.PENDIENTE,
            )
            if (
                serializer.validated_data["estado"]
                == SolicitudCancelacionDetalle.Estado.ACEPTADA
            ):
                cantidad_ya_cancelada = detalle.solicitudes_cancelacion.filter(
                    estado=SolicitudCancelacionDetalle.Estado.ACEPTADA,
                ).aggregate(total=Sum("cantidad"))["total"] or 0
                if cantidad_ya_cancelada + solicitud.cantidad > detalle.cantidad:
                    raise serializers.ValidationError(
                        {
                            "detail": (
                                "La cantidad solicitada ya no está disponible "
                                "para cancelar."
                            )
                        }
                    )
            solicitud.estado = serializer.validated_data["estado"]
            solicitud.resuelto_por = request.user
            solicitud.fecha_resolucion = timezone.now()
            solicitud.save(
                update_fields=[
                    "estado",
                    "resuelto_por",
                    "fecha_resolucion",
                ],
            )

        return Response(
            SolicitudCancelacionSerializer(
                solicitud,
                context=self.get_serializer_context(),
            ).data,
        )


class BarraPedidoCreateView(generics.CreateAPIView):
    serializer_class = PedidoSerializer
    permission_classes = [IsBarra]


class AvisosCargaBarraListView(generics.ListAPIView):
    serializer_class = AvisoCargaBarraSerializer
    permission_classes = [IsMozo]

    def get_queryset(self):
        return (
            AvisoCargaBarra.objects.filter(
                destinatario=self.request.user,
            )
            .select_related("pedido", "pedido__mesa")
            .prefetch_related(
                "pedido__detalles__producto",
                "pedido__detalles__sector_destino",
            )
        )


class RegistroCargaBarraListView(generics.ListAPIView):
    serializer_class = RegistroCargaBarraSerializer
    permission_classes = [IsAdministrador]

    def get_queryset(self):
        return (
            Pedido.objects.filter(aviso_carga_barra__isnull=False)
            .select_related(
                "creado_por",
                "cuenta",
                "cuenta__mozo_responsable",
                "mesa",
            )
            .prefetch_related(
                "detalles__producto",
                "detalles__sector_destino",
            )
        )


class PreparacionSectorMixin:
    permission_classes = [IsPreparador]

    def get_queryset(self):
        return (
            PreparacionPedidoSector.objects.filter(
                sector_id=self.request.user.sector_id,
            )
            .select_related(
                "pedido",
                "pedido__mesa",
                "sector",
            )
        )


class PreparacionPedidoSectorListView(
    PreparacionSectorMixin,
    generics.ListAPIView,
):
    serializer_class = PreparacionPedidoSectorSerializer


class PreparacionPedidoSectorEstadoView(
    PreparacionSectorMixin,
    generics.GenericAPIView,
):
    serializer_class = TransicionPreparacionSerializer

    def patch(self, request, *args, **kwargs):
        preparacion_autorizada = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                pedido = Pedido.objects.select_for_update().get(
                    pk=preparacion_autorizada.pedido_id,
                )
                preparacion = PreparacionPedidoSector.objects.select_for_update().select_related(
                    "sector",
                ).get(
                    pk=preparacion_autorizada.pk,
                    pedido=pedido,
                )
                preparacion.transicionar_a(serializer.validated_data["estado"])

                preparaciones = list(
                    pedido.preparaciones_sectoriales.select_related("sector")
                )
                if (
                    len(preparaciones) == 1
                    and preparaciones[0].sector.nombre == Sector.Nombre.BARRA
                    and preparaciones[0].estado
                    == PreparacionPedidoSector.Estado.LISTO
                ):
                    pedido.habilitar_retiro(request.user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.message_dict if hasattr(exc, "message_dict") else exc.messages,
            ) from exc

        return Response(
            PreparacionPedidoSectorSerializer(
                preparacion,
                context=self.get_serializer_context(),
            ).data,
        )


class ControlRetiroBarraListView(generics.ListAPIView):
    permission_classes = [IsBarra]
    serializer_class = ControlRetiroBarraSerializer

    def get_queryset(self):
        return (
            Pedido.objects.filter(
                preparaciones_sectoriales__isnull=False,
            )
            .distinct()
            .select_related("mesa", "mozo")
            .prefetch_related("preparaciones_sectoriales__sector")
        )


class ConfirmarRetiroView(generics.GenericAPIView):
    permission_classes = [IsBarra]
    serializer_class = ControlRetiroBarraSerializer

    def post(self, request, *args, **kwargs):
        try:
            with transaction.atomic():
                pedido = get_object_or_404(
                    Pedido.objects.select_for_update().select_related(
                        "mesa",
                        "mozo",
                    ),
                    pk=kwargs["pk"],
                )
                pedido.habilitar_retiro(request.user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.message_dict if hasattr(exc, "message_dict") else exc.messages,
            ) from exc

        return Response(
            ControlRetiroBarraSerializer(
                pedido,
                context=self.get_serializer_context(),
            ).data,
        )


class AvisosRetiroListView(generics.ListAPIView):
    permission_classes = [IsMozo]
    serializer_class = AvisoRetiroSerializer

    def get_queryset(self):
        return AvisoRetiro.objects.filter(
            destinatario=self.request.user,
        ).select_related("pedido")
