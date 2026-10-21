from django.shortcuts import redirect, render

from usuarios.models import Sector, User


def _is_barra_sector(user):
    sector_nombre = user.sector.nombre if user.sector_id else None
    return user.rol == User.Rol.BARRA and sector_nombre == Sector.Nombre.BARRA


def _can_review_cancellations(user):
    return _is_barra_sector(user)


def _role_home(user):
    if user.rol in {User.Rol.MOZO, User.Rol.BARRA}:
        return "web-mesas"
    if user.rol == User.Rol.ADMINISTRADOR:
        return "web-admin"
    return "web-pendiente"


def web_home(request):
    if request.user.is_authenticated:
        return redirect(_role_home(request.user))
    return render(request, "usuarios/login.html")


def web_mesas(request):
    if not request.user.is_authenticated:
        return redirect("web-home")
    if request.user.rol not in {User.Rol.MOZO, User.Rol.BARRA}:
        return redirect(_role_home(request.user))
    return render(
        request,
        "usuarios/mesas.html",
        {"puede_gestionar_turno": _is_barra_sector(request.user)},
    )


def web_pendiente(request):
    if not request.user.is_authenticated:
        return redirect("web-home")
    if request.user.rol != User.Rol.COCINA:
        return redirect(_role_home(request.user))
    return render(request, "usuarios/pendiente.html")


def web_admin(request):
    if not request.user.is_authenticated:
        return redirect("web-home")
    if request.user.rol != User.Rol.ADMINISTRADOR:
        return redirect(_role_home(request.user))
    return render(request, "usuarios/admin_panel.html")


def web_cancelaciones(request):
    if not request.user.is_authenticated:
        return redirect("web-home")
    if not _can_review_cancellations(request.user):
        return redirect(_role_home(request.user))
    return render(request, "usuarios/cancelaciones.html")
