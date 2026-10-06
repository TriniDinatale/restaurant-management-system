
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, serializers
from rest_framework.permissions import BasePermission, SAFE_METHODS
from rest_framework.response import Response

from usuarios.models import Sector, User
from usuarios.permissions import IsAdministrador, IsBarra, IsMozo, IsPreparador

from .models import (
    AvisoCargaBarra,
    AvisoRetiro,
    Cuenta,
    Mesa,
    Pedido,
    PreparacionPedidoSector,
)
from .serializers import (
    AvisoCargaBarraSerializer,
    AvisoRetiroSerializer,
    ControlRetiroBarraSerializer,
    CuentaSerializer,
    RegistroCargaBarraSerializer,
    MesaSerializer,
    PedidoSerializer,
    PreparacionPedidoSectorSerializer,
    TransicionPreparacionSerializer,
)


class IsMozoOrBarra(BasePermission):
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.rol in {User.Rol.MOZO, User.Rol.BARRA}
        )


class PedidoCreateView(generics.CreateAPIView):
    serializer_class = PedidoSerializer
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
