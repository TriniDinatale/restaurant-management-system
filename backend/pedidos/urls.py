
from django.urls import path

from .views import (
    PedidoCreateView,
    CuentaCreateView,
    AvisosRetiroListView,
    ConfirmarRetiroView,
    ControlRetiroBarraListView,
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
    path(
        "barra/retiros/",
        ControlRetiroBarraListView.as_view(),
        name="barra-retiros-list",
    ),
    path(
        "barra/retiros/<int:pk>/confirmar/",
        ConfirmarRetiroView.as_view(),
        name="barra-retiro-confirmar",
    ),
    path(
        "avisos/",
        AvisosRetiroListView.as_view(),
        name="aviso-retiro-list",
    ),
]
