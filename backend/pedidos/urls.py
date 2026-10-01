
from django.urls import path

from .views import (
    DetallePreparacionEstadoView,
    DetallePreparacionListView,
    PedidoCreateView,
    CuentaCreateView,
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
        DetallePreparacionListView.as_view(),
        name="detalle-preparacion-list",
    ),
    path(
        "preparacion/<int:pk>/estado/",
        DetallePreparacionEstadoView.as_view(),
        name="detalle-preparacion-estado",
    ),
]
