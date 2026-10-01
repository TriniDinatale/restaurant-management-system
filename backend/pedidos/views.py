
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import generics, serializers
from rest_framework.response import Response

from usuarios.permissions import IsMozo, IsPreparador

from .models import Pedido, PreparacionPedidoSector
from .serializers import (
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
        preparacion = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            preparacion.transicionar_a(
                serializer.validated_data["estado"],
            )
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
