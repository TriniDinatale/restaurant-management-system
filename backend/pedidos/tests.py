
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import TestCase

from productos.models import Categoria, Producto
from usuarios.models import Sector, User

from .models import Mesa, Cuenta, Pedido, DetallePedido

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from unittest.mock import patch
from django.db import IntegrityError, transaction

class PedidoModelTests(TestCase):
    def setUp(self):
        self.salon = Sector.objects.create(
            nombre=Sector.Nombre.SALON,
        )

        self.pizza = Sector.objects.create(
            nombre=Sector.Nombre.PIZZA,
        )

        self.mozo = User.objects.create_user(
            username="mozo_pedidos",
            password="test-password-123",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )

        self.cocina = User.objects.create_user(
            username="cocina_pedidos",
            password="test-password-123",
            rol=User.Rol.COCINA,
            sector=self.pizza,
        )

        self.mesa = Mesa.objects.create(numero=1)
        
        self.cuenta = Cuenta.objects.create(mesa=self.mesa)
        
        self.categoria = Categoria.objects.create(
            nombre="Pizzas",
        )

        self.producto = Producto.objects.create(
            nombre="Pizza muzzarella",
            precio=Decimal("10000.00"),
            categoria=self.categoria,
            sector_destino=self.pizza,
        )

        self.pedido = Pedido.objects.create(
            mesa=self.mesa,
            mozo=self.mozo,
        )

    def test_mesa_has_unique_number(self):
        mesa = Mesa(numero=1)

        with self.assertRaises(ValidationError):
            mesa.full_clean()

    def test_pedido_must_belong_to_mozo(self):
        pedido = Pedido(
            mesa=self.mesa,
            mozo=self.cocina,
        )

        with self.assertRaises(ValidationError):
            pedido.save()

    def test_pedido_can_belong_to_mozo(self):
        self.assertEqual(
            self.pedido.mozo,
            self.mozo,
        )

    def test_detalle_copies_product_price(self):
        detalle = DetallePedido.objects.create(
            pedido=self.pedido,
            producto=self.producto,
            cantidad=2,
        )

        self.assertEqual(
            detalle.precio_unitario,
            Decimal("10000.00"),
        )

    def test_detalle_copies_destination_sector(self):
        detalle = DetallePedido.objects.create(
            pedido=self.pedido,
            producto=self.producto,
            cantidad=1,
        )

        self.assertEqual(
            detalle.sector_destino,
            self.pizza,
        )

    def test_product_price_change_does_not_modify_existing_detail(self):
        detalle = DetallePedido.objects.create(
            pedido=self.pedido,
            producto=self.producto,
            cantidad=1,
        )

        self.producto.precio = Decimal("15000.00")
        self.producto.save()

        detalle.refresh_from_db()

        self.assertEqual(
            detalle.precio_unitario,
            Decimal("10000.00"),
        )

    def test_detalle_calculates_subtotal(self):
        detalle = DetallePedido.objects.create(
            pedido=self.pedido,
            producto=self.producto,
            cantidad=3,
        )

        self.assertEqual(
            detalle.subtotal,
            Decimal("30000.00"),
        )

    def test_detalle_rejects_zero_quantity(self):
        detalle = DetallePedido(
            pedido=self.pedido,
            producto=self.producto,
            cantidad=0,
        )

        with self.assertRaises(ValidationError):
            detalle.save()

    def test_detalle_rejects_inactive_product(self):
        self.producto.activo = False
        self.producto.save()

        detalle = DetallePedido(
            pedido=self.pedido,
            producto=self.producto,
            cantidad=1,
        )

        with self.assertRaises(ValidationError):
            detalle.save()

    def test_detalle_rejects_product_from_inactive_category(self):
        self.categoria.activo = False
        self.categoria.save()

        detalle = DetallePedido(
            pedido=self.pedido,
            producto=self.producto,
            cantidad=1,
        )

        with self.assertRaises(ValidationError):
            detalle.save()


