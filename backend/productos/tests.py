from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Categoria, Producto
from usuarios.models import Sector, User


class ProductoModelTests(TestCase):
    def setUp(self):
        self.categoria = Categoria.objects.create(
            nombre="Pizzas",
        )

        self.sector_salon = Sector.objects.create(
            nombre=Sector.Nombre.SALON,
        )
        self.sector_barra = Sector.objects.create(
            nombre=Sector.Nombre.BARRA,
        )
        self.sector_pizza = Sector.objects.create(
            nombre=Sector.Nombre.PIZZA,
        )
        self.sector_platos = Sector.objects.create(
            nombre=Sector.Nombre.PLATOS,
        )

    def crear_producto(self, sector):
        return Producto(
            nombre="Producto de prueba",
            precio="1000.00",
            categoria=self.categoria,
            sector_destino=sector,
        )

    def test_producto_no_puede_tener_salon_como_sector_destino(self):
        producto = self.crear_producto(self.sector_salon)

        with self.assertRaises(ValidationError):
            producto.full_clean()

    def test_producto_puede_tener_barra_como_sector_destino(self):
        producto = self.crear_producto(self.sector_barra)

        producto.full_clean()

    def test_producto_puede_tener_pizza_como_sector_destino(self):
        producto = self.crear_producto(self.sector_pizza)

        producto.full_clean()

    def test_producto_puede_tener_platos_como_sector_destino(self):
        producto = self.crear_producto(self.sector_platos)

        producto.full_clean()
        
    def test_producto_no_puede_tener_precio_negativo(self):
        producto = Producto(
            nombre="Pizza especial",
            precio="-100.00",
            categoria=self.categoria,
            sector_destino=self.sector_pizza,
        )

        with self.assertRaises(ValidationError):
            producto.full_clean()

    def test_no_puede_haber_producto_duplicado_en_misma_categoria(self):
        Producto.objects.create(
            nombre="Pizza muzzarella",
            precio="10000.00",
            categoria=self.categoria,
            sector_destino=self.sector_pizza,
        )

        producto_duplicado = Producto(
            nombre="Pizza muzzarella",
            precio="12000.00",
            categoria=self.categoria,
            sector_destino=self.sector_pizza,
        )

        with self.assertRaises(ValidationError):
            producto_duplicado.full_clean()

