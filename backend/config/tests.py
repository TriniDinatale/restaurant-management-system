from django.test import TestCase
from django.urls import reverse

from usuarios.models import Sector, User


class HealthCheckTests(TestCase):
    def test_health_check_returns_ok(self):
        response = self.client.get(reverse("health-check"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


class WebInterfaceAccessTests(TestCase):
    def setUp(self):
        self.salon = Sector.objects.create(nombre=Sector.Nombre.SALON)
        self.barra = Sector.objects.create(nombre=Sector.Nombre.BARRA)
        self.pizza = Sector.objects.create(nombre=Sector.Nombre.PIZZA)
        self.mozo = User.objects.create_user(
            username="web_mozo",
            password="test-password",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )
        self.barra_user = User.objects.create_user(
            username="web_barra",
            password="test-password",
            rol=User.Rol.BARRA,
            sector=self.barra,
        )
        self.admin = User.objects.create_user(
            username="web_admin",
            password="test-password",
            rol=User.Rol.ADMINISTRADOR,
        )
        self.cocina = User.objects.create_user(
            username="web_cocina",
            password="test-password",
            rol=User.Rol.COCINA,
            sector=self.pizza,
        )

    def test_anonymous_user_sees_login_and_is_redirected_from_operational_pages(
        self,
    ):
        home = self.client.get(reverse("web-home"))
        self.assertEqual(home.status_code, 200)
        self.assertTemplateUsed(home, "usuarios/login.html")
        self.assertContains(home, 'name="username"')
        self.assertContains(home, 'name="password"')
        self.assertContains(home, "Ingresar")

        for url in [reverse("web-mesas"), reverse("web-pendiente")]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(response, reverse("web-home"))

    def test_mozo_and_barra_are_routed_to_tables(self):
        for user in [self.mozo, self.barra_user]:
            with self.subTest(role=user.rol):
                self.client.force_login(user)
                self.assertRedirects(
                    self.client.get(reverse("web-home")),
                    reverse("web-mesas"),
                )
                response = self.client.get(reverse("web-mesas"))
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "usuarios/mesas.html")
                self.assertContains(response, 'id="notification-toggle"')
                self.assertContains(response, "<h1>Mesas</h1>")
                self.assertContains(response, 'id="loading-message"')
                self.assertContains(response, 'id="zones"')
                self.assertContains(response, 'id="account-overlay"')
                if user.rol == User.Rol.BARRA:
                    self.assertContains(response, 'id="bar-retirements"')
                    self.assertContains(response, 'id="bar-preparations"')
                    self.assertContains(response, "Preparación de Barra")
                    self.assertContains(
                        response,
                        "Confirmá que recibiste y revisaste los productos",
                    )
                    cancellation_page = self.client.get(
                        reverse("web-cancelaciones")
                    )
                    self.assertEqual(cancellation_page.status_code, 200)
                    self.assertTemplateUsed(
                        cancellation_page,
                        "usuarios/cancelaciones.html",
                    )
                else:
                    self.assertRegex(
                        response.content.decode(),
                        r'<section(?=[^>]*id="bar-retirements")'
                        r'(?=[^>]*\shidden)[^>]*>',
                    )
                    self.assertRedirects(
                        self.client.get(reverse("web-cancelaciones")),
                        reverse("web-mesas"),
                    )
                self.assertContains(response, "Cerrar sesión")
                self.assertRedirects(
                    self.client.get(reverse("web-pendiente")),
                    reverse("web-mesas"),
                )

    def test_administrator_is_routed_to_admin_panel(self):
        self.client.force_login(self.admin)
        self.assertRedirects(
            self.client.get(reverse("web-home")),
            reverse("web-admin"),
        )
        response = self.client.get(reverse("web-admin"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "usuarios/admin_panel.html")
        self.assertContains(response, "Panel de administración")
        self.assertContains(response, 'id="user-label"')
        self.assertContains(response, 'id="logout-button"')
        for section in [
            "Ventas y cobros",
            "Ventas por mozo",
            "Caja y turnos",
            "Productos y categorías",
            "Registro de operaciones de barra",
        ]:
            self.assertContains(response, section)
        self.assertContains(response, "Pendiente", count=5)
        for url in [reverse("web-mesas"), reverse("web-pendiente")]:
            with self.subTest(url=url):
                self.assertRedirects(
                    self.client.get(url),
                    reverse("web-admin"),
                )

    def test_other_roles_cannot_access_admin_panel(self):
        expected = {
            self.mozo: reverse("web-mesas"),
            self.barra_user: reverse("web-mesas"),
            self.cocina: reverse("web-pendiente"),
        }
        for user, destination in expected.items():
            with self.subTest(role=user.rol):
                self.client.force_login(user)
                self.assertRedirects(
                    self.client.get(reverse("web-admin")),
                    destination,
                )

    def test_anonymous_user_cannot_access_admin_panel(self):
        self.assertRedirects(
            self.client.get(reverse("web-admin")),
            reverse("web-home"),
        )

    def test_kitchen_cannot_access_cancellation_screen(self):
        self.client.force_login(self.cocina)
        self.assertRedirects(
            self.client.get(reverse("web-home")),
            reverse("web-pendiente"),
        )
        pending = self.client.get(reverse("web-pendiente"))
        self.assertEqual(pending.status_code, 200)
        self.assertTemplateUsed(pending, "usuarios/pendiente.html")
        self.assertRedirects(
            self.client.get(reverse("web-mesas")),
            reverse("web-pendiente"),
        )
        self.assertRedirects(
            self.client.get(reverse("web-cancelaciones")),
            reverse("web-pendiente"),
        )