class PedidoAPITests(APITestCase):
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
            username="mozo_api",
            password="test-password-123",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )

        self.admin = User.objects.create_user(
            username="admin_api",
            password="test-password-123",
            rol=User.Rol.ADMINISTRADOR,
        )

        self.usuario_barra = User.objects.create_user(
            username="barra_api",
            password="test-password-123",
            rol=User.Rol.BARRA,
            sector=self.barra,
        )

        self.cocina = User.objects.create_user(
            username="cocina_api",
            password="test-password-123",
            rol=User.Rol.COCINA,
            sector=self.pizza,
        )

        self.mesa = Mesa.objects.create(numero=1)
        
        self.cuenta = Cuenta.objects.create(mesa=self.mesa)
        
        self.categoria = Categoria.objects.create(
            nombre="Pizzas",
        )

        self.producto = Producto.objects.create(
            nombre="Pizza muzzarella",
            precio=Decimal("10000.00"),
            categoria=self.categoria,
            sector_destino=self.pizza,
        )

        self.producto_barra = Producto.objects.create(
            nombre="Gaseosa",
            precio=Decimal("3000.00"),
            categoria=self.categoria,
            sector_destino=self.barra,
        )

        self.url = reverse("pedido-create")

        self.datos = {
            "mesa": self.mesa.pk,
            "cuenta": self.cuenta.pk,
            "detalles": [
                {
                    "producto": self.producto.pk,
                    "cantidad": 2,
                },
                {
                    "producto": self.producto_barra.pk,
                    "cantidad": 1,
                },
            ],
        }

    def test_anonymous_user_cannot_create_order(self):
        response = self.client.post(
            self.url,
            self.datos,
            format="json",
        )

        self.assertIn(
            response.status_code,
            {
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            },
        )

        self.assertEqual(Pedido.objects.count(), 0)

    def test_non_waiter_roles_cannot_create_order(self):
        for usuario in [
            self.admin,
            self.usuario_barra,
            self.cocina,
        ]:
            with self.subTest(rol=usuario.rol):
                self.client.force_authenticate(user=usuario)

                response = self.client.post(
                    self.url,
                    self.datos,
                    format="json",
                )

                self.assertEqual(
                    response.status_code,
                    status.HTTP_403_FORBIDDEN,
                )

        self.assertEqual(Pedido.objects.count(), 0)

    def test_waiter_can_create_order_with_multiple_products(self):
        self.client.force_authenticate(user=self.mozo)

        response = self.client.post(
            self.url,
            self.datos,
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            response.data,
        )

        self.assertEqual(Pedido.objects.count(), 1)
        self.assertEqual(DetallePedido.objects.count(), 2)

        pedido = Pedido.objects.get()

        self.assertEqual(pedido.mozo, self.mozo)
        self.assertEqual(pedido.mesa, self.mesa)

    def test_order_copies_prices_and_destination_sectors(self):
        self.client.force_authenticate(user=self.mozo)

        response = self.client.post(
            self.url,
            self.datos,
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
        )

        detalle_pizza = DetallePedido.objects.get(
            producto=self.producto,
        )

        detalle_barra = DetallePedido.objects.get(
            producto=self.producto_barra,
        )

        self.assertEqual(
            detalle_pizza.precio_unitario,
            Decimal("10000.00"),
        )

        self.assertEqual(
            detalle_pizza.sector_destino,
            self.pizza,
        )

        self.assertEqual(
            detalle_barra.precio_unitario,
            Decimal("3000.00"),
        )

        self.assertEqual(
            detalle_barra.sector_destino,
            self.barra,
        )

    def test_order_requires_at_least_one_product(self):
        self.client.force_authenticate(user=self.mozo)

        response = self.client.post(
            self.url,
            {
                "mesa": self.mesa.pk,
                "detalles": [],
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

        self.assertEqual(Pedido.objects.count(), 0)

    def test_order_rejects_invalid_table(self):
        self.client.force_authenticate(user=self.mozo)

        datos = {
            **self.datos,
            "mesa": 999999,
        }

        response = self.client.post(
            self.url,
            datos,
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

        self.assertEqual(Pedido.objects.count(), 0)

    def test_order_rejects_inactive_product(self):
        self.client.force_authenticate(user=self.mozo)

        self.producto.activo = False
        self.producto.save()

        response = self.client.post(
            self.url,
            self.datos,
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

        self.assertEqual(Pedido.objects.count(), 0)
        self.assertEqual(DetallePedido.objects.count(), 0)

    def test_order_rejects_zero_quantity(self):
        self.client.force_authenticate(user=self.mozo)

        datos = {
            "mesa": self.mesa.pk,
            "detalles": [
                {
                    "producto": self.producto.pk,
                    "cantidad": 0,
                },
            ],
        }

        response = self.client.post(
            self.url,
            datos,
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

        self.assertEqual(Pedido.objects.count(), 0)

    def test_client_cannot_override_price_or_destination(self):
        self.client.force_authenticate(user=self.mozo)

        datos = {
            "mesa": self.mesa.pk,
            "cuenta": self.cuenta.pk,
            "detalles": [
                {
                    "producto": self.producto.pk,
                    "cantidad": 1,
                    "precio_unitario": "1.00",
                    "sector_destino": self.barra.pk,
                },
            ],
        }

        response = self.client.post(
            self.url,
            datos,
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            response.data,
        )

        detalle = DetallePedido.objects.get()

        self.assertEqual(
            detalle.precio_unitario,
            Decimal("10000.00"),
        )

        self.assertEqual(
            detalle.sector_destino,
            self.pizza,
        )

    def test_client_cannot_assign_another_waiter(self):
        self.client.force_authenticate(user=self.mozo)

        datos = {
            **self.datos,
            "mozo": self.admin.pk,
        }

        response = self.client.post(
            self.url,
            datos,
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            response.data,
        )

        pedido = Pedido.objects.get()

        self.assertEqual(pedido.mozo, self.mozo)
    
    def test_order_is_rolled_back_if_second_detail_fails(self):
        self.client.force_authenticate(user=self.mozo)

        original_save = DetallePedido.save
        contador = 0

        def guardar_detalle(instancia, *args, **kwargs):
            nonlocal contador

            contador += 1

            if contador == 2:
                raise ValidationError(
                    "Error simulado al guardar el segundo detalle."
                )

            return original_save(instancia, *args, **kwargs)

        with patch.object(
            DetallePedido,
            "save",
            autospec=True,
            side_effect=guardar_detalle,
        ):
            with self.assertRaises(ValidationError):
                self.client.post(
                    self.url,
                    self.datos,
                    format="json",
                )

        self.assertEqual(contador, 2)

        self.assertEqual(
            Pedido.objects.count(),
            0,
        )

        self.assertEqual(
            DetallePedido.objects.count(),
            0,
        )
    
    def test_order_rejects_closed_account(self):
        self.client.force_authenticate(user=self.mozo)

        self.cuenta.cerrar()

        response = self.client.post(
            self.url,
            self.datos,
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            response.data,
        )
        self.assertEqual(Pedido.objects.count(), 0)
        self.assertEqual(DetallePedido.objects.count(), 0)

    def test_order_rejects_account_from_another_table(self):
        self.client.force_authenticate(user=self.mozo)

        otra_mesa = Mesa.objects.create(numero=2)

        datos = {
            **self.datos,
            "mesa": otra_mesa.pk,
        }

        response = self.client.post(
            self.url,
            datos,
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            response.data,
        )
        self.assertEqual(Pedido.objects.count(), 0)
        self.assertEqual(DetallePedido.objects.count(), 0)

        

class CuentaModelTests(TestCase):
    def setUp(self):
        self.mesa = Mesa.objects.create(numero=10)

    def test_new_account_starts_open(self):
        cuenta = Cuenta.objects.create(mesa=self.mesa)

        self.assertEqual(
            cuenta.estado,
            Cuenta.Estado.ABIERTA,
        )
        self.assertIsNotNone(cuenta.fecha_apertura)
        self.assertIsNone(cuenta.fecha_cierre)

    def test_same_table_cannot_have_two_open_accounts(self):
        Cuenta.objects.create(mesa=self.mesa)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Cuenta.objects.create(mesa=self.mesa)

        self.assertEqual(
            Cuenta.objects.filter(
                mesa=self.mesa,
                estado=Cuenta.Estado.ABIERTA,
            ).count(),
            1,
        )

    def test_closing_account_records_closing_date(self):
        cuenta = Cuenta.objects.create(mesa=self.mesa)

        cuenta.cerrar()
        cuenta.refresh_from_db()

        self.assertEqual(
            cuenta.estado,
            Cuenta.Estado.CERRADA,
        )
        self.assertIsNotNone(cuenta.fecha_cierre)

    def test_closed_account_cannot_be_closed_again(self):
        cuenta = Cuenta.objects.create(mesa=self.mesa)
        cuenta.cerrar()

        with self.assertRaises(ValidationError):
            cuenta.cerrar()

    def test_table_can_be_reopened_after_closing(self):
        primera_cuenta = Cuenta.objects.create(
            mesa=self.mesa,
        )
        primera_cuenta.cerrar()

        segunda_cuenta = Cuenta.objects.create(
            mesa=self.mesa,
        )

        self.assertNotEqual(
            primera_cuenta.pk,
            segunda_cuenta.pk,
        )

        self.assertEqual(
            primera_cuenta.estado,
            Cuenta.Estado.CERRADA,
        )

        self.assertEqual(
            segunda_cuenta.estado,
            Cuenta.Estado.ABIERTA,
        )

        self.assertEqual(
            self.mesa.cuentas.count(),
            2,
        )

    def test_different_tables_can_have_open_accounts(self):
        otra_mesa = Mesa.objects.create(numero=11)

        Cuenta.objects.create(mesa=self.mesa)
        Cuenta.objects.create(mesa=otra_mesa)

        self.assertEqual(
            Cuenta.objects.filter(
                estado=Cuenta.Estado.ABIERTA,
            ).count(),
            2,
        )


class CuentaAPITests(APITestCase):
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
            username="mozo_cuenta_api",
            password="test-password-123",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )
        self.admin = User.objects.create_user(
            username="admin_cuenta_api",
            password="test-password-123",
            rol=User.Rol.ADMINISTRADOR,
        )
        self.usuario_barra = User.objects.create_user(
            username="barra_cuenta_api",
            password="test-password-123",
            rol=User.Rol.BARRA,
            sector=self.barra,
        )
        self.cocina = User.objects.create_user(
            username="cocina_cuenta_api",
            password="test-password-123",
            rol=User.Rol.COCINA,
            sector=self.pizza,
        )

        self.mesa = Mesa.objects.create(numero=20)
        self.url = reverse("cuenta-create")
        self.datos = {"mesa": self.mesa.pk}

    def test_waiter_can_open_account(self):
        self.client.force_authenticate(user=self.mozo)

        response = self.client.post(
            self.url,
            self.datos,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Cuenta.objects.count(), 1)
        self.assertEqual(Cuenta.objects.get().mesa, self.mesa)

    def test_non_waiter_roles_cannot_open_account(self):
        for usuario in [self.admin, self.usuario_barra, self.cocina]:
            with self.subTest(rol=usuario.rol):
                self.client.force_authenticate(user=usuario)

                response = self.client.post(
                    self.url,
                    self.datos,
                    format="json",
                )

                self.assertEqual(
                    response.status_code,
                    status.HTTP_403_FORBIDDEN,
                )

        self.assertEqual(Cuenta.objects.count(), 0)

    def test_anonymous_user_cannot_open_account(self):
        response = self.client.post(
            self.url,
            self.datos,
            format="json",
        )

        self.assertIn(
            response.status_code,
            {
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            },
        )
        self.assertEqual(Cuenta.objects.count(), 0)

    def test_open_account_cannot_be_duplicated_for_same_table(self):
        self.client.force_authenticate(user=self.mozo)

        primera_respuesta = self.client.post(
            self.url,
            self.datos,
            format="json",
        )
        segunda_respuesta = self.client.post(
            self.url,
            self.datos,
            format="json",
        )

        self.assertEqual(
            primera_respuesta.status_code,
            status.HTTP_201_CREATED,
        )
        self.assertEqual(
            segunda_respuesta.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertIn("mesa", segunda_respuesta.data)
        self.assertEqual(Cuenta.objects.count(), 1)

    def test_new_account_is_open(self):
        self.client.force_authenticate(user=self.mozo)

        response = self.client.post(
            self.url,
            self.datos,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["estado"], Cuenta.Estado.ABIERTA)

    def test_account_can_be_reopened_after_closing(self):
        self.client.force_authenticate(user=self.mozo)

        primera_respuesta = self.client.post(
            self.url,
            self.datos,
            format="json",
        )
        primera_cuenta = Cuenta.objects.get(pk=primera_respuesta.data["id"])
        primera_cuenta.cerrar()

        segunda_respuesta = self.client.post(
            self.url,
            self.datos,
            format="json",
        )

        self.assertEqual(
            segunda_respuesta.status_code,
            status.HTTP_201_CREATED,
        )
        self.assertEqual(Cuenta.objects.count(), 2)
        self.assertEqual(
            Cuenta.objects.filter(
                mesa=self.mesa,
                estado=Cuenta.Estado.ABIERTA,
            ).count(),
            1,
        )

    def test_unique_constraint_collision_returns_validation_error(self):
        self.client.force_authenticate(user=self.mozo)
        driver_error = Exception("duplicate key")
        driver_error.diag = SimpleNamespace(
            constraint_name="unique_cuenta_abierta_por_mesa",
        )
        integrity_error = IntegrityError("duplicate key")
        integrity_error.__cause__ = driver_error

        with patch(
            "pedidos.serializers.Cuenta.objects.create",
            side_effect=integrity_error,
        ):
            response = self.client.post(
                self.url,
                self.datos,
                format="json",
            )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertIn("mesa", response.data)
        self.assertEqual(Cuenta.objects.count(), 0)

