from rest_framework.permissions import BasePermission

from .models import Sector, User


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


class IsPreparador(BasePermission):
    message = "Solo un trabajador de preparación con sector operativo puede realizar esta acción."

    def has_permission(self, request, view):
        user = request.user

        if not user.is_authenticated or not user.sector_id:
            return False

        if user.rol == User.Rol.BARRA:
            return user.sector.nombre == Sector.Nombre.BARRA

        if user.rol == User.Rol.COCINA:
            return user.sector.nombre in {
                Sector.Nombre.PIZZA,
                Sector.Nombre.PLATOS,
            }

        return False
