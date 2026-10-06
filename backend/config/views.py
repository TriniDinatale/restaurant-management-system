from django.shortcuts import redirect, render

from usuarios.models import User


def _role_home(user):
    if user.rol in {User.Rol.MOZO, User.Rol.BARRA}:
        return "web-mesas"
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
    return render(request, "usuarios/mesas.html")


def web_pendiente(request):
    if not request.user.is_authenticated:
        return redirect("web-home")
    if request.user.rol in {User.Rol.MOZO, User.Rol.BARRA}:
        return redirect("web-mesas")
    return render(request, "usuarios/pendiente.html")
