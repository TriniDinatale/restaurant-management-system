from rest_framework import generics
from rest_framework.permissions import (
    IsAuthenticated,
    SAFE_METHODS,
)

from usuarios.permissions import IsAdministrador

from .models import Categoria, Producto
from .serializers import CategoriaSerializer, ProductoSerializer
from usuarios.models import User

class LecturaOAdministrador(IsAuthenticated):
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False

        if request.method in SAFE_METHODS:
            return True

        return IsAdministrador().has_permission(request, view)


class CategoriaListView(generics.ListCreateAPIView):
    serializer_class = CategoriaSerializer
    permission_classes = [LecturaOAdministrador]

    def get_queryset(self):
        queryset = Categoria.objects.all()

        if (
            self.request.user.rol == User.Rol.ADMINISTRADOR
            and self.request.query_params.get("todos") == "true"
        ):
            return queryset

        return queryset.filter(activo=True)


class CategoriaDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = CategoriaSerializer
    permission_classes = [LecturaOAdministrador]

    http_method_names = [
        "get",
        "patch",
        "head",
        "options",
    ]

    def get_queryset(self):
        queryset = Categoria.objects.all()

        if self.request.user.rol == User.Rol.ADMINISTRADOR:
            return queryset

        return queryset.filter(activo=True)



class ProductoListView(generics.ListCreateAPIView):
    serializer_class = ProductoSerializer
    permission_classes = [LecturaOAdministrador]

    def get_queryset(self):
        queryset = Producto.objects.select_related(
            "categoria",
            "sector_destino",
        )

        if self.request.method in SAFE_METHODS:
            if (
                self.request.user.rol == User.Rol.ADMINISTRADOR
                and self.request.query_params.get("todos") == "true"
            ):
                return queryset

            return queryset.filter(
                activo=True,
                categoria__activo=True,
            )

        return queryset



class ProductoDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = ProductoSerializer
    permission_classes = [LecturaOAdministrador]
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        queryset = Producto.objects.select_related(
            "categoria",
            "sector_destino",
        )

        if self.request.method in SAFE_METHODS:
            if self.request.user.rol == User.Rol.ADMINISTRADOR:
                return queryset

            return queryset.filter(
                activo=True,
                categoria__activo=True,
            )

        return queryset