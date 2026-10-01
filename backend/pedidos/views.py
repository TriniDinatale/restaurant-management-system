
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import generics, serializers
from rest_framework.response import Response

from usuarios.permissions import IsMozo, IsPreparador

from .models import DetallePedido, Pedido
from .serializers import (
    CuentaSerializer,
    DetallePedidoLecturaSerializer,
    PedidoSerializer,
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
            DetallePedido.objects.filter(
                sector_destino_id=self.request.user.sector_id,
            )
            .select_related(
                "pedido",
                "pedido__mesa",
                "producto",
                "sector_destino",
            )
        )


class DetallePreparacionListView(
    PreparacionSectorMixin,
    generics.ListAPIView,
):
    serializer_class = DetallePedidoLecturaSerializer


class DetallePreparacionEstadoView(
    PreparacionSectorMixin,
    generics.GenericAPIView,
):
    serializer_class = TransicionPreparacionSerializer

    def patch(self, request, *args, **kwargs):
        detalle = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            detalle.transicionar_a(
                serializer.validated_data["estado_preparacion"],
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.message_dict if hasattr(exc, "message_dict") else exc.messages,
            ) from exc

        return Response(
            DetallePedidoLecturaSerializer(detalle).data,
        )
