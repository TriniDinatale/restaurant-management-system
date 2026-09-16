from django.contrib.auth import authenticate, login, logout
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .permissions import IsAdministrador
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect

class CSRFTokenView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(
            {
                "csrfToken": get_token(request),
            }
        )


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get("username")
        password = request.data.get("password")

        if not username or not password:
            return Response(
                {
                    "detail": (
                        "El nombre de usuario y la contraseña "
                        "son obligatorios."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = authenticate(
            request,
            username=username,
            password=password,
        )

        if user is None:
            return Response(
                {"detail": "Credenciales inválidas."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {"detail": "La cuenta está deshabilitada."},
                status=status.HTTP_403_FORBIDDEN,
            )

        login(request, user)

        return Response(
            {
                "message": "Inicio de sesión correcto.",
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "rol": user.rol,
                    "sector": (
                        user.sector.nombre
                        if user.sector
                        else None
                    ),
                },
            },
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)

        return Response(
            {"message": "Sesión cerrada correctamente."},
            status=status.HTTP_200_OK,
        )


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        return Response(
            {
                "id": user.id,
                "username": user.username,
                "rol": user.rol,
                "sector": (
                    user.sector.nombre
                    if user.sector
                    else None
                ),
            },
            status=status.HTTP_200_OK,
        )


class AdminOnlyView(APIView):
    permission_classes = [IsAdministrador]

    def get(self, request):
        return Response(
            {
                "message": "Acceso administrativo autorizado.",
                "username": request.user.username,
            },
            status=status.HTTP_200_OK,
        )