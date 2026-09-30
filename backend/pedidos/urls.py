
from django.urls import path

from .views import (
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
]
