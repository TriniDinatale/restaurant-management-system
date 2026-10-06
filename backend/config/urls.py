from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from .views import web_home, web_mesas, web_pendiente


def health_check(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("", web_home, name="web-home"),
    path("mesas/", web_mesas, name="web-mesas"),
    path("pendiente/", web_pendiente, name="web-pendiente"),
    path("admin/", admin.site.urls),
    path("health/", health_check, name="health-check"),
    path("api/usuarios/", include("usuarios.urls")),
    path("api/productos/", include("productos.urls")),
    path("api/pedidos/", include("pedidos.urls")),
]