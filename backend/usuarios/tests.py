from django.contrib.auth import get_user_model
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from .models import Sector, User


class UserModelTests(TestCase):
    def test_custom_user_model_is_configured(self):
        User = get_user_model()

        self.assertEqual(User._meta.label, "usuarios.User")

    def test_password_is_hashed(self):
        User = get_user_model()

        user = User.objects.create_user(
            username="testuser",
            password="SecureTestPassword123!",
        )

        self.assertNotEqual(user.password, "SecureTestPassword123!")
        self.assertTrue(user.check_password("SecureTestPassword123!"))

    def test_waiter_can_belong_to_salon(self):
        User = get_user_model()
        salon = Sector.objects.create(nombre=Sector.Nombre.SALON)

        user = User(
            username="mozo1",
            rol=User.Rol.MOZO,
            sector=salon,
        )
        user.set_unusable_password()
        user.full_clean()


    def test_waiter_cannot_belong_to_pizza(self):
        User = get_user_model()
        pizza = Sector.objects.create(nombre=Sector.Nombre.PIZZA)

        user = User(
            username="mozo2",
            rol=User.Rol.MOZO,
            sector=pizza,
        )

        with self.assertRaises(ValidationError):
            user.full_clean()


    def test_bar_user_can_belong_to_bar(self):
        User = get_user_model()
        barra = Sector.objects.create(nombre=Sector.Nombre.BARRA)

        user = User(
            username="barra1",
            rol=User.Rol.BARRA,
            sector=barra,
        )
        user.set_unusable_password()
        user.full_clean()


    def test_bar_user_cannot_belong_to_salon(self):
        User = get_user_model()
        salon = Sector.objects.create(nombre=Sector.Nombre.SALON)

        user = User(
            username="barra2",
            rol=User.Rol.BARRA,
            sector=salon,
        )

        with self.assertRaises(ValidationError):
            user.full_clean()


    def test_kitchen_user_can_belong_to_pizza(self):
        User = get_user_model()
        pizza = Sector.objects.create(nombre=Sector.Nombre.PIZZA)

        user = User(
            username="cocina1",
            rol=User.Rol.COCINA,
            sector=pizza,
        )
        user.set_unusable_password()
        user.full_clean()


    def test_kitchen_user_can_belong_to_platos(self):
        User = get_user_model()
        platos = Sector.objects.create(nombre=Sector.Nombre.PLATOS)

        user = User(
            username="cocina2",
            rol=User.Rol.COCINA,
            sector=platos,
        )
        user.set_unusable_password()
        user.full_clean()


    def test_kitchen_user_cannot_belong_to_bar(self):
        User = get_user_model()
        barra = Sector.objects.create(nombre=Sector.Nombre.BARRA)

        user = User(
            username="cocina3",
            rol=User.Rol.COCINA,
            sector=barra,
        )

        with self.assertRaises(ValidationError):
            user.full_clean()


    def test_admin_can_exist_without_sector(self):
        User = get_user_model()

        user = User(
            username="admin1",
            rol=User.Rol.ADMINISTRADOR,
            sector=None,
        )
        user.set_unusable_password()
        user.full_clean()

