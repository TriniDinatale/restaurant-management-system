
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q, Sum, prefetch_related_objects
from django.db.models import Prefetch
from django.http import Http404
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
    CobroCuenta,
    Cuenta,
    DetallePedido,
    ImportePagoCuenta,
    LecturaNotificacionCancelacion,
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
    CobroCuentaCreateSerializer,
    CobroCuentaSerializer,
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
    SolicitudCancelacionNotificacionSerializer,
    SolicitudCancelacionSerializer,
)
from .services import (
    cantidad_disponible_cancelacion,
    detalles_vigentes_exist,
    detalles_vigentes_de_sector,
    estado_entrega,
    total_vigente_cuenta,
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


class CuentaCobroConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "cuenta_cobro_conflict"


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
            "pedidos__detalles__sector_destino",
            "pedidos__detalles__solicitudes_cancelacion__solicitante",
            "pedidos__detalles__solicitudes_cancelacion__resuelto_por",
            "pedidos__avisos_retiro",
        )
        return cuenta


class CobrarCuentaView(generics.GenericAPIView):
    permission_classes = [IsMozo]
    serializer_class = CobroCuentaCreateSerializer

    def obtener_existente(self, datos):
        cobro = (
            CobroCuenta.objects.select_related("cuenta", "mozo")
            .prefetch_related("importes")
            .filter(clave_idempotencia=datos["clave_idempotencia"])
            .first()
        )
        if cobro is None:
            return None
        if cobro.mozo_id != self.request.user.pk:
            self.permission_denied(self.request)
        importes_guardados = {
            importe.medio: importe.importe for importe in cobro.importes.all()
        }
        importes_enviados = {
            item["medio"]: item["importe"] for item in datos["importes"]
        }
        if (
            cobro.cuenta_id != self.kwargs["pk"]
            or cobro.total_esperado != datos["total_esperado"]
            or importes_guardados != importes_enviados
        ):
            raise IdempotencyKeyConflict()
        return Response(
            CobroCuentaSerializer(cobro).data,
            status=status.HTTP_200_OK,
        )

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        datos = serializer.validated_data

        existente = self.obtener_existente(datos)
        if existente is not None:
            return existente

        try:
            with transaction.atomic():
                mesa_id = (
                    Cuenta.objects.filter(pk=kwargs["pk"])
                    .values_list("mesa_id", flat=True)
                    .first()
                )
                if mesa_id is None:
                    raise Http404
                get_object_or_404(
                    Mesa.objects.select_for_update(),
                    pk=mesa_id,
                )
                existente = self.obtener_existente(datos)
                if existente is not None:
                    return existente
                cuenta = get_object_or_404(
                    Cuenta.objects.select_for_update(of=("self",)).select_related(
                        "mozo_responsable",
                    ),
                    pk=kwargs["pk"],
                    estado=Cuenta.Estado.ABIERTA,
                )
                if cuenta.mozo_responsable_id is None:
                    raise serializers.ValidationError(
                        {
                            "cuenta": (
                                "La cuenta histórica no tiene responsable; "
                                "debe resolverse administrativamente antes del cobro."
                            )
                        }
                    )
                if cuenta.mozo_responsable_id != request.user.pk:
                    self.permission_denied(request)

                existente = self.obtener_existente(datos)
                if existente is not None:
                    return existente

                if SolicitudCancelacionDetalle.objects.filter(
                    detalle__pedido__cuenta=cuenta,
                    estado=SolicitudCancelacionDetalle.Estado.PENDIENTE,
                ).exists():
                    raise CuentaCobroConflict(
                        "No se puede cerrar la cuenta mientras haya "
                        "solicitudes de cancelación pendientes."
                    )

                total_actual = total_vigente_cuenta(cuenta)
                if datos["total_esperado"] != total_actual:
                    raise CuentaCobroConflict(
                        "El total de la cuenta cambió. Revisá el nuevo total "
                        "antes de volver a cobrar."
                    )
                total_pagado = sum(
                    (item["importe"] for item in datos["importes"]),
                    start=Decimal("0.00"),
                )
                if total_pagado != total_actual:
                    raise serializers.ValidationError(
                        {
                            "importes": (
                                "La suma de los pagos debe coincidir exactamente "
                                "con el total vigente de la cuenta."
                            )
                        }
                    )

                cobro = CobroCuenta.objects.create(
                    cuenta=cuenta,
                    mozo=request.user,
                    total_cobrado=total_actual,
                    total_esperado=datos["total_esperado"],
                    clave_idempotencia=datos["clave_idempotencia"],
                )
                ImportePagoCuenta.objects.bulk_create(
                    [
                        ImportePagoCuenta(
                            cobro=cobro,
                            medio=item["medio"],
                            importe=item["importe"],
                        )
                        for item in datos["importes"]
                    ]
                )
                cuenta.cerrar()
                cobro = CobroCuenta.objects.select_related(
                    "cuenta",
                    "mozo",
                ).prefetch_related("importes").get(pk=cobro.pk)
                datos_respuesta = CobroCuentaSerializer(cobro).data
        except IntegrityError:
            existente = self.obtener_existente(datos)
            if existente is not None:
                return existente
            raise

        return Response(
            datos_respuesta,
            status=status.HTTP_201_CREATED,
        )


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
            mesa_id = (
                Cuenta.objects.filter(pk=kwargs["cuenta_id"])
                .values_list("mesa_id", flat=True)
                .first()
            )
            if mesa_id is None:
                raise Http404
            get_object_or_404(Mesa.objects.select_for_update(), pk=mesa_id)
            cuenta = get_object_or_404(
                Cuenta.objects.select_for_update(of=("self",)),
                pk=kwargs["cuenta_id"],
                estado=Cuenta.Estado.ABIERTA,
            )
            if cuenta.mozo_responsable_id != request.user.pk:
                self.permission_denied(request)

            pedido_id = (
                DetallePedido.objects.filter(
                    pk=kwargs["detalle_id"],
                    pedido__cuenta=cuenta,
                )
                .values_list("pedido_id", flat=True)
                .first()
            )
            if pedido_id is None:
                raise Http404
            pedido = get_object_or_404(
                Pedido.objects.select_for_update(of=("self",)),
                pk=pedido_id,
                cuenta=cuenta,
            )
            detalle = get_object_or_404(
                DetallePedido.objects.select_for_update(of=("self",))
                .select_related("pedido", "sector_destino"),
                pk=kwargs["detalle_id"],
                pedido=pedido,
            )
            existente = self.obtener_existente(datos)
            if existente is not None:
                return existente

            if estado_entrega(detalle) == "ENTREGADO":
                raise serializers.ValidationError(
                    {
                        "detail": (
                            "No se pueden solicitar cancelaciones de "
                            "productos entregados."
                        )
                    }
                )

            cantidad_disponible = cantidad_disponible_cancelacion(detalle)
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
            .order_by("fecha_solicitud", "pk")
        )


