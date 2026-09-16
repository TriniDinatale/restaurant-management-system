from django.urls import path

from .views import (
    AdminOnlyView,
    CSRFTokenView,
    LoginView,
    LogoutView,
    MeView,
)


urlpatterns = [
    path("csrf/", CSRFTokenView.as_view(), name="csrf"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", MeView.as_view(), name="me"),
    path(
        "admin-only/",
        AdminOnlyView.as_view(),
        name="admin-only",
    ),
]