class ProductoAPITests(APITestCase):
    def setUp(self):
        self.salon = Sector.objects.create(
            nombre=Sector.Nombre.SALON,
        )
        self.barra = Sector.objects.create(
            nombre=Sector.Nombre.BARRA,
        )
        self.pizza = Sector.objects.create(
            nombre=Sector.Nombre.PIZZA,
        )

        self.mozo = User.objects.create_user(
            username="mozo_productos",
            password="test-password-123",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )

        self.categoria_activa = Categoria.objects.create(
            nombre="Pizzas",
            activo=True,
        )

        self.categoria_inactiva = Categoria.objects.create(
            nombre="Promociones antiguas",
            activo=False,
        )

        self.producto_activo = Producto.objects.create(
            nombre="Pizza muzzarella",
            precio="10000.00",
            categoria=self.categoria_activa,
            sector_destino=self.pizza,
            activo=True,
        )

        self.producto_inactivo = Producto.objects.create(
            nombre="Pizza eliminada",
            precio="9000.00",
            categoria=self.categoria_activa,
            sector_destino=self.pizza,
            activo=False,
        )

        self.producto_categoria_inactiva = Producto.objects.create(
            nombre="Promocion vieja",
            precio="8000.00",
            categoria=self.categoria_inactiva,
            sector_destino=self.barra,
            activo=True,
        )

        self.list_url = reverse("producto-list")

    def test_product_list_requires_authentication(self):
        response = self.client.get(self.list_url)

        self.assertIn(
            response.status_code,
            {
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            },
        )

    def test_authenticated_user_can_list_products(self):
        self.client.force_authenticate(user=self.mozo)

        response = self.client.get(self.list_url)

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            response.data[0]["nombre"],
            self.producto_activo.nombre,
        )

    def test_inactive_products_are_not_listed(self):
        self.client.force_authenticate(user=self.mozo)

        response = self.client.get(self.list_url)

        ids = [producto["id"] for producto in response.data]

        self.assertNotIn(
            self.producto_inactivo.id,
            ids,
        )

    def test_products_from_inactive_categories_are_not_listed(self):
        self.client.force_authenticate(user=self.mozo)

        response = self.client.get(self.list_url)

        ids = [producto["id"] for producto in response.data]

        self.assertNotIn(
            self.producto_categoria_inactiva.id,
            ids,
        )

    def test_products_with_inactive_destination_sector_are_not_listed(self):
        producto_sector_inactivo = Producto.objects.create(
            nombre="Pizza de sector inactivo",
            precio="11000.00",
            categoria=self.categoria_activa,
            sector_destino=self.barra,
            activo=True,
        )
        self.barra.activo = False
        self.barra.save(update_fields=["activo"])
        self.client.force_authenticate(user=self.mozo)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [producto["id"] for producto in response.data]
        self.assertIn(self.producto_activo.pk, ids)
        self.assertNotIn(producto_sector_inactivo.pk, ids)

    def test_authenticated_user_can_retrieve_product(self):
        self.client.force_authenticate(user=self.mozo)

        url = reverse(
            "producto-detail",
            kwargs={"pk": self.producto_activo.pk},
        )

        response = self.client.get(url)

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            response.data["nombre"],
            self.producto_activo.nombre,
        )
        self.assertEqual(
            response.data["categoria_nombre"],
            self.categoria_activa.nombre,
        )
        self.assertEqual(
            response.data["sector_destino_nombre"],
            self.pizza.nombre,
        )

    def test_inactive_product_detail_returns_404(self):
        self.client.force_authenticate(user=self.mozo)

        url = reverse(
            "producto-detail",
            kwargs={"pk": self.producto_inactivo.pk},
        )

        response = self.client.get(url)

        self.assertEqual(
            response.status_code,
            status.HTTP_404_NOT_FOUND,
        )
        

