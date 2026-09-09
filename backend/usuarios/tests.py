from django.contrib.auth import get_user_model
from django.test import TestCase


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