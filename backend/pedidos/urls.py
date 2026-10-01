
from django.urls import path

from .views import (
    PedidoCreateView,
    CuentaCreateView,
    PreparacionPedidoSectorEstadoView,
    PreparacionPedidoSectorListView,
)

urlpatterns = [
    path(
        "",
        PedidoCreateView.as_view(),
        name="pedido-create",
    ),
    
    path(
        "cuentas/",
        CuentaCreateView.as_view(),
        name="cuenta-create",
    ),
    path(
        "preparacion/",
        PreparacionPedidoSectorListView.as_view(),
        name="detalle-preparacion-list",
    ),
    path(
        "preparacion/<int:pk>/estado/",
        PreparacionPedidoSectorEstadoView.as_view(),
        name="detalle-preparacion-estado",
    ),
]
