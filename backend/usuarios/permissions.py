from rest_framework.permissions import BasePermission

from .models import User


class IsAdministrador(BasePermission):
    message = "Solo un administrador puede realizar esta acción."

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.rol == User.Rol.ADMINISTRADOR
        )


class IsMozo(BasePermission):
    message = "Solo un mozo puede realizar esta acción."

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.rol == User.Rol.MOZO
        )


class IsBarra(BasePermission):
    message = "Solo un usuario de Barra puede realizar esta acción."

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.rol == User.Rol.BARRA
        )


class IsCocina(BasePermission):
    message = "Solo un usuario de Cocina puede realizar esta acción."

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.rol == User.Rol.COCINA
        )