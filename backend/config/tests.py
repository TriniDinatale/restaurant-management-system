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
                self.assertContains(response, "Actualizar datos")
                self.assertContains(response, "Cerrar sesión")
                self.assertRedirects(
                    self.client.get(reverse("web-pendiente")),
                    reverse("web-mesas"),
                )

    def test_administrator_and_kitchen_only_receive_pending_screen(self):
        for user in [self.admin, self.cocina]:
            with self.subTest(role=user.rol):
                self.client.force_login(user)
                self.assertRedirects(
                    self.client.get(reverse("web-home")),
                    reverse("web-pendiente"),
                )
                response = self.client.get(reverse("web-pendiente"))
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "usuarios/pendiente.html")
                self.assertContains(response, "pantalla operativa está pendiente")
                self.assertContains(response, "Cerrar sesión")
                self.assertRedirects(
                    self.client.get(reverse("web-mesas")),
                    reverse("web-pendiente"),
                )