class ProductoAdministracionAPITests(APITestCase):
    def setUp(self):
        self.salon = Sector.objects.create(
            nombre=Sector.Nombre.SALON,
        )
        self.barra = Sector.objects.create(
            nombre=Sector.Nombre.BARRA,
        )
        self.pizza = Sector.objects.create(
            nombre=Sector.Nombre.PIZZA,
        )

        self.admin = User.objects.create_user(
            username="admin_productos",
            password="test-password-123",
            rol=User.Rol.ADMINISTRADOR,
        )

        self.mozo = User.objects.create_user(
            username="mozo_productos_admin",
            password="test-password-123",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )

        self.usuario_barra = User.objects.create_user(
            username="barra_productos_admin",
            password="test-password-123",
            rol=User.Rol.BARRA,
            sector=self.barra,
        )

        self.cocina = User.objects.create_user(
            username="cocina_productos_admin",
            password="test-password-123",
            rol=User.Rol.COCINA,
            sector=self.pizza,
        )

        self.categoria = Categoria.objects.create(
            nombre="Pizzas",
            activo=True,
        )

        self.categoria_inactiva = Categoria.objects.create(
            nombre="Categoria inactiva",
            activo=False,
        )

        self.producto = Producto.objects.create(
            nombre="Pizza muzzarella",
            precio="10000.00",
            categoria=self.categoria,
            sector_destino=self.pizza,
        )

        self.list_url = reverse("producto-list")

        self.detail_url = reverse(
            "producto-detail",
            kwargs={"pk": self.producto.pk},
        )

        self.datos_producto = {
            "nombre": "Pizza especial",
            "precio": "12000.00",
            "categoria": self.categoria.pk,
            "sector_destino": self.pizza.pk,
        }

    def test_anonymous_user_cannot_create_product(self):
        response = self.client.post(
            self.list_url,
            self.datos_producto,
            format="json",
        )

        self.assertIn(
            response.status_code,
            {
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            },
        )

    def test_non_admin_users_cannot_create_products(self):
        for usuario in [
            self.mozo,
            self.usuario_barra,
            self.cocina,
        ]:
            with self.subTest(rol=usuario.rol):
                self.client.force_authenticate(user=usuario)

                response = self.client.post(
                    self.list_url,
                    self.datos_producto,
                    format="json",
                )

                self.assertEqual(
                    response.status_code,
                    status.HTTP_403_FORBIDDEN,
                )

        self.assertEqual(Producto.objects.count(), 1)

    def test_admin_can_create_product(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.post(
            self.list_url,
            self.datos_producto,
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            response.data,
        )

        self.assertTrue(
            Producto.objects.filter(
                nombre="Pizza especial",
                precio="12000.00",
                sector_destino=self.pizza,
            ).exists()
        )

    def test_non_admin_users_cannot_modify_products(self):
        for usuario in [
            self.mozo,
            self.usuario_barra,
            self.cocina,
        ]:
            with self.subTest(rol=usuario.rol):
                self.client.force_authenticate(user=usuario)

                response = self.client.patch(
                    self.detail_url,
                    {"precio": "15000.00"},
                    format="json",
                )

                self.assertEqual(
                    response.status_code,
                    status.HTTP_403_FORBIDDEN,
                )

        self.producto.refresh_from_db()
        self.assertEqual(self.producto.precio, 10000)

    def test_admin_can_modify_product_price(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.patch(
            self.detail_url,
            {"precio": "15000.00"},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            response.data,
        )

        self.producto.refresh_from_db()
        self.assertEqual(self.producto.precio, 15000)

    def test_admin_can_deactivate_product(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.patch(
            self.detail_url,
            {"activo": False},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            response.data,
        )

        self.producto.refresh_from_db()
        self.assertFalse(self.producto.activo)

    def test_product_cannot_have_salon_as_destination(self):
        self.client.force_authenticate(user=self.admin)

        datos = {
            **self.datos_producto,
            "sector_destino": self.salon.pk,
        }

        response = self.client.post(
            self.list_url,
            datos,
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

        self.assertIn("sector_destino", response.data)

    def test_product_cannot_use_inactive_category(self):
        self.client.force_authenticate(user=self.admin)

        datos = {
            **self.datos_producto,
            "categoria": self.categoria_inactiva.pk,
        }

        response = self.client.post(
            self.list_url,
            datos,
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

        self.assertIn("categoria", response.data)

    def test_product_cannot_be_physically_deleted(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.delete(self.detail_url)

        self.assertEqual(
            response.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

        self.assertTrue(
            Producto.objects.filter(pk=self.producto.pk).exists()
        )
        
    
    def test_admin_can_list_inactive_products(self):
        self.producto.activo = False
        self.producto.save()

        self.client.force_authenticate(user=self.admin)

        response = self.client.get(
            self.list_url,
            {"todos": "true"},
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        ids = [producto["id"] for producto in response.data]

        self.assertIn(self.producto.pk, ids)

    def test_admin_can_list_products_with_inactive_sector_when_requesting_all(self):
        producto_sector_inactivo = Producto.objects.create(
            nombre="Producto de sector inactivo",
            precio="11000.00",
            categoria=self.categoria,
            sector_destino=self.pizza,
            activo=True,
        )
        self.pizza.activo = False
        self.pizza.save(update_fields=["activo"])
        self.client.force_authenticate(user=self.admin)

        response = self.client.get(self.list_url, {"todos": "true"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [producto["id"] for producto in response.data]
        self.assertIn(producto_sector_inactivo.pk, ids)

    def test_non_admin_cannot_list_inactive_products(self):
        self.producto.activo = False
        self.producto.save()

        self.client.force_authenticate(user=self.mozo)

        response = self.client.get(
            self.list_url,
            {"todos": "true"},
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        ids = [producto["id"] for producto in response.data]

        self.assertNotIn(self.producto.pk, ids)

    def test_admin_can_retrieve_inactive_product(self):
        self.producto.activo = False
        self.producto.save()

        self.client.force_authenticate(user=self.admin)

        response = self.client.get(self.detail_url)

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        self.assertFalse(response.data["activo"])

    def test_admin_can_reactivate_product(self):
        self.producto.activo = False
        self.producto.save()

        self.client.force_authenticate(user=self.admin)

        response = self.client.patch(
            self.detail_url,
            {"activo": True},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            response.data,
        )

        self.producto.refresh_from_db()

        self.assertTrue(self.producto.activo)
        

class CategoriaAdministracionAPITests(APITestCase):
    def setUp(self):
        self.salon = Sector.objects.create(
            nombre=Sector.Nombre.SALON,
        )

        self.admin = User.objects.create_user(
            username="admin_categorias",
            password="test-password-123",
            rol=User.Rol.ADMINISTRADOR,
        )

        self.mozo = User.objects.create_user(
            username="mozo_categorias",
            password="test-password-123",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )

        self.categoria = Categoria.objects.create(
            nombre="Bebidas",
            activo=True,
        )

        self.categoria_inactiva = Categoria.objects.create(
            nombre="Promociones antiguas",
            activo=False,
        )

        self.list_url = reverse("categoria-list")

        self.detail_url = reverse(
            "categoria-detail",
            kwargs={"pk": self.categoria.pk},
        )

    def test_anonymous_user_cannot_list_categories(self):
        response = self.client.get(self.list_url)

        self.assertIn(
            response.status_code,
            {
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            },
        )

    def test_mozo_can_list_active_categories(self):
        self.client.force_authenticate(user=self.mozo)

        response = self.client.get(self.list_url)

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        ids = [categoria["id"] for categoria in response.data]

        self.assertIn(self.categoria.pk, ids)
        self.assertNotIn(self.categoria_inactiva.pk, ids)

    def test_mozo_cannot_list_inactive_categories(self):
        self.client.force_authenticate(user=self.mozo)

        response = self.client.get(
            self.list_url,
            {"todos": "true"},
        )

        ids = [categoria["id"] for categoria in response.data]

        self.assertNotIn(self.categoria_inactiva.pk, ids)

    def test_admin_can_list_all_categories(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.get(
            self.list_url,
            {"todos": "true"},
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        ids = [categoria["id"] for categoria in response.data]

        self.assertIn(self.categoria.pk, ids)
        self.assertIn(self.categoria_inactiva.pk, ids)

    def test_mozo_cannot_create_category(self):
        self.client.force_authenticate(user=self.mozo)

        response = self.client.post(
            self.list_url,
            {"nombre": "Pizzas"},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_admin_can_create_category(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.post(
            self.list_url,
            {"nombre": "Pizzas"},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            response.data,
        )

        self.assertTrue(
            Categoria.objects.filter(
                nombre="Pizzas",
            ).exists()
        )

    def test_mozo_cannot_modify_category(self):
        self.client.force_authenticate(user=self.mozo)

        response = self.client.patch(
            self.detail_url,
            {"nombre": "Bebidas nuevas"},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_admin_can_modify_category(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.patch(
            self.detail_url,
            {"nombre": "Bebidas nuevas"},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            response.data,
        )

        self.categoria.refresh_from_db()

        self.assertEqual(
            self.categoria.nombre,
            "Bebidas nuevas",
        )

    def test_admin_can_deactivate_category(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.patch(
            self.detail_url,
            {"activo": False},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            response.data,
        )

        self.categoria.refresh_from_db()

        self.assertFalse(self.categoria.activo)

    def test_admin_can_reactivate_category(self):
        self.client.force_authenticate(user=self.admin)

        url = reverse(
            "categoria-detail",
            kwargs={"pk": self.categoria_inactiva.pk},
        )

        response = self.client.patch(
            url,
            {"activo": True},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            response.data,
        )

        self.categoria_inactiva.refresh_from_db()

        self.assertTrue(self.categoria_inactiva.activo)

    def test_category_cannot_be_physically_deleted(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.delete(self.detail_url)

        self.assertEqual(
            response.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

        self.assertTrue(
            Categoria.objects.filter(
                pk=self.categoria.pk,
            ).exists()
        )

class CategoriaAdministracionAPITests(APITestCase):
    def setUp(self):
        self.salon = Sector.objects.create(
            nombre=Sector.Nombre.SALON,
        )

        self.admin = User.objects.create_user(
            username="admin_categorias",
            password="test-password-123",
            rol=User.Rol.ADMINISTRADOR,
        )

        self.mozo = User.objects.create_user(
            username="mozo_categorias",
            password="test-password-123",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )

        self.categoria = Categoria.objects.create(
            nombre="Bebidas",
            activo=True,
        )

        self.categoria_inactiva = Categoria.objects.create(
            nombre="Promociones antiguas",
            activo=False,
        )

        self.list_url = reverse("categoria-list")

        self.detail_url = reverse(
            "categoria-detail",
            kwargs={"pk": self.categoria.pk},
        )

    def test_anonymous_user_cannot_list_categories(self):
        response = self.client.get(self.list_url)

        self.assertIn(
            response.status_code,
            {
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            },
        )

    def test_mozo_can_list_active_categories(self):
        self.client.force_authenticate(user=self.mozo)

        response = self.client.get(self.list_url)

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        ids = [categoria["id"] for categoria in response.data]

        self.assertIn(self.categoria.pk, ids)
        self.assertNotIn(self.categoria_inactiva.pk, ids)

    def test_mozo_cannot_list_inactive_categories(self):
        self.client.force_authenticate(user=self.mozo)

        response = self.client.get(
            self.list_url,
            {"todos": "true"},
        )

        ids = [categoria["id"] for categoria in response.data]

        self.assertNotIn(self.categoria_inactiva.pk, ids)

    def test_admin_can_list_all_categories(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.get(
            self.list_url,
            {"todos": "true"},
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        ids = [categoria["id"] for categoria in response.data]

        self.assertIn(self.categoria.pk, ids)
        self.assertIn(self.categoria_inactiva.pk, ids)

    def test_mozo_cannot_create_category(self):
        self.client.force_authenticate(user=self.mozo)

        response = self.client.post(
            self.list_url,
            {"nombre": "Pizzas"},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_admin_can_create_category(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.post(
            self.list_url,
            {"nombre": "Pizzas"},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            response.data,
        )

        self.assertTrue(
            Categoria.objects.filter(
                nombre="Pizzas",
            ).exists()
        )

    def test_mozo_cannot_modify_category(self):
        self.client.force_authenticate(user=self.mozo)

        response = self.client.patch(
            self.detail_url,
            {"nombre": "Bebidas nuevas"},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_admin_can_modify_category(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.patch(
            self.detail_url,
            {"nombre": "Bebidas nuevas"},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            response.data,
        )

        self.categoria.refresh_from_db()

        self.assertEqual(
            self.categoria.nombre,
            "Bebidas nuevas",
        )

    def test_admin_can_deactivate_category(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.patch(
            self.detail_url,
            {"activo": False},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            response.data,
        )

        self.categoria.refresh_from_db()

        self.assertFalse(self.categoria.activo)

    def test_admin_can_reactivate_category(self):
        self.client.force_authenticate(user=self.admin)

        url = reverse(
            "categoria-detail",
            kwargs={"pk": self.categoria_inactiva.pk},
        )

        response = self.client.patch(
            url,
            {"activo": True},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
            response.data,
        )

        self.categoria_inactiva.refresh_from_db()

        self.assertTrue(self.categoria_inactiva.activo)

    def test_category_cannot_be_physically_deleted(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.delete(self.detail_url)

        self.assertEqual(
            response.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

        self.assertTrue(
            Categoria.objects.filter(
                pk=self.categoria.pk,
            ).exists()
        )