class AdminOnlyEndpointTests(APITestCase):
    def setUp(self):
        self.salon = Sector.objects.create(
            nombre=Sector.Nombre.SALON
        )

        self.barra = Sector.objects.create(
            nombre=Sector.Nombre.BARRA
        )

        self.pizza = Sector.objects.create(
            nombre=Sector.Nombre.PIZZA
        )

        self.admin = User.objects.create_user(
            username="admin_test",
            password="test-password-123",
            rol=User.Rol.ADMINISTRADOR,
        )

        self.mozo = User.objects.create_user(
            username="mozo_test",
            password="test-password-123",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )

        self.usuario_barra = User.objects.create_user(
            username="barra_test",
            password="test-password-123",
            rol=User.Rol.BARRA,
            sector=self.barra,
        )

        self.cocina = User.objects.create_user(
            username="cocina_test",
            password="test-password-123",
            rol=User.Rol.COCINA,
            sector=self.pizza,
        )

        self.url = reverse("admin-only")

    def test_anonymous_user_cannot_access_admin_endpoint(self):
        response = self.client.get(self.url)

        self.assertIn(
            response.status_code,
            {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN},
        )

    def test_mozo_cannot_access_admin_endpoint(self):
        self.client.force_authenticate(user=self.mozo)

        response = self.client.get(self.url)

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_barra_cannot_access_admin_endpoint(self):
        self.client.force_authenticate(user=self.usuario_barra)

        response = self.client.get(self.url)

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_cocina_cannot_access_admin_endpoint(self):
        self.client.force_authenticate(user=self.cocina)

        response = self.client.get(self.url)

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_administrador_can_access_admin_endpoint(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.get(self.url)

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            response.data["username"],
            self.admin.username,
        )

class AuthenticationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mozo_auth_test",
            password="test-password-123",
            rol=User.Rol.MOZO,
            sector=Sector.objects.create(
                nombre=Sector.Nombre.SALON
            ),
        )

        self.login_url = reverse("login")
        self.logout_url = reverse("logout")
        self.me_url = reverse("me")

    def test_login_with_valid_credentials(self):
        response = self.client.post(
            self.login_url,
            {
                "username": "mozo_auth_test",
                "password": "test-password-123",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            response.data["user"]["username"],
            self.user.username,
        )

        self.assertEqual(
            response.data["user"]["rol"],
            User.Rol.MOZO,
        )

        self.assertEqual(
            response.data["user"]["sector"],
            Sector.Nombre.SALON,
        )

    def test_login_with_invalid_password_is_rejected(self):
        response = self.client.post(
            self.login_url,
            {
                "username": "mozo_auth_test",
                "password": "contraseña-incorrecta",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_me_requires_authentication(self):
        response = self.client.get(self.me_url)

        self.assertIn(
            response.status_code,
            {
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            },
        )

    def test_logged_in_user_can_access_me(self):
        login_response = self.client.post(
            self.login_url,
            {
                "username": "mozo_auth_test",
                "password": "test-password-123",
            },
            format="json",
        )

        self.assertEqual(
            login_response.status_code,
            status.HTTP_200_OK,
        )

        response = self.client.get(self.me_url)

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            response.data["username"],
            self.user.username,
        )

    def test_logout_destroys_session(self):
        login_response = self.client.post(
            self.login_url,
            {
                "username": "mozo_auth_test",
                "password": "test-password-123",
            },
            format="json",
        )

        self.assertEqual(
            login_response.status_code,
            status.HTTP_200_OK,
        )

        logout_response = self.client.post(
            self.logout_url,
            format="json",
        )

        self.assertEqual(
            logout_response.status_code,
            status.HTTP_200_OK,
        )

        response = self.client.get(self.me_url)

        self.assertIn(
            response.status_code,
            {
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            },
        )

class CSRFSecurityTests(APITestCase):
    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)

        self.user = User.objects.create_user(
            username="csrf_test",
            password="test-password-123",
            rol=User.Rol.ADMINISTRADOR,
        )

        self.csrf_url = reverse("csrf")
        self.login_url = reverse("login")

    def test_login_without_csrf_token_is_rejected(self):
        response = self.client.post(
            self.login_url,
            {
                "username": "csrf_test",
                "password": "test-password-123",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_login_with_valid_csrf_token_is_allowed(self):
        csrf_response = self.client.get(self.csrf_url)

        self.assertEqual(
            csrf_response.status_code,
            status.HTTP_200_OK,
        )

        csrf_token = csrf_response.data["csrfToken"]

        response = self.client.post(
            self.login_url,
            {
                "username": "csrf_test",
                "password": "test-password-123",
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )