
from rest_framework import generics

from usuarios.permissions import IsMozo

from .models import Pedido
from .serializers import PedidoSerializer, CuentaSerializer


class PedidoCreateView(generics.CreateAPIView):
    serializer_class = PedidoSerializer
    permission_classes = [IsMozo]
    queryset = Pedido.objects.all()


class CuentaCreateView(generics.CreateAPIView):
    serializer_class = CuentaSerializer
    permission_classes = [IsMozo]
