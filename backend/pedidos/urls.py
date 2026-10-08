
from django.urls import path

from .views import (
    PedidoCreateView,
    PedidoPorMesaCreateView,
    CuentaListCreateView,
    AvisosRetiroListView,
    ConfirmarRetiroView,
    ControlRetiroBarraListView,
    MesaListView,
    BarraPedidoCreateView,
    AvisosCargaBarraListView,
    RegistroCargaBarraListView,
    PreparacionPedidoSectorEstadoView,
    PreparacionPedidoSectorListView,
)

urlpatterns = [
    path(
        "",
        PedidoCreateView.as_view(),
        name="pedido-create",
    ),
    
    path("mesas/", MesaListView.as_view(), name="mesa-list"),
    path(
        "mesas/cargas/",
        PedidoPorMesaCreateView.as_view(),
        name="pedido-mesa-create",
    ),
    path(
        "barra/cargas/",
        BarraPedidoCreateView.as_view(),
        name="barra-carga-create",
    ),
    path(
        "barra/cargas/registro/",
        RegistroCargaBarraListView.as_view(),
        name="barra-carga-registro",
    ),
    path(
        "avisos/cargas/",
        AvisosCargaBarraListView.as_view(),
        name="aviso-carga-barra-list",
    ),
    path(
        "cuentas/",
        CuentaListCreateView.as_view(),
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
