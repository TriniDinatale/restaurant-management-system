
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, serializers
from rest_framework.response import Response

from usuarios.models import Sector
from usuarios.permissions import IsBarra, IsMozo, IsPreparador

from .models import AvisoRetiro, Pedido, PreparacionPedidoSector
from .serializers import (
    AvisoRetiroSerializer,
    ControlRetiroBarraSerializer,
    CuentaSerializer,
    PedidoSerializer,
    PreparacionPedidoSectorSerializer,
    TransicionPreparacionSerializer,
)


class PedidoCreateView(generics.CreateAPIView):
    serializer_class = PedidoSerializer
    permission_classes = [IsMozo]
    queryset = Pedido.objects.all()


class CuentaCreateView(generics.CreateAPIView):
    serializer_class = CuentaSerializer
    permission_classes = [IsMozo]


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
