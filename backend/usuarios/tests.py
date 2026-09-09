from django.contrib.auth import get_user_model
from django.test import TestCase
from django.core.exceptions import ValidationError

from .models import Sector


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