class NotificacionesCancelacionListView(generics.ListAPIView):
    permission_classes = [IsCancellationNotificationUser]
    serializer_class = SolicitudCancelacionNotificacionSerializer

    def get_queryset(self):
        leidas_por_usuario = LecturaNotificacionCancelacion.objects.filter(
            solicitud_id=OuterRef("pk"),
            usuario=self.request.user,
        )
        return _cancelaciones_visibles(self.request.user).annotate(
            leido=Exists(leidas_por_usuario),
        ).select_related(
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


def _cancelaciones_visibles(usuario):
    queryset = SolicitudCancelacionDetalle.objects.all()
    if usuario.rol == User.Rol.MOZO:
        return queryset.filter(solicitante=usuario)
    return queryset.filter(
        ~Q(estado=SolicitudCancelacionDetalle.Estado.PENDIENTE)
        | Q(
            estado=SolicitudCancelacionDetalle.Estado.PENDIENTE,
            detalle__pedido__cuenta__estado=Cuenta.Estado.ABIERTA,
        ),
    )


class MarcarNotificacionesCancelacionLeidasView(generics.GenericAPIView):
    permission_classes = [IsCancellationNotificationUser]

    def post(self, request, *args, **kwargs):
        solicitudes = list(
            _cancelaciones_visibles(request.user).values_list("pk", flat=True),
        )
        lecturas_existentes = set(
            LecturaNotificacionCancelacion.objects.filter(
                usuario=request.user,
                solicitud_id__in=solicitudes,
            ).values_list("solicitud_id", flat=True),
        )
        nuevas_lecturas = [
            LecturaNotificacionCancelacion(
                solicitud_id=solicitud_id,
                usuario=request.user,
            )
            for solicitud_id in solicitudes
            if solicitud_id not in lecturas_existentes
        ]
        LecturaNotificacionCancelacion.objects.bulk_create(
            nuevas_lecturas,
            ignore_conflicts=True,
        )
        return Response({"actualizadas": len(nuevas_lecturas)})


class ResolverSolicitudCancelacionView(generics.GenericAPIView):
    permission_classes = [IsCancellationReviewer]
    serializer_class = ResolverCancelacionSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        solicitud_id = kwargs["pk"]
        solicitud_referencia = get_object_or_404(
            SolicitudCancelacionDetalle.objects.select_related(
                "detalle__pedido__cuenta",
            ),
            pk=solicitud_id,
        )
        cuenta_id = solicitud_referencia.detalle.pedido.cuenta_id
        mesa_id = solicitud_referencia.detalle.pedido.cuenta.mesa_id

        with transaction.atomic():
            get_object_or_404(Mesa.objects.select_for_update(), pk=mesa_id)
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
            pedido_id = DetallePedido.objects.filter(
                pk=detalle_id,
                pedido__cuenta=cuenta,
            ).values_list("pedido_id", flat=True).first()
            if pedido_id is None:
                raise serializers.ValidationError(
                    {"detail": "La solicitud de cancelación no existe."},
                )
            pedido = get_object_or_404(
                Pedido.objects.select_for_update(of=("self",)),
                pk=pedido_id,
                cuenta=cuenta,
            )
            detalle = get_object_or_404(
                DetallePedido.objects.select_for_update(of=("self",)),
                pk=detalle_id,
                pedido=pedido,
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
            .annotate(
                tiene_detalles_vigentes=detalles_vigentes_exist(),
            )
            .filter(tiene_detalles_vigentes=True)
            .select_related(
                "pedido",
                "pedido__mesa",
                "sector",
            )
            .prefetch_related(
                Prefetch(
                    "pedido__detalles",
                    queryset=DetallePedido.objects.select_related(
                        "producto",
                        "sector_destino",
                    ).prefetch_related(
                        "solicitudes_cancelacion",
                    ).order_by("pk"),
                ),
                "pedido__preparaciones_sectoriales__sector",
            )
            .order_by("pedido__fecha_creacion", "pk")
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
                cuenta = Cuenta.objects.select_for_update(of=("self",)).get(
                    pk=preparacion_autorizada.pedido.cuenta_id,
                )
                pedido = Pedido.objects.select_for_update(of=("self",)).get(
                    pk=preparacion_autorizada.pedido_id,
                    cuenta=cuenta,
                )
                preparacion = PreparacionPedidoSector.objects.select_for_update().select_related(
                    "sector",
                ).get(
                    pk=preparacion_autorizada.pk,
                    pedido=pedido,
                )
                if not detalles_vigentes_de_sector(
                    pedido,
                    preparacion.sector_id,
                    lock=True,
                ):
                    raise DjangoValidationError(
                        "La preparación no contiene productos vigentes."
                    )
                preparacion.transicionar_a(serializer.validated_data["estado"])

                if (
                    preparacion.sector.nombre == Sector.Nombre.BARRA
                    and preparacion.estado == PreparacionPedidoSector.Estado.LISTO
                ):
                    pedido.crear_aviso_retiro(
                        AvisoRetiro.Origen.BARRA,
                        request.user,
                    )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.message_dict if hasattr(exc, "message_dict") else exc.messages,
            ) from exc

        prefetch_related_objects(
            [preparacion],
            "pedido__mesa",
            "pedido__detalles__producto",
            "pedido__detalles__sector_destino",
            "pedido__detalles__solicitudes_cancelacion",
            "pedido__preparaciones_sectoriales__sector",
        )
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
            Pedido.objects.annotate(
                tiene_productos_cocina_vigentes=detalles_vigentes_exist(
                    en_pedido="pk",
                    en_sector=None,
                    sectores={
                        Sector.Nombre.PIZZA,
                        Sector.Nombre.PLATOS,
                    },
                ),
            )
            .filter(tiene_productos_cocina_vigentes=True)
            .exclude(
                avisos_retiro__origen=AvisoRetiro.Origen.COCINA,
            )
            .select_related("mesa", "mozo")
            .prefetch_related(
                Prefetch(
                    "detalles",
                    queryset=DetallePedido.objects.select_related(
                        "producto",
                        "sector_destino",
                    ).prefetch_related(
                        "solicitudes_cancelacion",
                    ).order_by("pk"),
                ),
                "preparaciones_sectoriales__sector",
            )
            .order_by("fecha_creacion", "pk")
        )


class ConfirmarRetiroView(generics.GenericAPIView):
    permission_classes = [IsBarra]
    serializer_class = ControlRetiroBarraSerializer

    def post(self, request, *args, **kwargs):
        try:
            with transaction.atomic():
                pedido_referencia = get_object_or_404(
                    Pedido.objects.select_related("cuenta"),
                    pk=kwargs["pk"],
                )
                Cuenta.objects.select_for_update(of=("self",)).get(
                    pk=pedido_referencia.cuenta_id,
                )
                pedido = get_object_or_404(
                    Pedido.objects.select_for_update(of=("self",)).select_related(
                        "mesa",
                        "mozo",
                    ),
                    pk=kwargs["pk"],
                )
                pedido.crear_aviso_retiro(
                    AvisoRetiro.Origen.COCINA,
                    request.user,
                )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.message_dict if hasattr(exc, "message_dict") else exc.messages,
            ) from exc

        prefetch_related_objects(
            [pedido],
            "detalles__producto",
            "detalles__sector_destino",
            "detalles__solicitudes_cancelacion",
            "preparaciones_sectoriales__sector",
        )
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
        ).select_related("pedido").order_by("-fecha_creacion", "-pk")


class MarcarAvisoRetiroLeidoView(generics.GenericAPIView):
    permission_classes = [IsMozo]

    def post(self, request, *args, **kwargs):
        aviso = get_object_or_404(
            AvisoRetiro.objects.filter(
                destinatario=request.user,
            ),
            pk=kwargs["pk"],
        )
        actualizados = AvisoRetiro.objects.filter(
            pk=aviso.pk,
            destinatario=request.user,
            leido_en__isnull=True,
        ).update(leido_en=timezone.now())
        return Response({"actualizados": actualizados})


class MarcarAvisosRetiroLeidosView(generics.GenericAPIView):
    permission_classes = [IsMozo]

    def post(self, request, *args, **kwargs):
        actualizados = AvisoRetiro.objects.filter(
            destinatario=request.user,
            leido_en__isnull=True,
        ).update(leido_en=timezone.now())
        return Response({"actualizados": actualizados})
