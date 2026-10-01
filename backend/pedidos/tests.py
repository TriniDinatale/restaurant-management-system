
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
            cuenta=self.cuenta,
        )

    def test_mesa_has_unique_number(self):
        mesa = Mesa(numero=1)

        with self.assertRaises(ValidationError):
            mesa.full_clean()

    def test_pedido_must_belong_to_mozo(self):
        pedido = Pedido(
            mesa=self.mesa,
            mozo=self.cocina,
            cuenta=self.cuenta,
        )

        with self.assertRaises(ValidationError):
            pedido.save()

    def test_pedido_can_belong_to_mozo(self):
        self.assertEqual(
            self.pedido.mozo,
            self.mozo,
        )

    def test_pedido_requires_account(self):
        pedido = Pedido(
            mesa=self.mesa,
            mozo=self.mozo,
        )

        with self.assertRaises(ValidationError):
            pedido.save()

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

    def test_order_requires_account(self):
        self.client.force_authenticate(user=self.mozo)
        datos = {**self.datos}
        datos.pop("cuenta")

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


class DetallePreparacionAPITests(APITestCase):
    def setUp(self):
        self.salon = Sector.objects.create(nombre=Sector.Nombre.SALON)
        self.barra = Sector.objects.create(nombre=Sector.Nombre.BARRA)
        self.pizza = Sector.objects.create(nombre=Sector.Nombre.PIZZA)
        self.platos = Sector.objects.create(nombre=Sector.Nombre.PLATOS)

        self.mozo = User.objects.create_user(
            username="mozo_preparacion",
            password="test-password-123",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )
        self.usuario_barra = User.objects.create_user(
            username="barra_preparacion",
            password="test-password-123",
            rol=User.Rol.BARRA,
            sector=self.barra,
        )
        self.cocina_pizza = User.objects.create_user(
            username="cocina_pizza_preparacion",
            password="test-password-123",
            rol=User.Rol.COCINA,
            sector=self.pizza,
        )
        self.cocina_platos = User.objects.create_user(
            username="cocina_platos_preparacion",
            password="test-password-123",
            rol=User.Rol.COCINA,
            sector=self.platos,
        )
        self.admin = User.objects.create_user(
            username="admin_preparacion",
            password="test-password-123",
            rol=User.Rol.ADMINISTRADOR,
        )

        self.mesa = Mesa.objects.create(numero=30)
        self.cuenta = Cuenta.objects.create(mesa=self.mesa)
        self.pedido = Pedido.objects.create(
            mesa=self.mesa,
            mozo=self.mozo,
            cuenta=self.cuenta,
        )
        self.categoria = Categoria.objects.create(
            nombre="Preparacion",
        )

        self.detalles = {}
        for nombre, sector in [
            ("Bebida preparacion", self.barra),
            ("Pizza preparacion", self.pizza),
            ("Plato preparacion", self.platos),
        ]:
            producto = Producto.objects.create(
                nombre=nombre,
                precio=Decimal("1000.00"),
                categoria=self.categoria,
                sector_destino=sector,
            )
            self.detalles[sector.nombre] = DetallePedido.objects.create(
                pedido=self.pedido,
                producto=producto,
                cantidad=1,
            )

        self.list_url = reverse("detalle-preparacion-list")

    def estado_url(self, detalle):
        return reverse(
            "detalle-preparacion-estado",
            args=[detalle.pk],
        )

    def ids_visibles(self, response):
        return {detalle["id"] for detalle in response.data}

    def test_new_detail_starts_pending(self):
        for detalle in self.detalles.values():
            with self.subTest(sector=detalle.sector_destino.nombre):
                self.assertEqual(
                    detalle.estado_preparacion,
                    DetallePedido.EstadoPreparacion.PENDIENTE,
                )

    def test_barra_only_sees_bar_details(self):
        self.client.force_authenticate(user=self.usuario_barra)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.ids_visibles(response),
            {self.detalles[Sector.Nombre.BARRA].pk},
        )

    def test_pizza_kitchen_only_sees_pizza_details(self):
        self.client.force_authenticate(user=self.cocina_pizza)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.ids_visibles(response),
            {self.detalles[Sector.Nombre.PIZZA].pk},
        )

    def test_platos_kitchen_only_sees_platos_details(self):
        self.client.force_authenticate(user=self.cocina_platos)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.ids_visibles(response),
            {self.detalles[Sector.Nombre.PLATOS].pk},
        )

    def test_barra_can_advance_its_detail_to_ready(self):
        detalle = self.detalles[Sector.Nombre.BARRA]
        self.client.force_authenticate(user=self.usuario_barra)

        response = self.client.patch(
            self.estado_url(detalle),
            {"estado_preparacion": DetallePedido.EstadoPreparacion.EN_PREPARACION},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(
            response.data["estado_preparacion"],
            DetallePedido.EstadoPreparacion.EN_PREPARACION,
        )

        response = self.client.patch(
            self.estado_url(detalle),
            {"estado_preparacion": DetallePedido.EstadoPreparacion.LISTO},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(
            response.data["estado_preparacion"],
            DetallePedido.EstadoPreparacion.LISTO,
        )

    def test_kitchen_can_advance_details_in_its_assigned_sector(self):
        for usuario, nombre_sector in [
            (self.cocina_pizza, Sector.Nombre.PIZZA),
            (self.cocina_platos, Sector.Nombre.PLATOS),
        ]:
            with self.subTest(sector=nombre_sector):
                detalle = self.detalles[nombre_sector]
                self.client.force_authenticate(user=usuario)

                response = self.client.patch(
                    self.estado_url(detalle),
                    {"estado_preparacion": DetallePedido.EstadoPreparacion.EN_PREPARACION},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_200_OK)

                response = self.client.patch(
                    self.estado_url(detalle),
                    {"estado_preparacion": DetallePedido.EstadoPreparacion.LISTO},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(
                    response.data["estado_preparacion"],
                    DetallePedido.EstadoPreparacion.LISTO,
                )

    def test_worker_cannot_transition_detail_from_another_sector(self):
        detalle = self.detalles[Sector.Nombre.PIZZA]
        self.client.force_authenticate(user=self.usuario_barra)

        response = self.client.patch(
            self.estado_url(detalle),
            {"estado_preparacion": DetallePedido.EstadoPreparacion.EN_PREPARACION},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        detalle.refresh_from_db()
        self.assertEqual(
            detalle.estado_preparacion,
            DetallePedido.EstadoPreparacion.PENDIENTE,
        )

    def test_mozo_and_admin_cannot_access_preparation(self):
        for usuario in [self.mozo, self.admin]:
            with self.subTest(rol=usuario.rol):
                self.client.force_authenticate(user=usuario)

                list_response = self.client.get(self.list_url)
                transition_response = self.client.patch(
                    self.estado_url(self.detalles[Sector.Nombre.BARRA]),
                    {"estado_preparacion": DetallePedido.EstadoPreparacion.EN_PREPARACION},
                    format="json",
                )

                self.assertEqual(
                    list_response.status_code,
                    status.HTTP_403_FORBIDDEN,
                )
                self.assertEqual(
                    transition_response.status_code,
                    status.HTTP_403_FORBIDDEN,
                )

    def test_anonymous_user_cannot_access_preparation(self):
        responses = [
            self.client.get(self.list_url),
            self.client.patch(
                self.estado_url(self.detalles[Sector.Nombre.BARRA]),
                {"estado_preparacion": DetallePedido.EstadoPreparacion.EN_PREPARACION},
                format="json",
            ),
        ]

        for response in responses:
            self.assertIn(
                response.status_code,
                {
                    status.HTTP_401_UNAUTHORIZED,
                    status.HTTP_403_FORBIDDEN,
                },
            )

    def test_cannot_skip_pending_and_mark_detail_ready(self):
        detalle = self.detalles[Sector.Nombre.BARRA]
        self.client.force_authenticate(user=self.usuario_barra)

        response = self.client.patch(
            self.estado_url(detalle),
            {"estado_preparacion": DetallePedido.EstadoPreparacion.LISTO},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        detalle.refresh_from_db()
        self.assertEqual(
            detalle.estado_preparacion,
            DetallePedido.EstadoPreparacion.PENDIENTE,
        )

    def test_repeated_transition_is_rejected_without_changing_state(self):
        detalle = self.detalles[Sector.Nombre.BARRA]
        self.client.force_authenticate(user=self.usuario_barra)
        payload = {
            "estado_preparacion": DetallePedido.EstadoPreparacion.EN_PREPARACION,
        }

        first_response = self.client.patch(
            self.estado_url(detalle),
            payload,
            format="json",
        )
        second_response = self.client.patch(
            self.estado_url(detalle),
            payload,
            format="json",
        )

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_400_BAD_REQUEST)
        detalle.refresh_from_db()
        self.assertEqual(
            detalle.estado_preparacion,
            DetallePedido.EstadoPreparacion.EN_PREPARACION,
        )

    def test_ready_detail_cannot_move_backwards(self):
        detalle = self.detalles[Sector.Nombre.BARRA]
        self.client.force_authenticate(user=self.usuario_barra)
        detalle.estado_preparacion = DetallePedido.EstadoPreparacion.LISTO
        detalle.save(update_fields=["estado_preparacion"])

        for estado in [
            DetallePedido.EstadoPreparacion.EN_PREPARACION,
            DetallePedido.EstadoPreparacion.PENDIENTE,
        ]:
            with self.subTest(estado=estado):
                response = self.client.patch(
                    self.estado_url(detalle),
                    {"estado_preparacion": estado},
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                detalle.refresh_from_db()
                self.assertEqual(
                    detalle.estado_preparacion,
                    DetallePedido.EstadoPreparacion.LISTO,
                )

