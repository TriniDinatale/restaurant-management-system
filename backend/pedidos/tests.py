
from decimal import Decimal
from datetime import timedelta
from types import SimpleNamespace
from threading import Barrier, Event, Thread
import time

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from productos.models import Categoria, Producto
from usuarios.models import Sector, User

from .models import (
    AvisoRetiro,
    AvisoCargaBarra,
    CobroCuenta,
    Cuenta,
    DetallePedido,
    ImportePagoCuenta,
    LecturaNotificacionCancelacion,
    Mesa,
    Pedido,
    PreparacionPedidoSector,
    SolicitudCancelacionDetalle,
)

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from unittest.mock import PropertyMock, patch
from django.db import IntegrityError
from uuid import uuid4


def preparar_pedido_para_cierre(pedido, estado=PreparacionPedidoSector.Estado.LISTO,
                                habilitar_retiro=True):
    """Deja la preparación de cada sector y el retiro como en el flujo real."""
    sectores = {}
    for detalle in pedido.detalles.select_related("sector_destino"):
        sectores[detalle.sector_destino_id] = detalle.sector_destino
    for sector in sectores.values():
        PreparacionPedidoSector.objects.update_or_create(
            pedido=pedido,
            sector=sector,
            defaults={"estado": estado},
        )
        if not habilitar_retiro:
            continue
        origen = (
            AvisoRetiro.Origen.BARRA
            if sector.nombre == Sector.Nombre.BARRA
            else AvisoRetiro.Origen.COCINA
        )
        AvisoRetiro.objects.get_or_create(
            pedido=pedido,
            origen=origen,
            defaults={
                "destinatario": pedido.mozo,
                "mensaje": f"{pedido.mesa.identificacion} — {origen}",
            },
        )

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

    def test_mesa_number_is_unique_within_zone(self):
        mesa = Mesa(numero=1, zona=Mesa.Zona.SALON)

        with self.assertRaises(ValidationError):
            mesa.full_clean()

    def test_database_rejects_duplicate_number_in_same_zone(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Mesa.objects.bulk_create(
                    [Mesa(numero=1, zona=Mesa.Zona.SALON)]
                )

    def test_same_mesa_number_is_allowed_in_different_zones(self):
        salon = Mesa.objects.create(numero=2, zona=Mesa.Zona.SALON)
        vereda_a = Mesa.objects.create(numero=2, zona=Mesa.Zona.VEREDA_A)
        vereda_b = Mesa.objects.create(numero=2, zona=Mesa.Zona.VEREDA_B)

        self.assertEqual(
            {salon.numero, vereda_a.numero, vereda_b.numero},
            {2},
        )

    def test_mesa_identification_uses_zone_label_and_number(self):
        casos = [
            (Mesa.Zona.SALON, "Salón · Mesa 8"),
            (Mesa.Zona.VEREDA_A, "Vereda A · Mesa 3"),
            (Mesa.Zona.VEREDA_B, "Vereda B · Mesa 3"),
        ]

        for zona, identificacion in casos:
            with self.subTest(zona=zona):
                numero = 8 if zona == Mesa.Zona.SALON else 3
                mesa = Mesa(numero=numero, zona=zona)
                self.assertEqual(mesa.identificacion, identificacion)
                self.assertEqual(str(mesa), identificacion)

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

        self.producto_platos = Producto.objects.create(
            nombre="Milanesa",
            precio=Decimal("8000.00"),
            categoria=self.categoria,
            sector_destino=Sector.objects.create(
                nombre=Sector.Nombre.PLATOS,
            ),
        )

        self.url = reverse("pedido-create")
        self.mesa_pedido_url = reverse("pedido-mesa-create")

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

    def enviar_por_mesa(self, mesa, mozo=None, producto=None):
        self.client.force_authenticate(user=mozo or self.mozo)
        return self.client.post(
            self.mesa_pedido_url,
            {
                "mesa": mesa.pk,
                "detalles": [
                    {
                        "producto": (producto or self.producto).pk,
                        "cantidad": 1,
                    },
                ],
            },
            format="json",
        )

    def test_first_table_order_opens_account_and_assigns_responsible(self):
        mesa = Mesa.objects.create(numero=31)

        response = self.enviar_por_mesa(mesa)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        cuenta = Cuenta.objects.get(mesa=mesa, estado=Cuenta.Estado.ABIERTA)
        pedido = Pedido.objects.get(mesa=mesa)
        self.assertEqual(pedido.cuenta, cuenta)
        self.assertEqual(cuenta.mozo_responsable, self.mozo)
        self.assertEqual(pedido.mozo, self.mozo)
        self.assertEqual(pedido.creado_por, self.mozo)

    def test_later_table_order_uses_same_open_account(self):
        mesa = Mesa.objects.create(numero=32)

        first = self.enviar_por_mesa(mesa)
        second = self.enviar_por_mesa(mesa)

        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.data)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED, second.data)
        self.assertEqual(Cuenta.objects.filter(mesa=mesa).count(), 1)
        cuenta = Cuenta.objects.get(mesa=mesa)
        self.assertEqual(cuenta.pedidos.count(), 2)
        self.assertEqual(cuenta.mozo_responsable, self.mozo)

    def test_table_order_uses_account_opened_explicitly(self):
        mesa = Mesa.objects.create(numero=38)
        self.client.force_authenticate(user=self.mozo)
        opened = self.client.post(
            reverse("cuenta-create"),
            {"mesa": mesa.pk},
            format="json",
        )

        response = self.enviar_por_mesa(mesa)

        self.assertEqual(opened.status_code, status.HTTP_201_CREATED, opened.data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        cuenta = Cuenta.objects.get(mesa=mesa, estado=Cuenta.Estado.ABIERTA)
        pedido = Pedido.objects.get(mesa=mesa)
        self.assertEqual(Cuenta.objects.filter(mesa=mesa).count(), 1)
        self.assertEqual(pedido.cuenta_id, cuenta.pk)
        self.assertEqual(cuenta.mozo_responsable, self.mozo)

    def test_new_occupancy_uses_new_account_without_reusing_history(self):
        mesa = Mesa.objects.create(numero=33)
        first = self.enviar_por_mesa(mesa)
        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.data)
        first_pedido = Pedido.objects.get(mesa=mesa)
        first_cuenta = first_pedido.cuenta
        aviso_historico = AvisoRetiro.objects.create(
            pedido=first_pedido,
            destinatario=self.mozo,
            mensaje=mesa.identificacion,
        )
        aviso_carga_historico = AvisoCargaBarra.objects.create(
            pedido=first_pedido,
            destinatario=self.mozo,
        )
        first_cuenta.cerrar()

        otro_mozo = User.objects.create_user(
            username="nuevo_responsable_mesa",
            password="test-password",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )
        second = self.enviar_por_mesa(mesa, mozo=otro_mozo)

        self.assertEqual(second.status_code, status.HTTP_201_CREATED, second.data)
        second_pedido = Pedido.objects.exclude(pk=first_pedido.pk).get(mesa=mesa)
        second_cuenta = second_pedido.cuenta
        self.assertNotEqual(first_cuenta.pk, second_cuenta.pk)
        self.assertEqual(second_cuenta.mozo_responsable, otro_mozo)
        self.assertEqual(list(first_cuenta.pedidos.values_list("pk", flat=True)), [first_pedido.pk])
        self.assertEqual(list(second_cuenta.pedidos.values_list("pk", flat=True)), [second_pedido.pk])
        aviso_historico.refresh_from_db()
        self.assertEqual(aviso_historico.pedido, first_pedido)
        self.assertEqual(aviso_historico.destinatario, self.mozo)
        aviso_carga_historico.refresh_from_db()
        self.assertEqual(aviso_carga_historico.pedido, first_pedido)
        self.assertEqual(aviso_carga_historico.destinatario, self.mozo)

    def test_new_table_order_rolls_back_account_order_and_preparations_on_detail_failure(self):
        mesa = Mesa.objects.create(numero=34)
        self.client.force_authenticate(user=self.mozo)

        with patch(
            "pedidos.serializers.DetallePedido.objects.create",
            side_effect=RuntimeError("Fallo simulado al crear detalle."),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    self.mesa_pedido_url,
                    {
                        "mesa": mesa.pk,
                        "detalles": [
                            {"producto": self.producto.pk, "cantidad": 1},
                        ],
                    },
                    format="json",
                )

        self.assertFalse(Cuenta.objects.filter(mesa=mesa).exists())
        self.assertFalse(Pedido.objects.filter(mesa=mesa).exists())
        self.assertFalse(PreparacionPedidoSector.objects.filter(pedido__mesa=mesa).exists())

    def test_table_order_endpoint_is_exclusive_to_waiters(self):
        mesa = Mesa.objects.create(numero=35)
        for usuario in [self.admin, self.usuario_barra, self.cocina]:
            with self.subTest(rol=usuario.rol):
                response = self.enviar_por_mesa(mesa, mozo=usuario)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.assertFalse(Cuenta.objects.filter(mesa=mesa).exists())
        self.assertFalse(Pedido.objects.filter(mesa=mesa).exists())

    def test_anonymous_user_cannot_use_table_order_endpoint(self):
        mesa = Mesa.objects.create(numero=37)

        response = self.client.post(
            self.mesa_pedido_url,
            {
                "mesa": mesa.pk,
                "detalles": [
                    {"producto": self.producto.pk, "cantidad": 1},
                ],
            },
            format="json",
        )

        self.assertIn(
            response.status_code,
            {
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            },
        )
        self.assertFalse(Cuenta.objects.filter(mesa=mesa).exists())
        self.assertFalse(Pedido.objects.filter(mesa=mesa).exists())

    def test_other_waiter_cannot_send_to_account_with_existing_responsible(self):
        mesa = Mesa.objects.create(numero=36)
        first = self.enviar_por_mesa(mesa)
        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.data)
        other = User.objects.create_user(
            username="otro_mozo_nueva_ruta",
            password="test-password",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )

        rejected = self.enviar_por_mesa(mesa, mozo=other)

        self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("responsable", str(rejected.data).lower())
        cuenta = Cuenta.objects.get(mesa=mesa, estado=Cuenta.Estado.ABIERTA)
        self.assertEqual(cuenta.mozo_responsable, self.mozo)
        self.assertEqual(cuenta.pedidos.count(), 1)

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
        self.cuenta.refresh_from_db()

        self.assertEqual(pedido.mozo, self.mozo)
        self.assertEqual(pedido.creado_por, self.mozo)
        self.assertEqual(self.cuenta.mozo_responsable, self.mozo)
        self.assertEqual(pedido.mesa, self.mesa)
        preparaciones = list(
            pedido.preparaciones_sectoriales.select_related("sector")
        )
        self.assertEqual(len(preparaciones), 2)
        self.assertEqual(
            {preparacion.sector.nombre for preparacion in preparaciones},
            {Sector.Nombre.PIZZA, Sector.Nombre.BARRA},
        )
        self.assertTrue(
            all(
                preparacion.estado
                == PreparacionPedidoSector.Estado.PENDIENTE
                for preparacion in preparaciones
            )
        )

    def test_multiple_details_in_one_sector_create_one_preparation(self):
        self.client.force_authenticate(user=self.mozo)
        response = self.client.post(
            self.url,
            {
                "mesa": self.mesa.pk,
                "cuenta": self.cuenta.pk,
                "detalles": [
                    {"producto": self.producto_barra.pk, "cantidad": 1},
                    {"producto": self.producto_barra.pk, "cantidad": 2},
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        pedido = Pedido.objects.get()
        self.assertEqual(pedido.detalles.count(), 2)
        self.assertEqual(pedido.preparaciones_sectoriales.count(), 1)
        self.assertEqual(
            pedido.preparaciones_sectoriales.get().sector,
            self.barra,
        )

    def test_pizza_and_platos_create_independent_preparations(self):
        self.client.force_authenticate(user=self.mozo)
        response = self.client.post(
            self.url,
            {
                "mesa": self.mesa.pk,
                "cuenta": self.cuenta.pk,
                "detalles": [
                    {"producto": self.producto.pk, "cantidad": 1},
                    {"producto": self.producto_platos.pk, "cantidad": 1},
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        pedido = Pedido.objects.get()
        self.assertEqual(pedido.preparaciones_sectoriales.count(), 2)
        self.assertEqual(
            set(
                pedido.preparaciones_sectoriales.values_list(
                    "sector__nombre",
                    flat=True,
                )
            ),
            {Sector.Nombre.PIZZA, Sector.Nombre.PLATOS},
        )
        self.assertFalse(pedido.cocina_lista)

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

    def test_only_account_responsible_can_add_waiter_orders(self):
        self.client.force_authenticate(user=self.mozo)
        primera_respuesta = self.client.post(
            self.url,
            self.datos,
            format="json",
        )
        self.assertEqual(primera_respuesta.status_code, status.HTTP_201_CREATED)

        otro_mozo = User.objects.create_user(
            username="otro_mozo_api",
            password="test-password",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )
        self.client.force_authenticate(user=otro_mozo)
        respuesta_rechazada = self.client.post(
            self.url,
            self.datos,
            format="json",
        )

        self.assertEqual(
            respuesta_rechazada.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertIn("responsable", str(respuesta_rechazada.data).lower())
        self.assertEqual(Pedido.objects.count(), 1)
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.mozo_responsable, self.mozo)

        self.client.force_authenticate(user=self.mozo)
        respuesta_responsable = self.client.post(
            self.url,
            self.datos,
            format="json",
        )
        self.assertEqual(
            respuesta_responsable.status_code,
            status.HTTP_201_CREATED,
        )
        self.assertEqual(Pedido.objects.count(), 2)
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.mozo_responsable, self.mozo)

    def test_legacy_account_with_orders_is_not_assigned_implicitly(self):
        Pedido.objects.create(
            mesa=self.mesa,
            cuenta=self.cuenta,
            mozo=self.mozo,
        )
        self.client.force_authenticate(user=self.mozo)

        response = self.client.post(self.url, self.datos, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("histórica", str(response.data).lower())
        self.cuenta.refresh_from_db()
        self.assertIsNone(self.cuenta.mozo_responsable_id)
        self.assertEqual(Pedido.objects.count(), 1)

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
        self.cuenta.refresh_from_db()
        self.assertIsNone(self.cuenta.mozo_responsable_id)
        self.assertEqual(PreparacionPedidoSector.objects.count(), 0)

        self.assertEqual(
            DetallePedido.objects.count(),
            0,
        )
        self.assertEqual(PreparacionPedidoSector.objects.count(), 0)

    def test_order_rolls_back_if_preparation_creation_fails(self):
        self.client.force_authenticate(user=self.mozo)

        with patch(
            "pedidos.serializers.PreparacionPedidoSector.objects.bulk_create",
            side_effect=ValidationError("Error simulado al crear preparación."),
        ):
            with self.assertRaises(ValidationError):
                self.client.post(
                    self.url,
                    self.datos,
                    format="json",
                )

        self.assertEqual(Pedido.objects.count(), 0)
        self.assertEqual(DetallePedido.objects.count(), 0)
        self.assertEqual(PreparacionPedidoSector.objects.count(), 0)
    
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

        

class BarraCargaAPITests(APITestCase):
    def setUp(self):
        self.salon = Sector.objects.create(nombre=Sector.Nombre.SALON)
        self.barra = Sector.objects.create(nombre=Sector.Nombre.BARRA)
        self.pizza = Sector.objects.create(nombre=Sector.Nombre.PIZZA)
        self.mozo = User.objects.create_user(
            username="mozo_responsable",
            password="test-password",
            first_name="Ana",
            last_name="Gomez",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )
        self.otro_mozo = User.objects.create_user(
            username="otro_mozo_responsable",
            password="test-password",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )
        self.usuario_barra = User.objects.create_user(
            username="usuario_barra_cargas",
            password="test-password",
            rol=User.Rol.BARRA,
            sector=self.barra,
        )
        self.admin = User.objects.create_user(
            username="admin_cargas",
            password="test-password",
            rol=User.Rol.ADMINISTRADOR,
        )
        self.cocina = User.objects.create_user(
            username="cocina_cargas",
            password="test-password",
            rol=User.Rol.COCINA,
            sector=self.pizza,
        )

        self.mesa = Mesa.objects.create(
            numero=1,
            zona=Mesa.Zona.VEREDA_B,
        )
        self.cuenta = Cuenta.objects.create(mesa=self.mesa)
        self.categoria = Categoria.objects.create(nombre="Carga desde barra")
        self.bebida = Producto.objects.create(
            nombre="Limonada",
            precio=Decimal("2500.00"),
            categoria=self.categoria,
            sector_destino=self.barra,
        )
        self.comida = Producto.objects.create(
            nombre="Empanada",
            precio=Decimal("1800.00"),
            categoria=self.categoria,
            sector_destino=self.pizza,
        )
        self.pedido_url = reverse("pedido-create")
        self.carga_url = reverse("barra-carga-create")
        self.avisos_url = reverse("aviso-carga-barra-list")
        self.registro_url = reverse("barra-carga-registro")

    def crear_responsable(self, cuenta=None, mesa=None, mozo=None):
        cuenta = cuenta or self.cuenta
        mesa = mesa or cuenta.mesa
        self.client.force_authenticate(user=mozo or self.mozo)
        response = self.client.post(
            self.pedido_url,
            {
                "mesa": mesa.pk,
                "cuenta": cuenta.pk,
                "detalles": [
                    {"producto": self.comida.pk, "cantidad": 1},
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        cuenta.refresh_from_db()
        return response

    def cargar_desde_barra(self, cuenta=None, mesa=None, usuario=None):
        cuenta = cuenta or self.cuenta
        mesa = mesa or cuenta.mesa
        self.client.force_authenticate(user=usuario or self.usuario_barra)
        return self.client.post(
            self.carga_url,
            {
                "mesa": mesa.pk,
                "cuenta": cuenta.pk,
                "mozo": self.otro_mozo.pk,
                "creado_por": self.admin.pk,
                "detalles": [
                    {"producto": self.bebida.pk, "cantidad": 3},
                    {"producto": self.comida.pk, "cantidad": 2},
                ],
            },
            format="json",
        )

    def test_barra_load_uses_responsible_and_records_author_and_notice_details(self):
        self.crear_responsable()

        response = self.cargar_desde_barra()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        pedido_carga = Pedido.objects.get(creado_por=self.usuario_barra)
        self.cuenta.refresh_from_db()
        self.assertEqual(pedido_carga.mozo, self.mozo)
        self.assertEqual(pedido_carga.creado_por, self.usuario_barra)
        self.assertEqual(self.cuenta.mozo_responsable, self.mozo)
        self.assertEqual(
            list(
                pedido_carga.detalles.order_by("producto__nombre").values_list(
                    "producto__nombre",
                    "cantidad",
                    "precio_unitario",
                    "sector_destino__nombre",
                )
            ),
            [
                ("Empanada", 2, Decimal("1800.00"), Sector.Nombre.PIZZA),
                ("Limonada", 3, Decimal("2500.00"), Sector.Nombre.BARRA),
            ],
        )
        self.assertEqual(
            set(pedido_carga.preparaciones_sectoriales.values_list(
                "sector__nombre",
                flat=True,
            )),
            {Sector.Nombre.BARRA, Sector.Nombre.PIZZA},
        )

        aviso = AvisoCargaBarra.objects.get(pedido=pedido_carga)
        self.assertEqual(aviso.destinatario, self.mozo)
        self.client.force_authenticate(user=self.mozo)
        avisos = self.client.get(self.avisos_url)
        self.assertEqual(avisos.status_code, status.HTTP_200_OK)
        self.assertEqual(len(avisos.data), 1)
        self.assertEqual(avisos.data[0]["pedido"], pedido_carga.pk)
        self.assertEqual(
            avisos.data[0]["mesa_identificacion"],
            "Vereda B · Mesa 1",
        )
        self.assertEqual(
            {
                (detalle["producto_nombre"], detalle["cantidad"])
                for detalle in avisos.data[0]["detalles"]
            },
            {("Limonada", 3), ("Empanada", 2)},
        )

    def test_barra_cannot_assign_responsible_to_empty_account(self):
        response = self.cargar_desde_barra()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("responsable", str(response.data).lower())
        self.cuenta.refresh_from_db()
        self.assertIsNone(self.cuenta.mozo_responsable_id)
        self.assertFalse(Pedido.objects.exists())
        self.assertFalse(AvisoCargaBarra.objects.exists())

    def test_barra_load_endpoint_is_exclusive_to_barra(self):
        for usuario in [self.mozo, self.admin, self.cocina]:
            with self.subTest(rol=usuario.rol):
                self.client.force_authenticate(user=usuario)
                response = self.client.post(
                    self.carga_url,
                    {
                        "mesa": self.mesa.pk,
                        "cuenta": self.cuenta.pk,
                        "detalles": [
                            {"producto": self.bebida.pk, "cantidad": 1},
                        ],
                    },
                    format="json",
                )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_403_FORBIDDEN,
                )

    def test_barra_notice_list_isolated_to_authenticated_waiter(self):
        self.crear_responsable()
        primera_carga = self.cargar_desde_barra()
        self.assertEqual(primera_carga.status_code, status.HTTP_201_CREATED)

        otra_mesa = Mesa.objects.create(numero=1, zona=Mesa.Zona.SALON)
        otra_cuenta = Cuenta.objects.create(mesa=otra_mesa)
        self.crear_responsable(
            cuenta=otra_cuenta,
            mesa=otra_mesa,
            mozo=self.otro_mozo,
        )
        segunda_carga = self.cargar_desde_barra(
            cuenta=otra_cuenta,
            mesa=otra_mesa,
        )
        self.assertEqual(segunda_carga.status_code, status.HTTP_201_CREATED)

        self.client.force_authenticate(user=self.mozo)
        avisos_mozo = self.client.get(
            self.avisos_url,
            {"destinatario": self.otro_mozo.pk},
        )
        self.assertEqual(len(avisos_mozo.data), 1)
        self.assertEqual(
            avisos_mozo.data[0]["mesa_identificacion"],
            "Vereda B · Mesa 1",
        )

        self.client.force_authenticate(user=self.otro_mozo)
        avisos_otro_mozo = self.client.get(self.avisos_url)
        self.assertEqual(len(avisos_otro_mozo.data), 1)
        self.assertEqual(
            avisos_otro_mozo.data[0]["mesa_identificacion"],
            "Salón · Mesa 1",
        )

        self.client.force_authenticate(user=self.usuario_barra)
        self.assertEqual(
            self.client.get(self.avisos_url).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_administrator_can_consult_barra_load_registry(self):
        self.crear_responsable()
        response = self.cargar_desde_barra()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        pedido_carga = Pedido.objects.get(creado_por=self.usuario_barra)

        self.client.force_authenticate(user=self.admin)
        registro = self.client.get(self.registro_url)

        self.assertEqual(registro.status_code, status.HTTP_200_OK)
        self.assertEqual(len(registro.data), 1)
        carga = registro.data[0]
        self.assertEqual(carga["id"], pedido_carga.pk)
        self.assertEqual(carga["autor_id"], self.usuario_barra.pk)
        self.assertEqual(carga["autor_nombre"], self.usuario_barra.username)
        self.assertEqual(carga["cuenta"], self.cuenta.pk)
        self.assertEqual(carga["mesa_identificacion"], "Vereda B · Mesa 1")
        self.assertEqual(carga["responsable_id"], self.mozo.pk)
        self.assertEqual(carga["responsable_nombre"], "Ana Gomez")
        self.assertTrue(carga["fecha_creacion"])
        self.assertEqual(len(carga["detalles"]), 2)

        User.objects.filter(pk=self.usuario_barra.pk).update(
            rol=User.Rol.ADMINISTRADOR,
        )
        self.usuario_barra.refresh_from_db()
        self.assertEqual(self.usuario_barra.rol, User.Rol.ADMINISTRADOR)
        self.client.force_authenticate(user=self.admin)
        registro_tras_cambio_rol = self.client.get(self.registro_url)
        self.assertEqual(registro_tras_cambio_rol.status_code, status.HTTP_200_OK)
        self.assertEqual(len(registro_tras_cambio_rol.data), 1)
        self.assertEqual(registro_tras_cambio_rol.data[0]["id"], pedido_carga.pk)
        User.objects.filter(pk=self.usuario_barra.pk).update(
            rol=User.Rol.BARRA,
        )
        self.usuario_barra.refresh_from_db()

        self.client.force_authenticate(user=self.mozo)
        self.assertEqual(
            self.client.get(self.registro_url).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.client.force_authenticate(user=self.usuario_barra)
        self.assertEqual(
            self.client.get(self.registro_url).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_barra_notice_failure_rolls_back_order_and_details(self):
        self.crear_responsable()
        conteo_pedidos = Pedido.objects.count()
        conteo_detalles = DetallePedido.objects.count()
        conteo_preparaciones = PreparacionPedidoSector.objects.count()

        with patch.object(
            AvisoCargaBarra,
            "save",
            side_effect=RuntimeError("fallo al crear aviso"),
        ):
            with self.assertRaises(RuntimeError):
                self.cargar_desde_barra()

        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.mozo_responsable, self.mozo)
        self.assertEqual(Pedido.objects.count(), conteo_pedidos)
        self.assertEqual(DetallePedido.objects.count(), conteo_detalles)
        self.assertEqual(
            PreparacionPedidoSector.objects.count(),
            conteo_preparaciones,
        )
        self.assertFalse(AvisoCargaBarra.objects.exists())


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
        self.mesas_url = reverse("mesa-list")
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
        self.assertIsNone(Cuenta.objects.get().mozo_responsable_id)

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

    def test_open_accounts_list_includes_table_identification_only(self):
        self.client.force_authenticate(user=self.mozo)
        segunda_mesa = Mesa.objects.create(
            numero=21,
            zona=Mesa.Zona.VEREDA_A,
        )
        cuenta_abierta = Cuenta.objects.create(mesa=segunda_mesa)
        cuenta_abierta.mozo_responsable = self.mozo
        cuenta_abierta.save(update_fields=["mozo_responsable"])
        cuenta_cerrada = Cuenta.objects.create(mesa=self.mesa)
        cuenta_cerrada.cerrar()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [cuenta["id"] for cuenta in response.data],
            [cuenta_abierta.pk],
        )
        self.assertEqual(
            response.data[0]["mesa_identificacion"],
            "Vereda A · Mesa 21",
        )
        self.assertEqual(response.data[0]["mesa"], segunda_mesa.pk)
        self.assertEqual(
            response.data[0]["mozo_responsable_id"],
            self.mozo.pk,
        )
        self.assertEqual(
            response.data[0]["mozo_responsable_nombre"],
            self.mozo.username,
        )
        self.mozo.first_name = "Ana"
        self.mozo.last_name = "Gomez"
        self.mozo.save(update_fields=["first_name", "last_name"])
        response_con_nombre = self.client.get(self.url)
        self.assertEqual(
            response_con_nombre.data[0]["mozo_responsable_nombre"],
            "Ana Gomez",
        )

    def test_mesa_list_returns_identification_ordered_by_zone_and_number(self):
        self.client.force_authenticate(user=self.mozo)
        salon_3 = Mesa.objects.create(numero=3, zona=Mesa.Zona.SALON)
        vereda_a_3 = Mesa.objects.create(numero=3, zona=Mesa.Zona.VEREDA_A)
        vereda_b_3 = Mesa.objects.create(numero=3, zona=Mesa.Zona.VEREDA_B)

        response = self.client.get(self.mesas_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [(mesa["zona"], mesa["numero"]) for mesa in response.data],
            [
                (Mesa.Zona.SALON, 3),
                (Mesa.Zona.SALON, 20),
                (Mesa.Zona.VEREDA_A, 3),
                (Mesa.Zona.VEREDA_B, 3),
            ],
        )
        self.assertEqual(
            [mesa["identificacion"] for mesa in response.data],
            [
                "Salón · Mesa 3",
                "Salón · Mesa 20",
                "Vereda A · Mesa 3",
                "Vereda B · Mesa 3",
            ],
        )
        self.assertEqual(
            [mesa["id"] for mesa in response.data],
            [salon_3.pk, self.mesa.pk, vereda_a_3.pk, vereda_b_3.pk],
        )

    def test_mesa_and_account_get_lists_allow_mozo_and_barra(self):
        for usuario in [self.mozo, self.usuario_barra]:
            with self.subTest(rol=usuario.rol):
                self.client.force_authenticate(user=usuario)
                self.assertEqual(
                    self.client.get(self.mesas_url).status_code,
                    status.HTTP_200_OK,
                )
                self.assertEqual(
                    self.client.get(self.url).status_code,
                    status.HTTP_200_OK,
                )

    def test_account_post_remains_mozo_only_when_barra_can_get_lists(self):
        self.client.force_authenticate(user=self.usuario_barra)
        self.assertEqual(
            self.client.get(self.mesas_url).status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            self.client.get(self.url).status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            self.client.post(self.url, self.datos, format="json").status_code,
            status.HTTP_403_FORBIDDEN,
        )

        self.client.force_authenticate(user=self.admin)
        for url in [self.mesas_url, self.url]:
            with self.subTest(url=url):
                self.assertEqual(
                    self.client.get(url).status_code,
                    status.HTTP_403_FORBIDDEN,
                )

        self.client.force_authenticate(user=None)
        for url in [self.mesas_url, self.url]:
            with self.subTest(url=url):
                self.assertIn(
                    self.client.get(url).status_code,
                    {
                        status.HTTP_401_UNAUTHORIZED,
                        status.HTTP_403_FORBIDDEN,
                    },
                )

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


class PreparacionPedidoSectorAPITests(APITestCase):
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
        for nombre, sector, cantidad in [
            ("Bebida preparacion", self.barra, 2),
            ("Pizza preparacion", self.pizza, 2),
            ("Plato preparacion", self.platos, 2),
        ]:
            self.detalles[sector.nombre] = []
            for indice in range(cantidad):
                producto = Producto.objects.create(
                    nombre=f"{nombre} {indice}",
                    precio=Decimal("1000.00"),
                    categoria=self.categoria,
                    sector_destino=sector,
                )
                self.detalles[sector.nombre].append(
                    DetallePedido.objects.create(
                        pedido=self.pedido,
                        producto=producto,
                        cantidad=1,
                    )
                )

        self.preparaciones = {
            sector.nombre: PreparacionPedidoSector.objects.create(
                pedido=self.pedido,
                sector=sector,
            )
            for sector in [self.barra, self.pizza, self.platos]
        }

        self.list_url = reverse("detalle-preparacion-list")

    def estado_url(self, preparacion):
        return reverse(
            "detalle-preparacion-estado",
            args=[preparacion.pk],
        )

    def preparaciones_visibles(self, response):
        return {preparacion["sector"]: preparacion for preparacion in response.data}

    def test_new_preparations_start_pending_and_are_unique_per_sector(self):
        self.assertEqual(PreparacionPedidoSector.objects.count(), 3)
        for preparacion in self.preparaciones.values():
            with self.subTest(sector=preparacion.sector.nombre):
                self.assertEqual(
                    preparacion.estado,
                    PreparacionPedidoSector.Estado.PENDIENTE,
                )

    def test_barra_only_sees_bar_preparation_and_products(self):
        self.client.force_authenticate(user=self.usuario_barra)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        visible = self.preparaciones_visibles(response)
        self.assertEqual(set(visible), {Sector.Nombre.BARRA})
        self.assertEqual(
            len(visible[Sector.Nombre.BARRA]["detalles"]),
            2,
        )
        self.assertEqual(visible[Sector.Nombre.BARRA]["mesa"], self.mesa.numero)
        self.assertEqual(visible[Sector.Nombre.BARRA]["zona"], self.mesa.zona)
        self.assertEqual(
            visible[Sector.Nombre.BARRA]["identificacion"],
            self.mesa.identificacion,
        )
        self.assertEqual(
            [
                (detalle["producto_nombre"], detalle["cantidad"])
                for detalle in visible[Sector.Nombre.BARRA]["detalles"]
            ],
            [
                (detalle.producto.nombre, detalle.cantidad)
                for detalle in self.detalles[Sector.Nombre.BARRA]
            ],
        )
        self.assertEqual(visible[Sector.Nombre.BARRA]["coordinacion_cocina"], [])

    def test_pizza_kitchen_sees_only_pizza_products_and_platos_status(self):
        self.client.force_authenticate(user=self.cocina_pizza)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        visible = self.preparaciones_visibles(response)
        self.assertEqual(set(visible), {Sector.Nombre.PIZZA})
        self.assertEqual(len(visible[Sector.Nombre.PIZZA]["detalles"]), 2)
        self.assertEqual(
            visible[Sector.Nombre.PIZZA]["coordinacion_cocina"],
            [{"sector": Sector.Nombre.PLATOS, "estado": "PENDIENTE"}],
        )

    def test_platos_kitchen_sees_only_platos_products_and_pizza_status(self):
        self.client.force_authenticate(user=self.cocina_platos)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        visible = self.preparaciones_visibles(response)
        self.assertEqual(set(visible), {Sector.Nombre.PLATOS})
        self.assertEqual(len(visible[Sector.Nombre.PLATOS]["detalles"]), 2)
        self.assertEqual(
            visible[Sector.Nombre.PLATOS]["coordinacion_cocina"],
            [{"sector": Sector.Nombre.PIZZA, "estado": "PENDIENTE"}],
        )

    def test_single_sector_orders_have_no_cooking_coordination(self):
        PreparacionPedidoSector.objects.filter(
            pedido=self.pedido,
            sector__nombre=Sector.Nombre.PLATOS,
        ).delete()

        self.client.force_authenticate(user=self.cocina_pizza)
        pizza_response = self.client.get(self.list_url)
        self.assertEqual(
            self.preparaciones_visibles(pizza_response)[Sector.Nombre.PIZZA][
                "coordinacion_cocina"
            ],
            [],
        )

        self.client.force_authenticate(user=self.cocina_platos)
        platos_response = self.client.get(self.list_url)
        self.assertEqual(platos_response.data, [])

    def test_client_sector_parameter_does_not_change_visible_preparations(self):
        self.client.force_authenticate(user=self.cocina_pizza)

        response = self.client.get(
            self.list_url,
            {"sector": Sector.Nombre.PLATOS},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(self.preparaciones_visibles(response)),
            {Sector.Nombre.PIZZA},
        )

    def test_barra_can_advance_its_preparation_to_ready(self):
        preparacion = self.preparaciones[Sector.Nombre.BARRA]
        self.client.force_authenticate(user=self.usuario_barra)

        response = self.client.patch(
            self.estado_url(preparacion),
            {"estado": PreparacionPedidoSector.Estado.LISTO},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(
            response.data["estado"],
            PreparacionPedidoSector.Estado.LISTO,
        )
        self.assertEqual(
            AvisoRetiro.objects.filter(
                pedido=self.pedido,
                origen=AvisoRetiro.Origen.BARRA,
            ).count(),
            1,
        )
        repeated = self.client.patch(
            self.estado_url(preparacion),
            {"estado": PreparacionPedidoSector.Estado.LISTO},
            format="json",
        )
        self.assertEqual(repeated.status_code, status.HTTP_200_OK)
        self.assertEqual(
            AvisoRetiro.objects.filter(
                pedido=self.pedido,
                origen=AvisoRetiro.Origen.BARRA,
            ).count(),
            1,
        )

    def test_barra_can_finish_legacy_in_preparation_state(self):
        preparacion = self.preparaciones[Sector.Nombre.BARRA]
        self.client.force_authenticate(user=self.usuario_barra)
        started = self.client.patch(
            self.estado_url(preparacion),
            {"estado": PreparacionPedidoSector.Estado.EN_PREPARACION},
            format="json",
        )
        self.assertEqual(started.status_code, status.HTTP_200_OK)
        ready = self.client.patch(
            self.estado_url(preparacion),
            {"estado": PreparacionPedidoSector.Estado.LISTO},
            format="json",
        )
        self.assertEqual(ready.status_code, status.HTTP_200_OK)
        self.assertEqual(
            AvisoRetiro.objects.filter(
                pedido=self.pedido,
                origen=AvisoRetiro.Origen.BARRA,
            ).count(),
            1,
        )

    def test_cocina_cannot_skip_directly_to_listo(self):
        preparacion = self.preparaciones[Sector.Nombre.PIZZA]
        self.client.force_authenticate(user=self.cocina_pizza)

        response = self.client.patch(
            self.estado_url(preparacion),
            {"estado": PreparacionPedidoSector.Estado.LISTO},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        preparacion.refresh_from_db()
        self.assertEqual(
            preparacion.estado,
            PreparacionPedidoSector.Estado.PENDIENTE,
        )

    def test_kitchen_can_advance_only_its_sector_preparation(self):
        for usuario, nombre_sector in [
            (self.cocina_pizza, Sector.Nombre.PIZZA),
            (self.cocina_platos, Sector.Nombre.PLATOS),
        ]:
            with self.subTest(sector=nombre_sector):
                preparacion = self.preparaciones[nombre_sector]
                self.client.force_authenticate(user=usuario)

                response = self.client.patch(
                    self.estado_url(preparacion),
                    {"estado": PreparacionPedidoSector.Estado.EN_PREPARACION},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_200_OK)

                response = self.client.patch(
                    self.estado_url(preparacion),
                    {"estado": PreparacionPedidoSector.Estado.LISTO},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(
                    response.data["estado"],
                    PreparacionPedidoSector.Estado.LISTO,
                )

    def test_workers_cannot_transition_preparation_from_another_sector(self):
        for usuario, otro_sector in [
            (self.usuario_barra, Sector.Nombre.PIZZA),
            (self.usuario_barra, Sector.Nombre.PLATOS),
            (self.cocina_pizza, Sector.Nombre.PLATOS),
            (self.cocina_platos, Sector.Nombre.PIZZA),
        ]:
            with self.subTest(usuario=usuario.username, sector=otro_sector):
                preparacion = self.preparaciones[otro_sector]
                self.client.force_authenticate(user=usuario)

                response = self.client.patch(
                    self.estado_url(preparacion),
                    {"estado": PreparacionPedidoSector.Estado.EN_PREPARACION},
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
                preparacion.refresh_from_db()
                self.assertEqual(
                    preparacion.estado,
                    PreparacionPedidoSector.Estado.PENDIENTE,
                )

    def test_mozo_and_admin_cannot_access_preparation(self):
        for usuario in [self.mozo, self.admin]:
            with self.subTest(rol=usuario.rol):
                self.client.force_authenticate(user=usuario)

                list_response = self.client.get(self.list_url)
                transition_response = self.client.patch(
                    self.estado_url(self.preparaciones[Sector.Nombre.BARRA]),
                    {"estado": PreparacionPedidoSector.Estado.EN_PREPARACION},
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
                self.estado_url(self.preparaciones[Sector.Nombre.BARRA]),
                {"estado": PreparacionPedidoSector.Estado.EN_PREPARACION},
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

    def test_invalid_transitions_do_not_change_persisted_state(self):
        invalid_transitions = [
            (PreparacionPedidoSector.Estado.PENDIENTE, PreparacionPedidoSector.Estado.PENDIENTE),
            (PreparacionPedidoSector.Estado.EN_PREPARACION, PreparacionPedidoSector.Estado.PENDIENTE),
            (PreparacionPedidoSector.Estado.EN_PREPARACION, PreparacionPedidoSector.Estado.EN_PREPARACION),
            (PreparacionPedidoSector.Estado.LISTO, PreparacionPedidoSector.Estado.EN_PREPARACION),
            (PreparacionPedidoSector.Estado.LISTO, PreparacionPedidoSector.Estado.PENDIENTE),
        ]
        self.client.force_authenticate(user=self.usuario_barra)
        preparacion = self.preparaciones[Sector.Nombre.BARRA]

        for estado_inicial, estado_solicitado in invalid_transitions:
            with self.subTest(
                estado_inicial=estado_inicial,
                estado_solicitado=estado_solicitado,
            ):
                PreparacionPedidoSector.objects.filter(pk=preparacion.pk).update(
                    estado=estado_inicial,
                )
                response = self.client.patch(
                    self.estado_url(preparacion),
                    {"estado": estado_solicitado},
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                preparacion.refresh_from_db()
                self.assertEqual(preparacion.estado, estado_inicial)

    def test_cooking_ready_is_derived_from_all_present_cooking_sectors(self):
        pizza = self.preparaciones[Sector.Nombre.PIZZA]
        platos = self.preparaciones[Sector.Nombre.PLATOS]

        pizza.estado = PreparacionPedidoSector.Estado.LISTO
        pizza.save(update_fields=["estado"])
        self.assertFalse(self.pedido.cocina_lista)

        platos.estado = PreparacionPedidoSector.Estado.LISTO
        platos.save(update_fields=["estado"])
        self.assertTrue(self.pedido.cocina_lista)

        self.preparaciones[Sector.Nombre.BARRA].estado = (
            PreparacionPedidoSector.Estado.EN_PREPARACION
        )
        self.preparaciones[Sector.Nombre.BARRA].save(update_fields=["estado"])
        self.assertTrue(self.pedido.cocina_lista)

    def test_cooking_ready_for_orders_with_only_one_cooking_sector(self):
        for numero_mesa, sector in [
            (31, self.pizza),
            (32, self.platos),
        ]:
            with self.subTest(sector=sector.nombre):
                mesa = Mesa.objects.create(numero=numero_mesa)
                cuenta = Cuenta.objects.create(mesa=mesa)
                pedido = Pedido.objects.create(
                    mesa=mesa,
                    mozo=self.mozo,
                    cuenta=cuenta,
                )
                producto = self.detalles[sector.nombre][0].producto
                DetallePedido.objects.create(
                    pedido=pedido,
                    producto=producto,
                    cantidad=1,
                )
                preparacion = PreparacionPedidoSector.objects.create(
                    pedido=pedido,
                    sector=sector,
                )

                self.assertEqual(
                    list(
                        pedido.preparaciones_sectoriales.values_list(
                            "sector__nombre",
                            flat=True,
                        )
                    ),
                    [sector.nombre],
                )
                preparacion.estado = PreparacionPedidoSector.Estado.LISTO
                preparacion.save(update_fields=["estado"])
                self.assertTrue(pedido.cocina_lista)

    def test_bar_preparation_does_not_participate_in_cooking_ready(self):
        self.preparaciones[Sector.Nombre.PIZZA].delete()
        self.preparaciones[Sector.Nombre.PLATOS].delete()
        self.preparaciones[Sector.Nombre.BARRA].estado = (
            PreparacionPedidoSector.Estado.LISTO
        )
        self.preparaciones[Sector.Nombre.BARRA].save(update_fields=["estado"])
        self.assertFalse(self.pedido.cocina_lista)

    def test_cooking_preparations_change_independently(self):
        pizza = self.preparaciones[Sector.Nombre.PIZZA]
        platos = self.preparaciones[Sector.Nombre.PLATOS]
        self.client.force_authenticate(user=self.cocina_pizza)

        response = self.client.patch(
            self.estado_url(pizza),
            {"estado": PreparacionPedidoSector.Estado.EN_PREPARACION},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        platos.refresh_from_db()
        self.assertEqual(platos.estado, PreparacionPedidoSector.Estado.PENDIENTE)

        response = self.client.patch(
            self.estado_url(pizza),
            {"estado": PreparacionPedidoSector.Estado.LISTO},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        platos.refresh_from_db()
        self.assertEqual(platos.estado, PreparacionPedidoSector.Estado.PENDIENTE)
        self.assertFalse(self.pedido.cocina_lista)

        self.client.force_authenticate(user=self.cocina_platos)
        for estado in [
            PreparacionPedidoSector.Estado.EN_PREPARACION,
            PreparacionPedidoSector.Estado.LISTO,
        ]:
            response = self.client.patch(
                self.estado_url(platos),
                {"estado": estado},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

        pizza.refresh_from_db()
        platos.refresh_from_db()
        self.assertEqual(pizza.estado, PreparacionPedidoSector.Estado.LISTO)
        self.assertEqual(platos.estado, PreparacionPedidoSector.Estado.LISTO)
        self.assertTrue(self.pedido.cocina_lista)

    def test_preparation_uses_only_accepted_cancellations_and_keeps_original_quantity(self):
        detalle = self.detalles[Sector.Nombre.BARRA][0]
        detalle.cantidad = 6
        detalle.save(update_fields=["cantidad"])
        for estado, cantidad in [
            (SolicitudCancelacionDetalle.Estado.ACEPTADA, 2),
            (SolicitudCancelacionDetalle.Estado.PENDIENTE, 1),
            (SolicitudCancelacionDetalle.Estado.RECHAZADA, 1),
        ]:
            SolicitudCancelacionDetalle.objects.create(
                detalle=detalle,
                solicitante=self.mozo,
                clave_idempotencia=uuid4(),
                cantidad=cantidad,
                motivo=f"Cancelación {estado.lower()}",
                estado=estado,
                resuelto_por=(
                    self.usuario_barra
                    if estado != SolicitudCancelacionDetalle.Estado.PENDIENTE
                    else None
                ),
            )
        self.client.force_authenticate(user=self.usuario_barra)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        barra = self.preparaciones_visibles(response)[Sector.Nombre.BARRA]
        product = next(
            item for item in barra["detalles"] if item["id"] == detalle.pk
        )
        self.assertEqual(product["cantidad"], 4)
        self.assertEqual(product["cantidad_original"], 6)

        SolicitudCancelacionDetalle.objects.create(
            detalle=detalle,
            solicitante=self.mozo,
            clave_idempotencia=uuid4(),
            cantidad=1,
            motivo="Otra cancelación aceptada",
            estado=SolicitudCancelacionDetalle.Estado.ACEPTADA,
            resuelto_por=self.usuario_barra,
        )
        updated = self.client.get(self.list_url)
        product = next(
            item
            for item in self.preparaciones_visibles(updated)[
                Sector.Nombre.BARRA
            ]["detalles"]
            if item["id"] == detalle.pk
        )
        self.assertEqual(product["cantidad"], 3)

    def test_cancelar_todo_un_origen_oculta_tarea_y_no_genera_aviso_vacio(self):
        detalles_barra = self.detalles[Sector.Nombre.BARRA]
        for detalle in detalles_barra:
            SolicitudCancelacionDetalle.objects.create(
                detalle=detalle,
                solicitante=self.mozo,
                clave_idempotencia=uuid4(),
                cantidad=detalle.cantidad,
                motivo="Cancelación total",
                estado=SolicitudCancelacionDetalle.Estado.ACEPTADA,
                resuelto_por=self.usuario_barra,
            )

        self.client.force_authenticate(user=self.usuario_barra)
        barra_response = self.client.get(self.list_url)
        self.assertEqual(barra_response.status_code, status.HTTP_200_OK)
        self.assertEqual(barra_response.data, [])
        stale_transition = self.client.patch(
            self.estado_url(self.preparaciones[Sector.Nombre.BARRA]),
            {"estado": PreparacionPedidoSector.Estado.LISTO},
            format="json",
        )
        self.assertEqual(stale_transition.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(
            AvisoRetiro.objects.filter(
                pedido=self.pedido,
                origen=AvisoRetiro.Origen.BARRA,
            ).exists(),
        )

        self.client.force_authenticate(user=self.cocina_pizza)
        pizza_response = self.client.get(self.list_url)
        self.assertEqual(
            set(self.preparaciones_visibles(pizza_response)),
            {Sector.Nombre.PIZZA},
        )

    def test_preparaciones_se_ordenan_por_fecha_y_id_sin_depender_del_estado(self):
        pedidos = [self.pedido]
        for numero in [31, 32]:
            mesa = Mesa.objects.create(numero=numero)
            cuenta = Cuenta.objects.create(mesa=mesa)
            pedido = Pedido.objects.create(
                mesa=mesa,
                cuenta=cuenta,
                mozo=self.mozo,
            )
            producto = Producto.objects.create(
                nombre=f"Barra orden {numero}",
                precio=Decimal("100.00"),
                categoria=self.categoria,
                sector_destino=self.barra,
            )
            DetallePedido.objects.create(
                pedido=pedido,
                producto=producto,
                cantidad=1,
            )
            PreparacionPedidoSector.objects.create(
                pedido=pedido,
                sector=self.barra,
            )
            pedidos.append(pedido)

        base = timezone.now()
        for pedido, seconds in zip(pedidos, [20, 0, 10]):
            Pedido.objects.filter(pk=pedido.pk).update(
                fecha_creacion=base + timedelta(seconds=seconds),
            )
        self.preparaciones[Sector.Nombre.BARRA].estado = (
            PreparacionPedidoSector.Estado.EN_PREPARACION
        )
        self.preparaciones[Sector.Nombre.BARRA].save(update_fields=["estado"])
        self.client.force_authenticate(user=self.usuario_barra)

        response = self.client.get(self.list_url)

        self.assertEqual(
            [item["pedido"] for item in response.data],
            [pedidos[1].pk, pedidos[2].pk, pedidos[0].pk],
        )


class RetiroPedidoAPITests(APITestCase):
    def setUp(self):
        self.salon = Sector.objects.create(nombre=Sector.Nombre.SALON)
        self.barra = Sector.objects.create(nombre=Sector.Nombre.BARRA)
        self.pizza = Sector.objects.create(nombre=Sector.Nombre.PIZZA)
        self.platos = Sector.objects.create(nombre=Sector.Nombre.PLATOS)
        self.mozo = User.objects.create_user(
            username="mozo_retiro",
            password="test-password-123",
            first_name="Ana",
            last_name="Gomez",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )
        self.otro_mozo = User.objects.create_user(
            username="mozo_sin_nombre",
            password="test-password-123",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )
        self.usuario_barra = User.objects.create_user(
            username="barra_retiro",
            password="test-password-123",
            rol=User.Rol.BARRA,
            sector=self.barra,
        )
        self.otra_barra = User.objects.create_user(
            username="otra_barra_retiro",
            password="test-password-123",
            rol=User.Rol.BARRA,
            sector=self.barra,
        )
        self.cocina_pizza = User.objects.create_user(
            username="cocina_pizza_retiro",
            password="test-password-123",
            rol=User.Rol.COCINA,
            sector=self.pizza,
        )
        self.cocina_platos = User.objects.create_user(
            username="cocina_platos_retiro",
            password="test-password-123",
            rol=User.Rol.COCINA,
            sector=self.platos,
        )
        self.admin = User.objects.create_user(
            username="admin_retiro",
            password="test-password-123",
            rol=User.Rol.ADMINISTRADOR,
        )
        self.categoria = Categoria.objects.create(nombre="Productos retiro")
        self.control_url = reverse("barra-retiros-list")
        self.avisos_url = reverse("aviso-retiro-list")

    def crear_pedido(
        self,
        sectores,
        numero_mesa,
        mozo=None,
        zona=Mesa.Zona.SALON,
    ):
        mesa = Mesa.objects.create(numero=numero_mesa, zona=zona)
        cuenta = Cuenta.objects.create(mesa=mesa)
        pedido = Pedido.objects.create(
            mesa=mesa,
            mozo=mozo or self.mozo,
            cuenta=cuenta,
        )
        preparaciones = {}
        for sector in sectores:
            producto = Producto.objects.create(
                nombre=f"Producto {sector.nombre} mesa {numero_mesa}",
                precio=Decimal("1000.00"),
                categoria=self.categoria,
                sector_destino=sector,
            )
            DetallePedido.objects.create(
                pedido=pedido,
                producto=producto,
                cantidad=1,
            )
            preparaciones[sector.nombre] = PreparacionPedidoSector.objects.create(
                pedido=pedido,
                sector=sector,
            )
        return pedido, preparaciones

    def estado_url(self, preparacion):
        return reverse(
            "detalle-preparacion-estado",
            args=[preparacion.pk],
        )

    def confirmacion_url(self, pedido):
        return reverse("barra-retiro-confirmar", args=[pedido.pk])

    def aviso_leido_url(self, aviso):
        return reverse("aviso-retiro-marcar-leido", args=[aviso.pk])

    def marcar_lista(self, preparacion):
        usuarios = {
            Sector.Nombre.BARRA: self.usuario_barra,
            Sector.Nombre.PIZZA: self.cocina_pizza,
            Sector.Nombre.PLATOS: self.cocina_platos,
        }
        self.client.force_authenticate(
            user=usuarios[preparacion.sector.nombre],
        )
        estados = (
            [PreparacionPedidoSector.Estado.LISTO]
            if preparacion.sector.nombre == Sector.Nombre.BARRA
            else [
                PreparacionPedidoSector.Estado.EN_PREPARACION,
                PreparacionPedidoSector.Estado.LISTO,
            ]
        )
        for estado in estados:
            response = self.client.patch(
                self.estado_url(preparacion),
                {"estado": estado},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def confirmar_retiro(self, pedido, usuario=None):
        self.client.force_authenticate(user=usuario or self.usuario_barra)
        return self.client.post(self.confirmacion_url(pedido), format="json")

    def test_barra_only_ready_creates_only_barra_notice(self):
        pedido, preparaciones = self.crear_pedido(
            [self.barra],
            47,
            zona=Mesa.Zona.VEREDA_A,
        )

        self.marcar_lista(preparaciones[Sector.Nombre.BARRA])

        pedido.refresh_from_db()
        avisos = list(AvisoRetiro.objects.filter(pedido=pedido))
        self.assertEqual(len(avisos), 1)
        aviso = avisos[0]
        self.assertEqual(aviso.origen, AvisoRetiro.Origen.BARRA)
        self.assertEqual(aviso.generado_por, self.usuario_barra)
        self.assertEqual(aviso.destinatario, self.mozo)
        self.assertEqual(aviso.mensaje, "Vereda A · Mesa 47 — Barra lista")
        self.assertNotEqual(pedido.mesa_id, pedido.mesa.numero)

    def test_kitchen_only_order_can_be_manually_confirmed_while_preparations_are_pending(self):
        pedido, preparaciones = self.crear_pedido(
            [self.pizza, self.platos],
            48,
        )

        pedido.refresh_from_db()
        self.assertFalse(pedido.cocina_lista)
        self.assertFalse(AvisoRetiro.objects.filter(pedido=pedido).exists())

        self.client.force_authenticate(user=self.usuario_barra)
        response = self.client.get(self.control_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]["mesa"], 48)
        self.assertEqual(response.data[0]["zona"], Mesa.Zona.SALON)
        self.assertEqual(response.data[0]["identificacion"], "Salón · Mesa 48")
        self.assertEqual(response.data[0]["pedido"], pedido.pk)
        self.assertEqual(response.data[0]["mozo_id"], self.mozo.pk)
        self.assertEqual(response.data[0]["mozo_nombre"], "Ana Gomez")
        self.assertEqual(
            response.data[0]["productos_cocina"],
            [
                {
                    "producto_nombre": f"Producto {Sector.Nombre.PIZZA} mesa 48",
                    "cantidad": 1,
                    "sector": Sector.Nombre.PIZZA,
                },
                {
                    "producto_nombre": f"Producto {Sector.Nombre.PLATOS} mesa 48",
                    "cantidad": 1,
                    "sector": Sector.Nombre.PLATOS,
                },
            ],
        )
        self.assertNotIn("precio_unitario", response.data[0])
        self.assertNotIn(
            "precio_unitario",
            response.data[0]["productos_cocina"][0],
        )
        confirmation = self.confirmar_retiro(pedido)
        self.assertEqual(confirmation.status_code, status.HTTP_200_OK)
        for preparacion in preparaciones.values():
            preparacion.refresh_from_db()
            self.assertEqual(
                preparacion.estado,
                PreparacionPedidoSector.Estado.PENDIENTE,
            )
        self.assertEqual(self.client.get(self.control_url).data, [])
        aviso = AvisoRetiro.objects.get(pedido=pedido)
        self.assertEqual(aviso.origen, AvisoRetiro.Origen.COCINA)
        self.assertEqual(aviso.mensaje, "Salón · Mesa 48 — Cocina lista")

    def test_mixed_barra_can_be_ready_while_kitchen_is_pending(self):
        pedido, preparaciones = self.crear_pedido(
            [self.barra, self.pizza],
            49,
        )

        self.client.force_authenticate(user=self.usuario_barra)
        pending_items = self.client.get(self.control_url)
        self.assertEqual(len(pending_items.data), 1)
        self.assertEqual(
            pending_items.data[0]["productos_cocina"][0]["sector"],
            Sector.Nombre.PIZZA,
        )

        self.marcar_lista(preparaciones[Sector.Nombre.BARRA])
        self.assertTrue(
            AvisoRetiro.objects.filter(
                pedido=pedido,
                origen=AvisoRetiro.Origen.BARRA,
            ).exists(),
        )

    def test_recepcion_usa_cantidad_vigente_y_no_oculta_otro_origen(self):
        pedido, _ = self.crear_pedido([self.barra, self.pizza], 105)
        detalle_barra = pedido.detalles.get(
            sector_destino=self.barra,
        )
        detalle_barra.cantidad = 6
        detalle_barra.save(update_fields=["cantidad"])
        for estado, cantidad in [
            (SolicitudCancelacionDetalle.Estado.ACEPTADA, 2),
            (SolicitudCancelacionDetalle.Estado.PENDIENTE, 1),
            (SolicitudCancelacionDetalle.Estado.RECHAZADA, 1),
        ]:
            SolicitudCancelacionDetalle.objects.create(
                detalle=detalle_barra,
                solicitante=self.mozo,
                clave_idempotencia=uuid4(),
                cantidad=cantidad,
                motivo=f"Cancelación {estado.lower()}",
                estado=estado,
                resuelto_por=(
                    self.usuario_barra
                    if estado != SolicitudCancelacionDetalle.Estado.PENDIENTE
                    else None
                ),
            )
        self.client.force_authenticate(user=self.usuario_barra)

        response = self.client.get(self.control_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        order = next(item for item in response.data if item["pedido"] == pedido.pk)
        self.assertEqual(
            order["productos_cocina"],
            [
                {
                    "producto_nombre": f"Producto {Sector.Nombre.PIZZA} mesa 105",
                    "cantidad": 1,
                    "sector": Sector.Nombre.PIZZA,
                },
            ],
        )

    def test_cocina_totalmente_cancelada_no_aparece_ni_admite_aviso(self):
        pedido, _ = self.crear_pedido([self.pizza, self.platos], 106)
        for detalle in pedido.detalles.all():
            SolicitudCancelacionDetalle.objects.create(
                detalle=detalle,
                solicitante=self.mozo,
                clave_idempotencia=uuid4(),
                cantidad=detalle.cantidad,
                motivo="Cancelación total",
                estado=SolicitudCancelacionDetalle.Estado.ACEPTADA,
                resuelto_por=self.usuario_barra,
            )

        self.client.force_authenticate(user=self.cocina_pizza)
        preparation_response = self.client.get(
            reverse("detalle-preparacion-list"),
        )
        self.assertEqual(preparation_response.status_code, status.HTTP_200_OK)
        self.assertEqual(preparation_response.data, [])

        self.client.force_authenticate(user=self.usuario_barra)
        response = self.client.get(self.control_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            any(item["pedido"] == pedido.pk for item in response.data),
        )
        confirmation = self.confirmar_retiro(pedido)
        self.assertEqual(confirmation.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            AvisoRetiro.objects.filter(
                pedido=pedido,
                origen=AvisoRetiro.Origen.COCINA,
            ).exists(),
        )

    def test_recepciones_se_ordenan_por_llegada_y_id_estable(self):
        pedidos = []
        preparaciones = {}
        for numero in [107, 108, 109]:
            pedido, preparaciones_pedido = self.crear_pedido(
                [self.pizza],
                numero,
            )
            pedidos.append(pedido)
            preparaciones[pedido.pk] = preparaciones_pedido

        base = timezone.now()
        for pedido, seconds in zip(pedidos, [20, 0, 10]):
            Pedido.objects.filter(pk=pedido.pk).update(
                fecha_creacion=base + timedelta(seconds=seconds),
            )
        self.client.force_authenticate(user=self.usuario_barra)

        response = self.client.get(self.control_url)

        self.assertEqual(
            [item["pedido"] for item in response.data],
            [pedidos[1].pk, pedidos[2].pk, pedidos[0].pk],
        )
        preparacion_cocina = preparaciones[pedidos[1].pk][
            Sector.Nombre.PIZZA
        ]
        preparacion_cocina.refresh_from_db()
        self.assertEqual(
            preparacion_cocina.estado,
            PreparacionPedidoSector.Estado.PENDIENTE,
        )

        updated = self.client.get(self.control_url)
        self.assertEqual(
            [item["pedido"] for item in updated.data],
            [pedidos[1].pk, pedidos[2].pk, pedidos[0].pk],
        )

    def test_kitchen_only_products_from_both_sectors_do_not_require_digital_readiness(self):
        pedido, preparaciones = self.crear_pedido(
            [self.pizza, self.platos],
            50,
        )

        response = self.confirmar_retiro(pedido)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        pedido.refresh_from_db()
        self.assertFalse(pedido.cocina_lista)
        self.assertEqual(AvisoRetiro.objects.filter(pedido=pedido).count(), 1)
        self.assertTrue(
            all(
                preparacion.estado == PreparacionPedidoSector.Estado.PENDIENTE
                for preparacion in preparaciones.values()
            ),
        )

    def test_mixed_order_can_confirm_cocina_while_barra_is_pending(self):
        pedido, preparaciones = self.crear_pedido([self.barra, self.pizza], 51)

        response = self.confirmar_retiro(pedido)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            AvisoRetiro.objects.get(pedido=pedido).origen,
            AvisoRetiro.Origen.COCINA,
        )
        self.assertEqual(len(self.client.get(self.control_url).data), 0)
        barra_tasks = self.client.get(
            reverse("detalle-preparacion-list"),
        ).data
        self.assertEqual(len(barra_tasks), 1)
        self.assertEqual(barra_tasks[0]["estado"], "PENDIENTE")
        self.assertEqual(
            preparaciones[Sector.Nombre.BARRA].estado,
            PreparacionPedidoSector.Estado.PENDIENTE,
        )

    def test_both_origins_are_independent_in_either_completion_order(self):
        barra_first, barra_first_preparations = self.crear_pedido(
            [self.barra, self.pizza],
            63,
        )
        self.marcar_lista(barra_first_preparations[Sector.Nombre.BARRA])
        self.assertEqual(
            len(self.client.get(self.control_url).data),
            1,
            "Barra listo no debe retirar la tarea de recepción de Cocina.",
        )
        self.assertEqual(
            self.confirmar_retiro(barra_first).status_code,
            status.HTTP_200_OK,
        )

        cocina_first, cocina_first_preparations = self.crear_pedido(
            [self.barra, self.platos],
            64,
        )
        self.assertEqual(
            self.confirmar_retiro(cocina_first).status_code,
            status.HTTP_200_OK,
        )
        self.client.force_authenticate(user=self.usuario_barra)
        barra_tasks = self.client.get(
            reverse("detalle-preparacion-list"),
        ).data
        self.assertTrue(any(
            task["pedido"] == cocina_first.pk
            and task["estado"] == "PENDIENTE"
            for task in barra_tasks
        ))
        self.marcar_lista(cocina_first_preparations[Sector.Nombre.BARRA])

        for pedido in [barra_first, cocina_first]:
            self.assertEqual(
                set(
                    AvisoRetiro.objects.filter(pedido=pedido).values_list(
                        "origen",
                        flat=True,
                    ),
                ),
                {AvisoRetiro.Origen.BARRA, AvisoRetiro.Origen.COCINA},
            )
            self.assertEqual(
                AvisoRetiro.objects.filter(pedido=pedido).count(),
                2,
            )
        self.client.force_authenticate(user=self.mozo)
        notices = self.client.get(self.avisos_url).data
        self.assertEqual(len(notices), 4)
        self.assertEqual(
            {
                (notice["pedido"], notice["origen"])
                for notice in notices
            },
            {
                (pedido.pk, origin)
                for pedido in [barra_first, cocina_first]
                for origin in [
                    AvisoRetiro.Origen.BARRA,
                    AvisoRetiro.Origen.COCINA,
                ]
            },
        )
        self.assertIn(
            "Salón · Mesa 63 — Barra lista",
            {notice["mensaje"] for notice in notices},
        )
        self.assertIn(
            "Salón · Mesa 63 — Cocina lista",
            {notice["mensaje"] for notice in notices},
        )

    def test_database_enforces_one_notice_per_origin_and_one_ambiguous_notice(self):
            pedido, _ = self.crear_pedido([self.barra, self.pizza], 66)
            AvisoRetiro.objects.create(
                pedido=pedido,
                origen=AvisoRetiro.Origen.BARRA,
                destinatario=self.mozo,
                mensaje="Barra",
            )
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    AvisoRetiro.objects.create(
                        pedido=pedido,
                        origen=AvisoRetiro.Origen.BARRA,
                        destinatario=self.mozo,
                        mensaje="Duplicado",
                    )
            AvisoRetiro.objects.create(
                pedido=pedido,
                origen=None,
                destinatario=self.mozo,
                mensaje="Origen histórico desconocido",
            )
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    AvisoRetiro.objects.create(
                        pedido=pedido,
                        origen=None,
                        destinatario=self.mozo,
                        mensaje="Duplicado ambiguo",
                    )

    def test_kitchen_only_order_does_not_require_digital_preparation_records(self):
        pedido, _ = self.crear_pedido([self.pizza], 52)
        pedido.preparaciones_sectoriales.all().delete()

        response = self.confirmar_retiro(pedido)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(AvisoRetiro.objects.filter(pedido=pedido).count(), 1)

    def test_order_without_preparations_cannot_be_enabled_or_listed(self):
        pedido, _ = self.crear_pedido([], 53)

        response = self.confirmar_retiro(pedido)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.client.get(self.control_url).data, [])
        self.assertFalse(AvisoRetiro.objects.filter(pedido=pedido).exists())

    def test_repeated_confirmation_preserves_original_bar_user_and_timestamp(self):
        pedido, _ = self.crear_pedido([self.pizza], 54)
        first_response = self.confirmar_retiro(pedido)
        self.assertEqual(first_response.status_code, status.HTTP_200_OK)

        aviso = AvisoRetiro.objects.get(
            pedido=pedido,
            origen=AvisoRetiro.Origen.COCINA,
        )
        fecha_aviso_original = aviso.fecha_creacion
        self.assertEqual(self.client.get(self.control_url).data, [])
        otro_usuario = self.otra_barra
        second_response = self.confirmar_retiro(pedido, otro_usuario)

        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        aviso.refresh_from_db()
        self.assertEqual(aviso.generado_por, self.usuario_barra)
        self.assertEqual(aviso.fecha_creacion, fecha_aviso_original)
        self.assertEqual(AvisoRetiro.objects.filter(pedido=pedido).count(), 1)

    def test_confirming_after_account_closes_does_not_revalidate_order_creation(self):
        pedido, preparaciones = self.crear_pedido([self.pizza], 55)
        self.marcar_lista(preparaciones[Sector.Nombre.PIZZA])
        pedido.cuenta.cerrar()

        response = self.confirmar_retiro(pedido)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_notice_failure_rolls_back_retiro_habilitation(self):
        pedido, preparaciones = self.crear_pedido([self.pizza], 56)
        self.marcar_lista(preparaciones[Sector.Nombre.PIZZA])
        self.client.force_authenticate(user=self.usuario_barra)

        with patch.object(AvisoRetiro, "save", side_effect=RuntimeError("fallo")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.confirmacion_url(pedido), format="json")

        self.assertFalse(AvisoRetiro.objects.filter(pedido=pedido).exists())

    def test_bar_control_uses_username_when_waiter_has_no_name(self):
        pedido, preparaciones = self.crear_pedido(
            [self.pizza],
            57,
            mozo=self.otro_mozo,
        )
        self.marcar_lista(preparaciones[Sector.Nombre.PIZZA])
        self.client.force_authenticate(user=self.usuario_barra)

        response = self.client.get(self.control_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        pedido_data = next(
            item for item in response.data if item["pedido"] == pedido.pk
        )
        self.assertEqual(pedido_data["mozo_id"], self.otro_mozo.pk)
        self.assertEqual(pedido_data["mozo_nombre"], self.otro_mozo.username)

    def test_notice_list_is_mozo_only_and_filtered_by_authenticated_user(self):
        primer_pedido, primer_preparaciones = self.crear_pedido(
            [self.pizza],
            57,
            mozo=self.mozo,
        )
        segundo_pedido, segundo_preparaciones = self.crear_pedido(
            [self.platos],
            58,
            mozo=self.otro_mozo,
        )
        self.marcar_lista(primer_preparaciones[Sector.Nombre.PIZZA])
        self.confirmar_retiro(primer_pedido)
        self.marcar_lista(segundo_preparaciones[Sector.Nombre.PLATOS])
        self.confirmar_retiro(segundo_pedido)

        self.client.force_authenticate(user=self.mozo)
        response = self.client.get(
            self.avisos_url,
            {"destinatario": self.otro_mozo.pk},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["pedido"], primer_pedido.pk)
        self.assertEqual(response.data[0]["origen"], AvisoRetiro.Origen.COCINA)
        self.assertEqual(
            response.data[0]["mensaje"],
            "Salón · Mesa 57 — Cocina lista",
        )
        self.assertFalse(response.data[0]["leido"])

        self.client.force_authenticate(user=self.otro_mozo)
        response = self.client.get(self.avisos_url)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["pedido"], segundo_pedido.pk)
        self.assertEqual(
            response.data[0]["mensaje"],
            "Salón · Mesa 58 — Cocina lista",
        )
        self.assertFalse(response.data[0]["leido"])

    def test_reading_one_origin_notice_does_not_mark_the_other_as_read(self):
        pedido, preparaciones = self.crear_pedido([self.barra, self.pizza], 61)
        self.marcar_lista(preparaciones[Sector.Nombre.BARRA])
        self.confirmar_retiro(pedido)

        self.client.force_authenticate(user=self.mozo)
        notices = self.client.get(self.avisos_url).data
        self.assertEqual(len(notices), 2)
        barra_notice = AvisoRetiro.objects.get(
            pedido=pedido,
            origen=AvisoRetiro.Origen.BARRA,
        )
        marked = self.client.post(self.aviso_leido_url(barra_notice), format="json")
        self.assertEqual(marked.status_code, status.HTTP_200_OK)
        self.assertEqual(marked.data, {"actualizados": 1})
        self.client.force_authenticate(user=self.otro_mozo)
        self.assertEqual(
            self.client.post(
                self.aviso_leido_url(
                    AvisoRetiro.objects.get(
                        pedido=pedido,
                        origen=AvisoRetiro.Origen.COCINA,
                    ),
                ),
                format="json",
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )

        self.client.force_authenticate(user=self.mozo)
        notices = self.client.get(self.avisos_url).data
        self.assertTrue(next(
            notice for notice in notices
            if notice["origen"] == AvisoRetiro.Origen.BARRA
        )["leido"])
        self.assertFalse(next(
            notice for notice in notices
            if notice["origen"] == AvisoRetiro.Origen.COCINA
        )["leido"])
        barra_notice.refresh_from_db()
        self.assertIsNotNone(barra_notice.leido_en)
        self.assertIsNone(
            AvisoRetiro.objects.get(
                pedido=pedido,
                origen=AvisoRetiro.Origen.COCINA,
            ).leido_en,
        )

    def test_retirement_notice_routes_are_mozo_only(self):
        pedido, _ = self.crear_pedido([self.pizza], 59)
        self.assertEqual(
            self.confirmar_retiro(pedido).status_code,
            status.HTTP_200_OK,
        )
        aviso = AvisoRetiro.objects.get(pedido=pedido)
        for usuario in [
            self.usuario_barra,
            self.cocina_pizza,
            self.admin,
            None,
        ]:
            with self.subTest(usuario=usuario):
                self.client.force_authenticate(user=usuario)
                for response in [
                    self.client.get(self.avisos_url),
                    self.client.post(
                        self.aviso_leido_url(
                            aviso,
                        ),
                        format="json",
                    ),
                ]:
                    self.assertIn(
                        response.status_code,
                        {
                            status.HTTP_401_UNAUTHORIZED,
                            status.HTTP_403_FORBIDDEN,
                        },
                    )

    def test_bar_control_and_confirmation_are_exclusive_to_barra(self):
        pedido, preparaciones = self.crear_pedido([self.pizza], 60)
        self.marcar_lista(preparaciones[Sector.Nombre.PIZZA])

        for usuario in [self.mozo, self.cocina_pizza, self.admin]:
            with self.subTest(usuario=usuario.username):
                self.client.force_authenticate(user=usuario)
                self.assertEqual(
                    self.client.get(self.control_url).status_code,
                    status.HTTP_403_FORBIDDEN,
                )
                self.assertEqual(
                    self.client.post(
                        self.confirmacion_url(pedido),
                        format="json",
                    ).status_code,
                    status.HTTP_403_FORBIDDEN,
                )

        self.client.force_authenticate(user=self.usuario_barra)
        self.assertEqual(
            self.client.get(self.avisos_url).status_code,
            status.HTTP_403_FORBIDDEN,
        )


class AvisoRetiroOriginMigrationTests(TransactionTestCase):
    migrate_from = [("pedidos", "0012_lecturanotificacioncancelacion")]
    migrate_to = [
        ("pedidos", "0013_remove_avisoretiro_unique_aviso_retiro_por_pedido_and_more"),
    ]

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        SectorHistorico = old_apps.get_model("usuarios", "Sector")
        UserHistorico = old_apps.get_model("usuarios", "User")
        MesaHistorica = old_apps.get_model("pedidos", "Mesa")
        CuentaHistorica = old_apps.get_model("pedidos", "Cuenta")
        PedidoHistorico = old_apps.get_model("pedidos", "Pedido")
        AvisoHistorico = old_apps.get_model("pedidos", "AvisoRetiro")
        CategoriaHistorica = old_apps.get_model("productos", "Categoria")
        ProductoHistorico = old_apps.get_model("productos", "Producto")
        DetalleHistorico = old_apps.get_model("pedidos", "DetallePedido")

        barra = SectorHistorico.objects.create(nombre="BARRA")
        pizza = SectorHistorico.objects.create(nombre="PIZZA")
        mozo = UserHistorico.objects.create(
            username="mozo_aviso_historico",
            password="",
            rol="MOZO",
        )
        categoria = CategoriaHistorica.objects.create(nombre="Histórico")
        leido_en = timezone.now()

        def crear_aviso(numero_mesa, sectores):
            mesa = MesaHistorica.objects.create(numero=numero_mesa)
            cuenta = CuentaHistorica.objects.create(mesa=mesa)
            pedido = PedidoHistorico.objects.create(
                mesa=mesa,
                mozo=mozo,
                cuenta=cuenta,
                retiro_habilitado_en=leido_en,
                retiro_habilitado_por=mozo,
            )
            for sector in sectores:
                producto = ProductoHistorico.objects.create(
                    nombre=f"Histórico {numero_mesa} {sector.nombre}",
                    precio=Decimal("100.00"),
                    categoria=categoria,
                    sector_destino=sector,
                )
                DetalleHistorico.objects.create(
                    pedido=pedido,
                    producto=producto,
                    sector_destino=sector,
                    cantidad=1,
                    precio_unitario=Decimal("100.00"),
                )
            AvisoHistorico.objects.create(
                pedido=pedido,
                destinatario=mozo,
                mensaje=f"Salón · Mesa {numero_mesa}",
                leido_en=leido_en,
            )
            return pedido.pk

        self.pedido_barra = crear_aviso(1, [barra])
        self.pedido_mixto = crear_aviso(2, [barra, pizza])
        self.pedido_ambiguo = crear_aviso(3, [])
        self.fecha_lectura = leido_en

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        migrated_apps = executor.loader.project_state(self.migrate_to).apps
        AvisoActual = migrated_apps.get_model("pedidos", "AvisoRetiro")
        self.avisos = {
            aviso.pedido_id: aviso
            for aviso in AvisoActual.objects.all()
        }

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_migration_preserves_notice_data_and_classifies_only_proven_origins(self):
        self.assertEqual(len(self.avisos), 3)
        barra = self.avisos[self.pedido_barra]
        self.assertEqual(barra.origen, "BARRA")
        self.assertEqual(barra.mensaje, "Salón · Mesa 1 — Barra lista")

        cocina = self.avisos[self.pedido_mixto]
        self.assertEqual(cocina.origen, "COCINA")
        self.assertEqual(cocina.mensaje, "Salón · Mesa 2 — Cocina lista")

        ambiguo = self.avisos[self.pedido_ambiguo]
        self.assertIsNone(ambiguo.origen)
        self.assertEqual(ambiguo.mensaje, "Salón · Mesa 3")
        for aviso in self.avisos.values():
            self.assertEqual(aviso.destinatario_id, barra.destinatario_id)
            self.assertEqual(aviso.generado_por_id, barra.destinatario_id)
            self.assertEqual(aviso.leido_en, self.fecha_lectura)


class CuentaConsumoAPITests(APITestCase):
    def setUp(self):
        self.salon = Sector.objects.create(nombre=Sector.Nombre.SALON)
        self.barra = Sector.objects.create(nombre=Sector.Nombre.BARRA)
        self.pizza = Sector.objects.create(nombre=Sector.Nombre.PIZZA)
        self.mozo = User.objects.create_user(
            username="consumo_mozo",
            password="test-password",
            first_name="Ana",
            last_name="Gomez",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )
        self.otro_mozo = User.objects.create_user(
            username="consumo_otro_mozo",
            password="test-password",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )
        self.usuario_barra = User.objects.create_user(
            username="consumo_barra",
            password="test-password",
            rol=User.Rol.BARRA,
            sector=self.barra,
        )
        self.admin = User.objects.create_user(
            username="consumo_admin",
            password="test-password",
            rol=User.Rol.ADMINISTRADOR,
        )
        self.cocina = User.objects.create_user(
            username="consumo_cocina",
            password="test-password",
            rol=User.Rol.COCINA,
            sector=self.pizza,
        )
        self.mesa = Mesa.objects.create(
            numero=71,
            zona=Mesa.Zona.VEREDA_A,
        )
        self.cuenta = Cuenta.objects.create(
            mesa=self.mesa,
            mozo_responsable=self.mozo,
        )
        self.categoria = Categoria.objects.create(nombre="Bebidas consumo")
        self.producto = Producto.objects.create(
            nombre="Limonada consumo",
            precio=Decimal("1200.00"),
            categoria=self.categoria,
            sector_destino=self.barra,
        )
        self.url = reverse("cuenta-consumo", args=[self.cuenta.pk])

    def crear_pedido(self, autor, cantidad=1, producto=None):
        pedido = Pedido.objects.create(
            mesa=self.mesa,
            cuenta=self.cuenta,
            mozo=self.mozo,
            creado_por=autor,
        )
        DetallePedido.objects.create(
            pedido=pedido,
            producto=producto or self.producto,
            cantidad=cantidad,
        )
        if autor == self.usuario_barra:
            AvisoCargaBarra.objects.create(
                pedido=pedido,
                destinatario=self.mozo,
            )
        return pedido

    def consultar_como(self, usuario):
        self.client.force_authenticate(user=usuario)
        return self.client.get(self.url)

    def test_suma_pedidos_de_mozo_y_barra_usando_precios_guardados(self):
        pedido_mozo = self.crear_pedido(self.mozo, cantidad=2)
        self.producto.precio = Decimal("1900.00")
        self.producto.save()
        pedido_barra = self.crear_pedido(self.usuario_barra, cantidad=3)

        response = self.consultar_como(self.mozo)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.cuenta.pk)
        self.assertEqual(response.data["mesa"], self.mesa.pk)
        self.assertEqual(
            response.data["mesa_identificacion"],
            "Vereda A · Mesa 71",
        )
        self.assertEqual(
            response.data["responsable"],
            {"id": self.mozo.pk, "nombre": "Ana Gomez"},
        )
        self.assertEqual(
            {pedido["id"] for pedido in response.data["pedidos"]},
            {pedido_mozo.pk, pedido_barra.pk},
        )
        pedido_por_id = {
            pedido["id"]: pedido for pedido in response.data["pedidos"]
        }
        detalle_mozo = pedido_por_id[pedido_mozo.pk]["detalles"][0]
        self.assertEqual(Decimal(detalle_mozo["precio_unitario"]), Decimal("1200.00"))
        self.assertEqual(Decimal(detalle_mozo["subtotal"]), Decimal("2400.00"))
        self.assertFalse(pedido_por_id[pedido_mozo.pk]["agregado_por_barra"])
        self.assertEqual(pedido_por_id[pedido_mozo.pk]["autor_id"], self.mozo.pk)
        detalle_barra = pedido_por_id[pedido_barra.pk]["detalles"][0]
        self.assertEqual(Decimal(detalle_barra["precio_unitario"]), Decimal("1900.00"))
        self.assertEqual(Decimal(detalle_barra["subtotal"]), Decimal("5700.00"))
        self.assertTrue(pedido_por_id[pedido_barra.pk]["agregado_por_barra"])
        self.assertEqual(pedido_por_id[pedido_barra.pk]["autor_id"], self.usuario_barra.pk)
        self.assertEqual(pedido_por_id[pedido_barra.pk]["autor_nombre"], "consumo_barra")
        self.assertEqual(Decimal(response.data["total"]), Decimal("8100.00"))

    def test_incluye_producto_inactivo_con_nombre_y_precio_snapshot(self):
        pedido = self.crear_pedido(self.mozo, cantidad=2)
        self.producto.precio = Decimal("2500.00")
        self.producto.activo = False
        self.producto.save()

        response = self.consultar_como(self.mozo)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        detalle = response.data["pedidos"][0]["detalles"][0]
        self.assertEqual(response.data["pedidos"][0]["id"], pedido.pk)
        self.assertEqual(detalle["producto"], self.producto.pk)
        self.assertEqual(detalle["producto_nombre"], "Limonada consumo")
        self.assertEqual(Decimal(detalle["precio_unitario"]), Decimal("1200.00"))
        self.assertEqual(Decimal(detalle["subtotal"]), Decimal("2400.00"))

    def test_cuenta_cerrada_y_nueva_cuenta_de_la_misma_mesa_quedan_separadas(self):
        pedido_anterior = self.crear_pedido(self.mozo)
        self.cuenta.cerrar()
        cuenta_nueva = Cuenta.objects.create(
            mesa=self.mesa,
            mozo_responsable=self.otro_mozo,
        )
        Pedido.objects.create(
            mesa=self.mesa,
            cuenta=cuenta_nueva,
            mozo=self.otro_mozo,
            creado_por=self.otro_mozo,
        )
        DetallePedido.objects.create(
            pedido=cuenta_nueva.pedidos.get(),
            producto=self.producto,
            cantidad=4,
        )

        self.client.force_authenticate(user=self.usuario_barra)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_404_NOT_FOUND)
        self.url = reverse("cuenta-consumo", args=[cuenta_nueva.pk])
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["pedidos"]), 1)
        self.assertNotEqual(response.data["pedidos"][0]["id"], pedido_anterior.pk)
        self.assertEqual(Decimal(response.data["total"]), Decimal("4800.00"))

    def test_cuenta_inexistente_devuelve_not_found(self):
        self.client.force_authenticate(user=self.usuario_barra)

        response = self.client.get(
            reverse("cuenta-consumo", args=[self.cuenta.pk + 1000]),
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cuenta_abierta_vacia_es_consultable_por_barra_y_total_es_cero(self):
        response = self.consultar_como(self.usuario_barra)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["pedidos"], [])
        self.assertEqual(Decimal(response.data["total"]), Decimal("0.00"))

    def test_pedido_historico_sin_creado_por_usa_el_mozo_como_autor(self):
        pedido = Pedido.objects.create(
            mesa=self.mesa,
            cuenta=self.cuenta,
            mozo=self.mozo,
            creado_por=None,
        )
        DetallePedido.objects.create(
            pedido=pedido,
            producto=self.producto,
            cantidad=1,
        )

        response = self.consultar_como(self.mozo)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["pedidos"][0]["autor_id"], self.mozo.pk)
        self.assertEqual(response.data["pedidos"][0]["autor_nombre"], "Ana Gomez")

    def test_responsable_sin_nombre_usa_username(self):
        self.mozo.first_name = ""
        self.mozo.last_name = ""
        self.mozo.save()

        response = self.consultar_como(self.mozo)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["responsable"],
            {"id": self.mozo.pk, "nombre": "consumo_mozo"},
        )

    def test_solo_mozo_responsable_y_barra_pueden_consultar_consumo(self):
        self.crear_pedido(self.mozo, cantidad=3)

        response_responsable = self.consultar_como(self.mozo)
        response_otro_mozo = self.consultar_como(self.otro_mozo)

        self.assertEqual(response_responsable.status_code, status.HTTP_200_OK)
        self.assertEqual(response_otro_mozo.status_code, status.HTTP_403_FORBIDDEN)
        self.assertNotIn("pedidos", response_otro_mozo.data)
        self.assertEqual(
            self.consultar_como(self.usuario_barra).status_code,
            status.HTTP_200_OK,
        )

    def test_sin_responsable_solo_barra_puede_consultar(self):
        self.cuenta.mozo_responsable = None
        self.cuenta.save(update_fields=["mozo_responsable"])

        self.assertEqual(
            self.consultar_como(self.mozo).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        response_barra = self.consultar_como(self.usuario_barra)
        self.assertEqual(response_barra.status_code, status.HTTP_200_OK)
        self.assertIsNone(response_barra.data["responsable"])
        self.assertEqual(response_barra.data["pedidos"], [])

        self.client.force_authenticate(user=self.usuario_barra)
        carga = self.client.post(
            reverse("barra-carga-create"),
            {
                "mesa": self.mesa.pk,
                "cuenta": self.cuenta.pk,
                "detalles": [
                    {"producto": self.producto.pk, "cantidad": 1},
                ],
            },
            format="json",
        )
        self.assertEqual(carga.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(self.cuenta.pedidos.exists())

    def test_administrador_cocina_y_anonimos_no_pueden_consultar(self):
        for usuario in [self.admin, self.cocina]:
            with self.subTest(rol=usuario.rol):
                self.assertEqual(
                    self.consultar_como(usuario).status_code,
                    status.HTTP_403_FORBIDDEN,
                )

        self.client.force_authenticate(user=None)
        response = self.client.get(self.url)
        self.assertIn(
            response.status_code,
            {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN},
        )

    def test_carga_barra_se_identifica_por_aviso_aunque_cambie_rol_del_autor(self):
        pedido = self.crear_pedido(self.usuario_barra, cantidad=2)
        User.objects.filter(pk=self.usuario_barra.pk).update(
            rol=User.Rol.ADMINISTRADOR,
        )

        response = self.consultar_como(self.mozo)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        pedido_data = response.data["pedidos"][0]
        self.assertEqual(pedido_data["id"], pedido.pk)
        self.assertTrue(pedido_data["agregado_por_barra"])
        self.assertEqual(pedido_data["autor_id"], self.usuario_barra.pk)
        self.assertEqual(pedido_data["autor_nombre"], "consumo_barra")


class SolicitudCancelacionDetalleAPITests(APITestCase):
    def setUp(self):
        self.salon = Sector.objects.create(nombre=Sector.Nombre.SALON)
        self.barra = Sector.objects.create(nombre=Sector.Nombre.BARRA)
        self.pizza = Sector.objects.create(nombre=Sector.Nombre.PIZZA)
        self.platos = Sector.objects.create(nombre=Sector.Nombre.PLATOS)
        self.mozo = User.objects.create_user(
            username="cancelacion_mozo",
            password="ClaveSoloParaTests-2026",
            first_name="María",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )
        self.otro_mozo = User.objects.create_user(
            username="cancelacion_otro_mozo",
            password="ClaveSoloParaTests-2026",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )
        self.barra_user = User.objects.create_user(
            username="cancelacion_barra",
            password="ClaveSoloParaTests-2026",
            rol=User.Rol.BARRA,
            sector=self.barra,
        )
        self.cocina_pizza = User.objects.create_user(
            username="cancelacion_pizza",
            password="ClaveSoloParaTests-2026",
            rol=User.Rol.COCINA,
            sector=self.pizza,
        )
        self.cocina_platos = User.objects.create_user(
            username="cancelacion_platos",
            password="ClaveSoloParaTests-2026",
            rol=User.Rol.COCINA,
            sector=self.platos,
        )
        self.admin = User.objects.create_user(
            username="cancelacion_admin",
            password="ClaveSoloParaTests-2026",
            rol=User.Rol.ADMINISTRADOR,
        )
        self.mesa = Mesa.objects.create(numero=91, zona=Mesa.Zona.SALON)
        self.cuenta = Cuenta.objects.create(
            mesa=self.mesa,
            mozo_responsable=self.mozo,
        )
        self.categoria = Categoria.objects.create(nombre="Cancelaciones")
        self.producto = Producto.objects.create(
            nombre="Bebida para cancelar",
            precio=Decimal("1200.00"),
            categoria=self.categoria,
            sector_destino=self.barra,
        )
        self.pedido = Pedido.objects.create(
            mesa=self.mesa,
            cuenta=self.cuenta,
            mozo=self.mozo,
            creado_por=self.mozo,
        )
        self.detalle = DetallePedido.objects.create(
            pedido=self.pedido,
            producto=self.producto,
            cantidad=4,
        )
        self.solicitar_url = reverse(
            "detalle-solicitar-cancelacion",
            args=[self.cuenta.pk, self.detalle.pk],
        )
        self.consumo_url = reverse("cuenta-consumo", args=[self.cuenta.pk])
        self.pendientes_url = reverse("cancelacion-pendiente-list")
        self.notificaciones_url = reverse("cancelacion-notificacion-list")
        self.notificaciones_leidas_url = reverse(
            "cancelacion-notificacion-marcar-leidas",
        )

    def solicitar(
        self,
        cantidad=1,
        motivo="El cliente cambió de opinión",
        clave_idempotencia=None,
    ):
        return self.client.post(
            self.solicitar_url,
            {
                "clave_idempotencia": clave_idempotencia or uuid4(),
                "cantidad": cantidad,
                "motivo": motivo,
            },
            format="json",
        )

    def resolver(self, solicitud_id, usuario, estado):
        self.client.force_authenticate(user=usuario)
        return self.client.post(
            reverse("cancelacion-resolver", args=[solicitud_id]),
            {"estado": estado},
            format="json",
        )

    def consultar_consumo(self):
        self.client.force_authenticate(user=self.mozo)
        return self.client.get(self.consumo_url)

    def test_solo_mozo_responsable_puede_solicitar_cancelacion(self):
        for usuario in [
            self.otro_mozo,
            self.barra_user,
            self.cocina_pizza,
            self.admin,
        ]:
            with self.subTest(rol=usuario.rol, usuario=usuario.username):
                self.client.force_authenticate(user=usuario)
                response = self.solicitar()
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=None)
        response = self.solicitar()
        self.assertIn(
            response.status_code,
            {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN},
        )
        self.client.force_authenticate(user=self.mozo)
        response = self.solicitar()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            response.data["solicitante_nombre"],
            "María",
        )

        self.cuenta.mozo_responsable = None
        self.cuenta.save(update_fields=["mozo_responsable"])
        self.client.force_authenticate(user=self.mozo)
        unassigned = self.solicitar()
        self.assertEqual(unassigned.status_code, status.HTTP_403_FORBIDDEN)

    def test_cantidad_positiva_motivo_opcional_y_reserva_pendiente(self):
        self.client.force_authenticate(user=self.mozo)
        clave_idempotencia = uuid4()
        invalid = self.client.post(
            self.solicitar_url,
            {
                "clave_idempotencia": uuid4(),
                "cantidad": 0,
            },
            format="json",
        )
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)

        payload = {
            "clave_idempotencia": clave_idempotencia,
            "cantidad": 3,
        }
        pending = self.client.post(
            self.solicitar_url,
            payload,
            format="json",
        )
        self.assertEqual(pending.status_code, status.HTTP_201_CREATED)
        self.assertEqual(pending.data["motivo"], "")
        repeated = self.client.post(
            self.solicitar_url,
            payload,
            format="json",
        )
        self.assertEqual(repeated.status_code, status.HTTP_200_OK)
        self.assertEqual(repeated.data["id"], pending.data["id"])
        excessive = self.solicitar(
            cantidad=2,
            clave_idempotencia=uuid4(),
        )
        self.assertEqual(excessive.status_code, status.HTTP_400_BAD_REQUEST)

        response = self.consultar_consumo()
        detail = response.data["pedidos"][0]["detalles"][0]
        self.assertEqual(detail["cantidad"], 4)
        self.assertEqual(detail["cantidad_cancelada"], 0)
        self.assertEqual(detail["cantidad_vigente"], 4)
        self.assertEqual(detail["cantidad_pendiente"], 3)
        self.assertEqual(detail["cantidad_disponible_cancelacion"], 1)
        self.assertEqual(Decimal(detail["subtotal"]), Decimal("4800.00"))
        self.assertEqual(Decimal(response.data["total"]), Decimal("4800.00"))

    def test_repetir_misma_clave_devuelve_solicitud_existente_sin_reservar_dos_veces(self):
        self.client.force_authenticate(user=self.mozo)
        clave_idempotencia = uuid4()
        first = self.solicitar(
            cantidad=2,
            clave_idempotencia=clave_idempotencia,
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        repeated = self.solicitar(
            cantidad=2,
            clave_idempotencia=clave_idempotencia,
        )

        self.assertEqual(repeated.status_code, status.HTTP_200_OK)
        self.assertEqual(repeated.data["id"], first.data["id"])
        self.assertEqual(
            repeated.data["clave_idempotencia"],
            str(clave_idempotencia),
        )
        self.assertEqual(
            SolicitudCancelacionDetalle.objects.filter(
                clave_idempotencia=clave_idempotencia,
            ).count(),
            1,
        )
        consumption = self.consultar_consumo()
        detail = consumption.data["pedidos"][0]["detalles"][0]
        self.assertEqual(detail["cantidad_pendiente"], 2)
        self.assertEqual(detail["cantidad_disponible_cancelacion"], 2)

    def test_reutilizar_clave_con_datos_diferentes_devuelve_conflicto(self):
        self.client.force_authenticate(user=self.mozo)
        clave_idempotencia = uuid4()
        created = self.solicitar(
            cantidad=1,
            motivo="Motivo original",
            clave_idempotencia=clave_idempotencia,
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)

        for payload in [
            {
                "clave_idempotencia": clave_idempotencia,
                "cantidad": 2,
                "motivo": "Motivo original",
            },
            {
                "clave_idempotencia": clave_idempotencia,
                "cantidad": 1,
                "motivo": "Otro motivo",
            },
        ]:
            with self.subTest(payload=payload):
                response = self.client.post(
                    self.solicitar_url,
                    payload,
                    format="json",
                )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_409_CONFLICT,
                )

        self.assertEqual(
            SolicitudCancelacionDetalle.objects.filter(
                clave_idempotencia=clave_idempotencia,
            ).count(),
            1,
        )

    def test_aceptacion_descuenta_snapshot_y_resolucion_repetida_no_repite(self):
        self.producto.precio = Decimal("2500.00")
        self.producto.save()
        self.client.force_authenticate(user=self.mozo)
        response = self.solicitar(cantidad=2)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        solicitud_id = response.data["id"]

        wrong_sector = self.resolver(
            solicitud_id,
            self.cocina_pizza,
            SolicitudCancelacionDetalle.Estado.ACEPTADA,
        )
        self.assertEqual(wrong_sector.status_code, status.HTTP_403_FORBIDDEN)

        accepted = self.resolver(
            solicitud_id,
            self.barra_user,
            SolicitudCancelacionDetalle.Estado.ACEPTADA,
        )
        self.assertEqual(accepted.status_code, status.HTTP_200_OK)
        self.assertEqual(accepted.data["estado"], "ACEPTADA")
        self.assertEqual(accepted.data["resuelto_por_id"], self.barra_user.pk)
        self.assertIsNotNone(accepted.data["fecha_resolucion"])

        self.client.force_authenticate(user=self.mozo)
        excessive = self.solicitar(cantidad=3)
        self.assertEqual(excessive.status_code, status.HTTP_400_BAD_REQUEST)

        repeated = self.resolver(
            solicitud_id,
            self.barra_user,
            SolicitudCancelacionDetalle.Estado.ACEPTADA,
        )
        self.assertEqual(repeated.status_code, status.HTTP_404_NOT_FOUND)
        response = self.consultar_consumo()
        detail = response.data["pedidos"][0]["detalles"][0]
        self.assertEqual(detail["cantidad"], 4)
        self.assertEqual(detail["cantidad_cancelada"], 2)
        self.assertEqual(detail["cantidad_vigente"], 2)
        self.assertEqual(Decimal(detail["precio_unitario"]), Decimal("1200.00"))
        self.assertEqual(Decimal(detail["subtotal_original"]), Decimal("4800.00"))
        self.assertEqual(Decimal(detail["subtotal"]), Decimal("2400.00"))
        self.assertEqual(Decimal(response.data["total"]), Decimal("2400.00"))
        self.detalle.refresh_from_db()
        self.assertEqual(self.detalle.cantidad, 4)
        self.assertEqual(self.detalle.precio_unitario, Decimal("1200.00"))

    def test_nueva_resolucion_rechazada_se_rechaza_y_historico_se_conserva(self):
        self.client.force_authenticate(user=self.mozo)
        response = self.solicitar(cantidad=2)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        rejected = self.resolver(
            response.data["id"],
            self.barra_user,
            SolicitudCancelacionDetalle.Estado.RECHAZADA,
        )
        self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST)
        solicitud_historica = SolicitudCancelacionDetalle.objects.create(
            detalle=self.detalle,
            solicitante=self.mozo,
            clave_idempotencia=uuid4(),
            cantidad=1,
            motivo="Registro histórico rechazado",
            estado=SolicitudCancelacionDetalle.Estado.RECHAZADA,
            resuelto_por=self.barra_user,
        )

        consumption = self.consultar_consumo()
        detail = consumption.data["pedidos"][0]["detalles"][0]
        self.assertEqual(detail["cantidad_cancelada"], 0)
        self.assertEqual(detail["cantidad_vigente"], 4)
        self.assertEqual(Decimal(consumption.data["total"]), Decimal("4800.00"))
        estados = {
            solicitud["estado"]
            for solicitud in detail["solicitudes_cancelacion"]
        }
        self.assertIn("PENDIENTE", estados)
        self.assertIn("RECHAZADA", estados)
        self.assertEqual(
            next(
                solicitud["id"]
                for solicitud in detail["solicitudes_cancelacion"]
                if solicitud["estado"] == "RECHAZADA"
            ),
            solicitud_historica.pk,
        )

    def test_barra_recibe_todos_los_sectores_y_mozos_solo_sus_solicitudes(self):
        self.client.force_authenticate(user=self.mozo)
        propia = self.solicitar()
        producto_pizza = Producto.objects.create(
            nombre="Pizza para notificar",
            precio=Decimal("2000.00"),
            categoria=self.categoria,
            sector_destino=self.pizza,
        )
        detalle_pizza = DetallePedido.objects.create(
            pedido=self.pedido,
            producto=producto_pizza,
            cantidad=2,
        )
        pizza_request = self.client.post(
            reverse(
                "detalle-solicitar-cancelacion",
                args=[self.cuenta.pk, detalle_pizza.pk],
            ),
            {
                "clave_idempotencia": uuid4(),
                "cantidad": 1,
                "motivo": "Notificar a pizza",
            },
            format="json",
        )
        self.assertEqual(pizza_request.status_code, status.HTTP_201_CREATED)
        historica = SolicitudCancelacionDetalle.objects.create(
            detalle=self.detalle,
            solicitante=self.mozo,
            clave_idempotencia=uuid4(),
            cantidad=1,
            motivo="Resultado visible en notificaciones",
            estado=SolicitudCancelacionDetalle.Estado.RECHAZADA,
            resuelto_por=self.barra_user,
        )

        otra_mesa = Mesa.objects.create(numero=92, zona=Mesa.Zona.SALON)
        otra_cuenta = Cuenta.objects.create(
            mesa=otra_mesa,
            mozo_responsable=self.otro_mozo,
        )
        otro_pedido = Pedido.objects.create(
            mesa=otra_mesa,
            cuenta=otra_cuenta,
            mozo=self.otro_mozo,
            creado_por=self.otro_mozo,
        )
        otro_detalle = DetallePedido.objects.create(
            pedido=otro_pedido,
            producto=self.producto,
            cantidad=2,
        )
        self.client.force_authenticate(user=self.otro_mozo)
        ajena = self.client.post(
            reverse(
                "detalle-solicitar-cancelacion",
                args=[otra_cuenta.pk, otro_detalle.pk],
            ),
            {
                "clave_idempotencia": uuid4(),
                "cantidad": 1,
                "motivo": "Solicitud de otra persona",
            },
            format="json",
        )
        self.assertEqual(ajena.status_code, status.HTTP_201_CREATED)

        self.client.force_authenticate(user=self.mozo)
        propias = self.client.get(self.notificaciones_url)
        self.assertEqual(propias.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item["id"] for item in propias.data},
            {propia.data["id"], pizza_request.data["id"], historica.pk},
        )
        self.assertTrue(
            all(item["solicitante_id"] == self.mozo.pk for item in propias.data),
        )
        self.assertTrue(
            all(item["fecha_solicitud"] for item in propias.data),
        )
        self.assertTrue(
            all(item["motivo"] for item in propias.data),
        )
        self.assertTrue(all(not item["leido"] for item in propias.data))

        self.client.force_authenticate(user=self.barra_user)
        barra = self.client.get(self.notificaciones_url)
        self.assertEqual(barra.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item["id"] for item in barra.data},
            {
                propia.data["id"],
                pizza_request.data["id"],
                ajena.data["id"],
                historica.pk,
            },
        )
        self.assertIn(
            "RECHAZADA",
            {
                item["estado"]
                for item in barra.data
                if item["id"] == historica.pk
            },
        )
        self.assertTrue(
            all(item["zona"] == "Salón" for item in barra.data),
        )
        self.assertTrue(all(not item["leido"] for item in barra.data))

        for usuario in [self.cocina_pizza, self.cocina_platos]:
            with self.subTest(rol=usuario.rol, sector=usuario.sector.nombre):
                self.client.force_authenticate(user=usuario)
                response = self.client.get(self.notificaciones_url)
                self.assertEqual(
                    response.status_code,
                    status.HTTP_403_FORBIDDEN,
                )

        self.client.force_authenticate(user=self.otro_mozo)
        otra_respuesta = self.client.get(self.notificaciones_url)
        self.assertEqual(otra_respuesta.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item["id"] for item in otra_respuesta.data},
            {ajena.data["id"]},
        )

        self.client.force_authenticate(user=self.admin)
        admin_response = self.client.get(self.notificaciones_url)
        self.assertEqual(admin_response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=None)
        response = self.client.get(self.notificaciones_url)
        self.assertIn(
            response.status_code,
            {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN},
        )

    def test_mozo_marca_solo_sus_notificaciones_sin_modificar_solicitudes(self):
        self.client.force_authenticate(user=self.mozo)
        propia = self.solicitar()

        otra_mesa = Mesa.objects.create(numero=94, zona=Mesa.Zona.SALON)
        otra_cuenta = Cuenta.objects.create(
            mesa=otra_mesa,
            mozo_responsable=self.otro_mozo,
        )
        otro_pedido = Pedido.objects.create(
            mesa=otra_mesa,
            cuenta=otra_cuenta,
            mozo=self.otro_mozo,
            creado_por=self.otro_mozo,
        )
        otro_detalle = DetallePedido.objects.create(
            pedido=otro_pedido,
            producto=self.producto,
            cantidad=2,
        )
        self.client.force_authenticate(user=self.otro_mozo)
        ajena = self.client.post(
            reverse(
                "detalle-solicitar-cancelacion",
                args=[otra_cuenta.pk, otro_detalle.pk],
            ),
            {
                "clave_idempotencia": uuid4(),
                "cantidad": 1,
                "motivo": "Solicitud ajena",
            },
            format="json",
        )
        self.assertEqual(ajena.status_code, status.HTTP_201_CREATED)

        self.client.force_authenticate(user=self.mozo)
        visible = self.client.get(self.notificaciones_url)
        self.assertEqual({item["id"] for item in visible.data}, {propia.data["id"]})
        self.assertFalse(visible.data[0]["leido"])
        marked = self.client.post(
            self.notificaciones_leidas_url,
            format="json",
        )
        self.assertEqual(marked.status_code, status.HTTP_200_OK)
        self.assertEqual(marked.data, {"actualizadas": 1})

        visible = self.client.get(self.notificaciones_url)
        self.assertEqual(len(visible.data), 1)
        self.assertTrue(visible.data[0]["leido"])
        own_read = LecturaNotificacionCancelacion.objects.get(
            solicitud_id=propia.data["id"],
            usuario=self.mozo,
        )
        self.assertIsNotNone(own_read.leido_en)
        self.assertFalse(
            LecturaNotificacionCancelacion.objects.filter(
                solicitud_id=ajena.data["id"],
                usuario=self.mozo,
            ).exists(),
        )
        solicitud_propia = SolicitudCancelacionDetalle.objects.get(
            pk=propia.data["id"],
        )
        self.assertEqual(
            solicitud_propia.estado,
            SolicitudCancelacionDetalle.Estado.PENDIENTE,
        )

        self.client.force_authenticate(user=self.otro_mozo)
        own_for_other = self.client.get(self.notificaciones_url)
        self.assertEqual(len(own_for_other.data), 1)
        self.assertEqual(own_for_other.data[0]["id"], ajena.data["id"])
        self.assertFalse(own_for_other.data[0]["leido"])

        self.client.force_authenticate(user=self.barra_user)
        barra_view = self.client.get(self.notificaciones_url)
        own_as_barra = next(
            item for item in barra_view.data if item["id"] == propia.data["id"]
        )
        self.assertFalse(own_as_barra["leido"])

    def test_barra_marca_solo_notificaciones_que_puede_consultar(self):
        self.client.force_authenticate(user=self.mozo)
        visible_request = self.solicitar()
        closed_mesa = Mesa.objects.create(
            numero=95,
            zona=Mesa.Zona.SALON,
        )
        closed_account = Cuenta.objects.create(
            mesa=closed_mesa,
            mozo_responsable=self.otro_mozo,
        )
        closed_order = Pedido.objects.create(
            mesa=closed_mesa,
            cuenta=closed_account,
            mozo=self.otro_mozo,
            creado_por=self.otro_mozo,
        )
        closed_detail = DetallePedido.objects.create(
            pedido=closed_order,
            producto=self.producto,
            cantidad=2,
        )
        self.client.force_authenticate(user=self.otro_mozo)
        closed_request = self.client.post(
            reverse(
                "detalle-solicitar-cancelacion",
                args=[closed_account.pk, closed_detail.pk],
            ),
            {
                "clave_idempotencia": uuid4(),
                "cantidad": 1,
                "motivo": "Solicitud de otra cuenta",
            },
            format="json",
        )
        self.assertEqual(closed_request.status_code, status.HTTP_201_CREATED)
        closed_account.cerrar()

        self.client.force_authenticate(user=self.barra_user)
        before = self.client.get(self.notificaciones_url)
        self.assertEqual(
            {item["id"] for item in before.data},
            {visible_request.data["id"]},
        )
        marked = self.client.post(
            self.notificaciones_leidas_url,
            format="json",
        )
        self.assertEqual(marked.status_code, status.HTTP_200_OK)
        self.assertEqual(marked.data, {"actualizadas": 1})

        after = self.client.get(self.notificaciones_url)
        self.assertEqual(len(after.data), 1)
        self.assertTrue(after.data[0]["leido"])
        self.assertTrue(
            LecturaNotificacionCancelacion.objects.filter(
                solicitud_id=visible_request.data["id"],
                usuario=self.barra_user,
            ).exists(),
        )
        self.assertFalse(
            LecturaNotificacionCancelacion.objects.filter(
                solicitud_id=closed_request.data["id"],
                usuario=self.barra_user,
            ).exists(),
        )
        visible_request_db = SolicitudCancelacionDetalle.objects.get(
            pk=visible_request.data["id"],
        )
        closed_request_db = SolicitudCancelacionDetalle.objects.get(
            pk=closed_request.data["id"],
        )
        self.assertEqual(
            visible_request_db.estado,
            SolicitudCancelacionDetalle.Estado.PENDIENTE,
        )
        self.assertEqual(
            closed_request_db.estado,
            SolicitudCancelacionDetalle.Estado.PENDIENTE,
        )

    def test_solo_mozo_y_barra_autorizada_pueden_marcar_notificaciones(self):
        for usuario in [self.cocina_pizza, self.cocina_platos, self.admin, None]:
            with self.subTest(usuario=usuario):
                self.client.force_authenticate(user=usuario)
                response = self.client.post(
                    self.notificaciones_leidas_url,
                    format="json",
                )
                self.assertIn(
                    response.status_code,
                    {
                        status.HTTP_401_UNAUTHORIZED,
                        status.HTTP_403_FORBIDDEN,
                    },
                )

        barra_sin_sector = User.objects.create_user(
            username="cancelacion_barra_sin_sector_lectura",
            rol=User.Rol.BARRA,
        )
        barra_sector_incorrecto = User.objects.create_user(
            username="cancelacion_barra_sector_incorrecto_lectura",
            rol=User.Rol.BARRA,
            sector=self.pizza,
        )
        for usuario in [barra_sin_sector, barra_sector_incorrecto]:
            with self.subTest(usuario=usuario.username):
                self.client.force_authenticate(user=usuario)
                response = self.client.post(
                    self.notificaciones_leidas_url,
                    format="json",
                )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_403_FORBIDDEN,
                )

    def test_mozo_ve_resultado_historico_solo_de_sus_solicitudes(self):
        historica = SolicitudCancelacionDetalle.objects.create(
            detalle=self.detalle,
            solicitante=self.mozo,
            clave_idempotencia=uuid4(),
            cantidad=1,
            motivo="Historial propio",
            estado=SolicitudCancelacionDetalle.Estado.RECHAZADA,
            resuelto_por=self.barra_user,
        )
        otra_mesa = Mesa.objects.create(numero=93, zona=Mesa.Zona.VEREDA_A)
        otra_cuenta = Cuenta.objects.create(
            mesa=otra_mesa,
            mozo_responsable=self.otro_mozo,
        )
        otro_pedido = Pedido.objects.create(
            mesa=otra_mesa,
            cuenta=otra_cuenta,
            mozo=self.otro_mozo,
            creado_por=self.otro_mozo,
        )
        otro_detalle = DetallePedido.objects.create(
            pedido=otro_pedido,
            producto=self.producto,
            cantidad=2,
        )
        solicitud_ajena = SolicitudCancelacionDetalle.objects.create(
            detalle=otro_detalle,
            solicitante=self.otro_mozo,
            clave_idempotencia=uuid4(),
            cantidad=1,
            motivo="Historial privado",
            estado=SolicitudCancelacionDetalle.Estado.ACEPTADA,
            resuelto_por=self.barra_user,
        )

        self.client.force_authenticate(user=self.mozo)
        response = self.client.get(self.notificaciones_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], historica.pk)
        self.assertEqual(response.data[0]["estado"], "RECHAZADA")
        self.assertNotEqual(response.data[0]["id"], solicitud_ajena.pk)

    def test_barra_ve_y_acepta_pendientes_de_todos_los_sectores(self):
        producto_pizza = Producto.objects.create(
            nombre="Pizza para cancelar",
            precio=Decimal("1800.00"),
            categoria=self.categoria,
            sector_destino=self.pizza,
        )
        detalle_pizza = DetallePedido.objects.create(
            pedido=self.pedido,
            producto=producto_pizza,
            cantidad=2,
        )
        producto_platos = Producto.objects.create(
            nombre="Plato para cancelar",
            precio=Decimal("2400.00"),
            categoria=self.categoria,
            sector_destino=self.platos,
        )
        detalle_platos = DetallePedido.objects.create(
            pedido=self.pedido,
            producto=producto_platos,
            cantidad=2,
        )
        self.client.force_authenticate(user=self.mozo)
        solicitudes = [self.solicitar()]
        for detalle in [detalle_pizza, detalle_platos]:
            response = self.client.post(
                reverse(
                    "detalle-solicitar-cancelacion",
                    args=[self.cuenta.pk, detalle.pk],
                ),
                {
                    "clave_idempotencia": uuid4(),
                    "cantidad": 1,
                    "motivo": "Pedido equivocado",
                },
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            solicitudes.append(response)

        for solicitud in solicitudes:
            for usuario in [self.cocina_pizza, self.cocina_platos]:
                with self.subTest(
                    sector=usuario.sector.nombre,
                    solicitud=solicitud.data["id"],
                ):
                    respuesta = self.resolver(
                        solicitud.data["id"],
                        usuario,
                        SolicitudCancelacionDetalle.Estado.ACEPTADA,
                    )
                    self.assertEqual(
                        respuesta.status_code,
                        status.HTTP_403_FORBIDDEN,
                    )

        self.client.force_authenticate(user=self.barra_user)
        barra_items = self.client.get(self.pendientes_url)
        self.assertEqual(barra_items.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item["producto_nombre"] for item in barra_items.data},
            {
                "Bebida para cancelar",
                "Pizza para cancelar",
                "Plato para cancelar",
            },
        )

        for usuario in [self.cocina_pizza, self.cocina_platos, self.mozo, self.admin]:
            with self.subTest(rol=usuario.rol):
                self.client.force_authenticate(user=usuario)
                response = self.client.get(self.pendientes_url)
                self.assertEqual(
                    response.status_code,
                    status.HTTP_403_FORBIDDEN,
                )

        for solicitud in solicitudes:
            accepted = self.resolver(
                solicitud.data["id"],
                self.barra_user,
                SolicitudCancelacionDetalle.Estado.ACEPTADA,
            )
            self.assertEqual(accepted.status_code, status.HTTP_200_OK)
            self.assertEqual(accepted.data["estado"], "ACEPTADA")
            self.assertEqual(
                accepted.data["resuelto_por_id"],
                self.barra_user.pk,
            )

    def test_barra_sin_sector_o_con_sector_incorrecto_no_puede_revisar(self):
        self.client.force_authenticate(user=self.mozo)
        pending = self.solicitar()
        self.assertEqual(pending.status_code, status.HTTP_201_CREATED)

        barra_sin_sector = User.objects.create_user(
            username="cancelacion_barra_sin_sector",
            rol=User.Rol.BARRA,
        )
        barra_otro_sector = User.objects.create_user(
            username="cancelacion_barra_otro_sector",
            rol=User.Rol.BARRA,
            sector=self.pizza,
        )

        for usuario in [barra_sin_sector, barra_otro_sector]:
            with self.subTest(usuario=usuario.username):
                self.client.force_authenticate(user=usuario)
                pending_list = self.client.get(self.pendientes_url)
                self.assertEqual(
                    pending_list.status_code,
                    status.HTTP_403_FORBIDDEN,
                )
                resolution = self.client.post(
                    reverse("cancelacion-resolver", args=[pending.data["id"]]),
                    {"estado": SolicitudCancelacionDetalle.Estado.ACEPTADA},
                    format="json",
                )
                self.assertEqual(
                    resolution.status_code,
                    status.HTTP_403_FORBIDDEN,
                )

        solicitud = SolicitudCancelacionDetalle.objects.get(pk=pending.data["id"])
        self.assertEqual(
            solicitud.estado,
            SolicitudCancelacionDetalle.Estado.PENDIENTE,
        )
        self.assertIsNone(solicitud.resuelto_por_id)

    def test_cuenta_cerrada_no_admite_solicitud_ni_resolucion_y_nueva_cuenta_aislada(self):
        self.client.force_authenticate(user=self.mozo)
        pending = self.solicitar(cantidad=1)
        self.assertEqual(pending.status_code, status.HTTP_201_CREATED)
        solicitud_id = pending.data["id"]

        self.cuenta.cerrar()
        rejected_request = self.solicitar(cantidad=1)
        self.assertEqual(rejected_request.status_code, status.HTTP_404_NOT_FOUND)
        rejected_resolution = self.resolver(
            solicitud_id,
            self.barra_user,
            SolicitudCancelacionDetalle.Estado.ACEPTADA,
        )
        self.assertEqual(
            rejected_resolution.status_code,
            status.HTTP_404_NOT_FOUND,
        )

        nueva_cuenta = Cuenta.objects.create(
            mesa=self.mesa,
            mozo_responsable=self.otro_mozo,
        )
        nuevo_pedido = Pedido.objects.create(
            mesa=self.mesa,
            cuenta=nueva_cuenta,
            mozo=self.otro_mozo,
            creado_por=self.otro_mozo,
        )
        DetallePedido.objects.create(
            pedido=nuevo_pedido,
            producto=self.producto,
            cantidad=2,
        )
        self.client.force_authenticate(user=self.otro_mozo)
        mismatched_detail_url = reverse(
            "detalle-solicitar-cancelacion",
            args=[nueva_cuenta.pk, self.detalle.pk],
        )
        mismatched_detail = self.client.post(
            mismatched_detail_url,
            {
                "clave_idempotencia": uuid4(),
                "cantidad": 1,
                "motivo": "Detalle de otra cuenta",
            },
            format="json",
        )
        self.assertEqual(
            mismatched_detail.status_code,
            status.HTTP_404_NOT_FOUND,
        )
        response = self.client.get(
            reverse("cuenta-consumo", args=[nueva_cuenta.pk]),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        detail = response.data["pedidos"][0]["detalles"][0]
        self.assertEqual(detail["cantidad_cancelada"], 0)
        self.assertEqual(detail["solicitudes_cancelacion"], [])
        self.assertEqual(Decimal(response.data["total"]), Decimal("2400.00"))
    def test_estado_entrega_se_separa_por_origen_y_leer_aviso_no_lo_cambia(self):
        productos_por_sector = {}
        for indice, sector in enumerate([self.pizza, self.platos], start=1):
            producto = Producto.objects.create(
                nombre=f"Producto de cocina {indice}",
                precio=Decimal("800.00"),
                categoria=self.categoria,
                sector_destino=sector,
            )
            DetallePedido.objects.create(
                pedido=self.pedido,
                producto=producto,
                cantidad=2,
            )
            productos_por_sector[sector.nombre] = producto

        self.client.force_authenticate(user=self.mozo)
        initial = self.consultar_consumo()
        self.assertEqual(initial.status_code, status.HTTP_200_OK)
        self.assertTrue(
            all(
                detail["estado_entrega"] == "PENDIENTE"
                and detail["puede_solicitar_cancelacion"]
                for order in initial.data["pedidos"]
                for detail in order["detalles"]
            )
        )

        barra_aviso = AvisoRetiro.objects.create(
            pedido=self.pedido,
            origen=AvisoRetiro.Origen.BARRA,
            destinatario=self.mozo,
            mensaje="Barra lista",
        )
        consumo = self.consultar_consumo()
        detalles = {
            detail["producto"]: detail
            for order in consumo.data["pedidos"]
            for detail in order["detalles"]
        }
        self.assertEqual(detalles[self.producto.pk]["estado_entrega"], "ENTREGADO")
        self.assertFalse(
            detalles[self.producto.pk]["puede_solicitar_cancelacion"],
        )
        self.assertEqual(
            detalles[self.producto.pk]["cantidad_disponible_cancelacion"],
            0,
        )
        for sector in [self.pizza, self.platos]:
            detail = detalles[productos_por_sector[sector.nombre].pk]
            self.assertEqual(detail["estado_entrega"], "PENDIENTE")
            self.assertTrue(detail["puede_solicitar_cancelacion"])

        avisos = self.client.get(reverse("aviso-retiro-list"))
        self.assertEqual(avisos.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.client.post(
                reverse("aviso-retiro-marcar-leido", args=[barra_aviso.pk]),
                format="json",
            ).status_code,
            status.HTTP_200_OK,
        )
        details_after_read = {
            detail["producto"]: detail["estado_entrega"]
            for detail in self.consultar_consumo().data["pedidos"][0]["detalles"]
        }
        self.assertEqual(
            details_after_read[self.producto.pk],
            "ENTREGADO",
        )

        AvisoRetiro.objects.create(
            pedido=self.pedido,
            origen=AvisoRetiro.Origen.COCINA,
            destinatario=self.mozo,
            mensaje="Cocina lista",
        )
        consumo = self.consultar_consumo()
        detalles = {
            detail["producto"]: detail
            for order in consumo.data["pedidos"]
            for detail in order["detalles"]
        }
        for sector in [self.pizza, self.platos]:
            detail = detalles[productos_por_sector[sector.nombre].pk]
            self.assertEqual(detail["estado_entrega"], "ENTREGADO")
            self.assertFalse(detail["puede_solicitar_cancelacion"])

    def test_aviso_historico_sin_origen_no_atribuye_entrega(self):
        AvisoRetiro.objects.create(
            pedido=self.pedido,
            origen=None,
            destinatario=self.mozo,
            mensaje="Salón · Mesa histórica",
        )

        response = self.consultar_consumo()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        detail = response.data["pedidos"][0]["detalles"][0]
        self.assertEqual(detail["estado_entrega"], "PENDIENTE")
        self.assertTrue(detail["puede_solicitar_cancelacion"])

    def test_producto_entregado_rechaza_nuevas_cancelaciones_y_reintenta_idempotente(self):
        self.client.force_authenticate(user=self.mozo)
        key = uuid4()
        created = self.solicitar(cantidad=1, clave_idempotencia=key)
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        AvisoRetiro.objects.create(
            pedido=self.pedido,
            origen=AvisoRetiro.Origen.BARRA,
            destinatario=self.mozo,
            mensaje="Barra lista",
        )

        repeated = self.solicitar(cantidad=1, clave_idempotencia=key)
        rejected = self.solicitar(cantidad=1, clave_idempotencia=uuid4())

        self.assertEqual(repeated.status_code, status.HTTP_200_OK)
        self.assertEqual(repeated.data["id"], created.data["id"])
        self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            SolicitudCancelacionDetalle.objects.filter(
                detalle=self.detalle,
            ).count(),
            1,
        )
        detail = self.consultar_consumo().data["pedidos"][0]["detalles"][0]
        self.assertEqual(detail["estado_entrega"], "ENTREGADO")
        self.assertEqual(detail["cantidad_pendiente"], 1)
        self.assertEqual(detail["cantidad_disponible_cancelacion"], 0)

    def test_producto_totalmente_cancelado_conserva_historial_y_no_parece_pendiente(self):
        self.detalle.cantidad = 4
        self.detalle.save(update_fields=["cantidad"])
        SolicitudCancelacionDetalle.objects.create(
            detalle=self.detalle,
            solicitante=self.mozo,
            clave_idempotencia=uuid4(),
            cantidad=4,
            motivo="Cancelación total",
            estado=SolicitudCancelacionDetalle.Estado.ACEPTADA,
            resuelto_por=self.barra_user,
        )

        response = self.consultar_consumo()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        detail = response.data["pedidos"][0]["detalles"][0]
        self.assertEqual(detail["estado_entrega"], "CANCELADO")
        self.assertFalse(detail["puede_solicitar_cancelacion"])
        self.assertEqual(detail["cantidad_vigente"], 0)
        self.assertEqual(Decimal(detail["subtotal"]), Decimal("0.00"))
        self.assertEqual(Decimal(response.data["total"]), Decimal("0.00"))
        self.assertEqual(len(detail["solicitudes_cancelacion"]), 1)

    def test_solicitudes_pendientes_se_ordenan_de_mas_antigua_a_mas_nueva(self):
        self.client.force_authenticate(user=self.mozo)
        created = [
            self.solicitar(cantidad=1, clave_idempotencia=uuid4())
            for _ in range(3)
        ]
        self.assertTrue(
            all(response.status_code == status.HTTP_201_CREATED for response in created)
        )
        base = timezone.now()
        for response, seconds in zip(created, [20, 0, 10]):
            SolicitudCancelacionDetalle.objects.filter(
                pk=response.data["id"],
            ).update(fecha_solicitud=base + timedelta(seconds=seconds))

        self.client.force_authenticate(user=self.barra_user)
        pending = self.client.get(self.pendientes_url)

        self.assertEqual(pending.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [request["id"] for request in pending.data],
            [created[1].data["id"], created[2].data["id"], created[0].data["id"]],
        )


class AvisoCancelacionConcurrenteTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_cancelacion_que_compite_con_aviso_espera_y_luego_se_rechaza(self):
        salon = Sector.objects.create(nombre=Sector.Nombre.SALON)
        barra = Sector.objects.create(nombre=Sector.Nombre.BARRA)
        mozo = User.objects.create_user(
            username="mozo_concurrente",
            password="clave-segura",
            rol=User.Rol.MOZO,
            sector=salon,
        )
        usuario_barra = User.objects.create_user(
            username="barra_concurrente",
            password="clave-segura",
            rol=User.Rol.BARRA,
            sector=barra,
        )
        mesa = Mesa.objects.create(numero=120)
        cuenta = Cuenta.objects.create(
            mesa=mesa,
            mozo_responsable=mozo,
        )
        categoria = Categoria.objects.create(nombre="Concurrentes")
        producto = Producto.objects.create(
            nombre="Bebida concurrente",
            precio=Decimal("100.00"),
            categoria=categoria,
            sector_destino=barra,
        )
        pedido = Pedido.objects.create(mesa=mesa, cuenta=cuenta, mozo=mozo)
        detalle = DetallePedido.objects.create(
            pedido=pedido,
            producto=producto,
            cantidad=1,
        )
        lock_acquired = Event()
        request_started = Event()
        release_lock = Event()
        thread_errors = []
        request_result = []
        idempotency_key = uuid4()

        def emitir_aviso():
            close_old_connections()
            try:
                with transaction.atomic():
                    Cuenta.objects.select_for_update().get(pk=cuenta.pk)
                    pedido_bloqueado = Pedido.objects.select_for_update().get(
                        pk=pedido.pk,
                    )
                    lock_acquired.set()
                    if not release_lock.wait(timeout=5):
                        raise TimeoutError("No se liberó el bloqueo de la cuenta.")
                    pedido_bloqueado.crear_aviso_retiro(
                        AvisoRetiro.Origen.BARRA,
                        User.objects.get(pk=usuario_barra.pk),
                    )
            except Exception as error:
                thread_errors.append(error)
            finally:
                close_old_connections()

        def solicitar_cancelacion():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(user=User.objects.get(pk=mozo.pk))
                request_started.set()
                request_result.append(
                    client.post(
                        reverse(
                            "detalle-solicitar-cancelacion",
                            args=[cuenta.pk, detalle.pk],
                        ),
                        {
                            "clave_idempotencia": idempotency_key,
                            "cantidad": 1,
                            "motivo": "Cancelación concurrente",
                        },
                        format="json",
                    ),
                )
            except Exception as error:
                thread_errors.append(error)
            finally:
                close_old_connections()

        notice_thread = Thread(target=emitir_aviso)
        request_thread = Thread(target=solicitar_cancelacion)
        notice_thread.start()
        self.assertTrue(lock_acquired.wait(timeout=5))
        request_thread.start()
        try:
            self.assertTrue(request_started.wait(timeout=5))
            time.sleep(0.2)
        finally:
            release_lock.set()

        notice_thread.join(timeout=10)
        request_thread.join(timeout=10)
        self.assertFalse(notice_thread.is_alive())
        self.assertFalse(request_thread.is_alive())
        self.assertEqual(thread_errors, [])
        self.assertEqual(len(request_result), 1)
        self.assertEqual(request_result[0].status_code, 400)
        self.assertTrue(
            AvisoRetiro.objects.filter(
                pedido=pedido,
                origen=AvisoRetiro.Origen.BARRA,
            ).exists(),
        )
        self.assertFalse(
            SolicitudCancelacionDetalle.objects.filter(
                clave_idempotencia=idempotency_key,
            ).exists(),
        )


class CobrarCuentaAPITests(APITestCase):
    def setUp(self):
        self.salon = Sector.objects.create(nombre=Sector.Nombre.SALON)
        self.barra = Sector.objects.create(nombre=Sector.Nombre.BARRA)
        self.mozo = User.objects.create_user(
            username="cobro_mozo",
            password="test-password",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )
        self.otro_mozo = User.objects.create_user(
            username="cobro_otro_mozo",
            password="test-password",
            rol=User.Rol.MOZO,
            sector=self.salon,
        )
        self.usuario_barra = User.objects.create_user(
            username="cobro_barra",
            password="test-password",
            rol=User.Rol.BARRA,
            sector=self.barra,
        )
        self.mesa = Mesa.objects.create(numero=130)
        self.cuenta = Cuenta.objects.create(
            mesa=self.mesa,
            mozo_responsable=self.mozo,
        )
        self.categoria = Categoria.objects.create(nombre="Cobros")
        self.producto = Producto.objects.create(
            nombre="Producto de cobro",
            precio=Decimal("1200.00"),
            categoria=self.categoria,
            sector_destino=self.barra,
        )
        self.pedido = Pedido.objects.create(
            mesa=self.mesa,
            cuenta=self.cuenta,
            mozo=self.mozo,
            creado_por=self.mozo,
        )
        self.detalle = DetallePedido.objects.create(
            pedido=self.pedido,
            producto=self.producto,
            cantidad=1,
        )
        preparar_pedido_para_cierre(self.pedido)
        self.url = reverse("cuenta-cobrar", args=[self.cuenta.pk])

    def pagar(self, *, total="1200.00", importes=None, clave=None):
        self.client.force_authenticate(user=self.mozo)
        return self.client.post(
            self.url,
            {
                "clave_idempotencia": clave or uuid4(),
                "total_esperado": total,
                "importes": importes if importes is not None else [
                    {"medio": "EFECTIVO", "importe": "1200.00"},
                ],
            },
            format="json",
        )

    def test_cobro_con_un_medio_conserva_historial_y_cierra(self):
        response = self.pagar()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["cuenta"], self.cuenta.pk)
        self.assertEqual(response.data["total_cobrado"], "1200.00")
        self.assertEqual(len(response.data["importes"]), 1)
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.estado, Cuenta.Estado.CERRADA)
        self.assertEqual(self.cuenta.pedidos.count(), 1)
        cobro = CobroCuenta.objects.get(cuenta=self.cuenta)
        self.assertEqual(cobro.mozo, self.mozo)
        self.assertEqual(cobro.total_cobrado, Decimal("1200.00"))
        self.assertEqual(
            list(cobro.importes.values_list("medio", "importe")),
            [("EFECTIVO", Decimal("1200.00"))],
        )

    def test_cobro_combinado_con_productos_listos_y_retiro_habilitado(self):
        response = self.pagar(
            importes=[
                {"medio": "EFECTIVO", "importe": "500.00"},
                {"medio": "TRANSFERENCIA", "importe": "400.00"},
                {"medio": "TARJETA", "importe": "300.00"},
            ],
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            CobroCuenta.objects.get(cuenta=self.cuenta).importes.count(),
            3,
        )

    def assert_cobro_rechazado(self, response, texto=None):
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        if texto:
            self.assertIn(texto, str(response.data["detail"]))
        self.assertFalse(CobroCuenta.objects.exists())
        self.assertFalse(ImportePagoCuenta.objects.exists())
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.estado, Cuenta.Estado.ABIERTA)
        self.assertIsNone(self.cuenta.fecha_cierre)

    def crear_pedido_adicional(self, producto=None, cantidad=1):
        pedido = Pedido.objects.create(
            mesa=self.mesa,
            cuenta=self.cuenta,
            mozo=self.mozo,
            creado_por=self.mozo,
        )
        detalle = DetallePedido.objects.create(
            pedido=pedido,
            producto=producto or self.producto,
            cantidad=cantidad,
        )
        return pedido, detalle

    def crear_producto_cocina(self, nombre="Pizza", precio="800.00"):
        pizza = Sector.objects.get_or_create(nombre=Sector.Nombre.PIZZA)[0]
        return Producto.objects.create(
            nombre=nombre,
            precio=Decimal(precio),
            categoria=self.categoria,
            sector_destino=pizza,
        )

    def test_producto_pendiente_impide_cobrar_y_cerrar(self):
        preparar_pedido_para_cierre(
            self.pedido,
            estado=PreparacionPedidoSector.Estado.PENDIENTE,
            habilitar_retiro=False,
        )

        response = self.pagar()

        self.assert_cobro_rechazado(response, "pendiente de preparación")

    def test_producto_en_preparacion_impide_cobrar_y_cerrar(self):
        preparar_pedido_para_cierre(
            self.pedido,
            estado=PreparacionPedidoSector.Estado.EN_PREPARACION,
            habilitar_retiro=False,
        )

        response = self.pagar()

        self.assert_cobro_rechazado(response, "en preparación")

    def test_producto_listo_sin_retiro_habilitado_impide_cobrar_y_cerrar(self):
        AvisoRetiro.objects.filter(pedido=self.pedido).delete()

        response = self.pagar()

        self.assert_cobro_rechazado(response, "sin retiro habilitado")

    def test_producto_en_preparacion_con_retiro_habilitado_impide_cobrar(self):
        PreparacionPedidoSector.objects.filter(pedido=self.pedido).update(
            estado=PreparacionPedidoSector.Estado.EN_PREPARACION,
        )

        response = self.pagar()

        self.assert_cobro_rechazado(response, "en preparación")

    def test_preparacion_inexistente_se_considera_pendiente(self):
        PreparacionPedidoSector.objects.filter(pedido=self.pedido).delete()

        response = self.pagar()

        self.assert_cobro_rechazado(response, "pendiente de preparación")

    def test_aviso_historico_sin_origen_no_habilita_retiro(self):
        AvisoRetiro.objects.filter(pedido=self.pedido).update(origen=None)

        response = self.pagar()

        self.assert_cobro_rechazado(response, "sin retiro habilitado")

    def test_producto_de_cocina_requiere_listo_y_retiro_habilitado_por_barra(self):
        pizza = self.crear_producto_cocina()
        pedido, _ = self.crear_pedido_adicional(pizza)
        total = "2000.00"
        importes = [{"medio": "EFECTIVO", "importe": total}]

        preparar_pedido_para_cierre(
            pedido,
            estado=PreparacionPedidoSector.Estado.PENDIENTE,
            habilitar_retiro=False,
        )
        self.assert_cobro_rechazado(
            self.pagar(total=total, importes=importes),
            "pendiente de preparación",
        )

        # Retiro de cocina habilitado manualmente, pero la cocina no terminó.
        AvisoRetiro.objects.create(
            pedido=pedido,
            origen=AvisoRetiro.Origen.COCINA,
            destinatario=self.mozo,
            generado_por=self.usuario_barra,
            mensaje="Cocina lista",
        )
        self.assert_cobro_rechazado(
            self.pagar(total=total, importes=importes),
            "pendiente de preparación",
        )

        PreparacionPedidoSector.objects.filter(pedido=pedido).update(
            estado=PreparacionPedidoSector.Estado.LISTO,
        )
        response = self.pagar(total=total, importes=importes)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_aviso_de_barra_no_habilita_producto_de_cocina(self):
        pizza = self.crear_producto_cocina()
        pedido, _ = self.crear_pedido_adicional(pizza)
        preparar_pedido_para_cierre(pedido)
        AvisoRetiro.objects.filter(
            pedido=pedido,
            origen=AvisoRetiro.Origen.COCINA,
        ).delete()

        response = self.pagar(
            total="2000.00",
            importes=[{"medio": "EFECTIVO", "importe": "2000.00"}],
        )

        self.assert_cobro_rechazado(response, "sin retiro habilitado")

    def test_varios_pedidos_todos_deben_estar_listos_y_habilitados(self):
        segundo, _ = self.crear_pedido_adicional()
        tercero, _ = self.crear_pedido_adicional()
        preparar_pedido_para_cierre(segundo)
        preparar_pedido_para_cierre(
            tercero,
            estado=PreparacionPedidoSector.Estado.EN_PREPARACION,
            habilitar_retiro=False,
        )
        total = "3600.00"
        importes = [{"medio": "EFECTIVO", "importe": total}]

        response = self.pagar(total=total, importes=importes)

        self.assert_cobro_rechazado(response, f"Pedido {tercero.pk}")
        self.assertNotIn(f"Pedido {segundo.pk}:", str(response.data["detail"]))

        preparar_pedido_para_cierre(tercero)
        response = self.pagar(total=total, importes=importes)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.estado, Cuenta.Estado.CERRADA)

    def test_producto_totalmente_cancelado_no_bloquea_el_cierre(self):
        pedido_pendiente, detalle = self.crear_pedido_adicional()
        preparar_pedido_para_cierre(
            pedido_pendiente,
            estado=PreparacionPedidoSector.Estado.PENDIENTE,
            habilitar_retiro=False,
        )
        SolicitudCancelacionDetalle.objects.create(
            detalle=detalle,
            solicitante=self.mozo,
            clave_idempotencia=uuid4(),
            cantidad=1,
            motivo="Cancelado antes de preparar",
            estado=SolicitudCancelacionDetalle.Estado.ACEPTADA,
            resuelto_por=self.usuario_barra,
            fecha_resolucion=timezone.now(),
        )

        response = self.pagar()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_cancelacion_parcial_aceptada_no_oculta_unidades_vigentes_pendientes(self):
        pedido_pendiente, detalle = self.crear_pedido_adicional(cantidad=2)
        preparar_pedido_para_cierre(
            pedido_pendiente,
            estado=PreparacionPedidoSector.Estado.PENDIENTE,
            habilitar_retiro=False,
        )
        SolicitudCancelacionDetalle.objects.create(
            detalle=detalle,
            solicitante=self.mozo,
            clave_idempotencia=uuid4(),
            cantidad=1,
            motivo="Una unidad menos",
            estado=SolicitudCancelacionDetalle.Estado.ACEPTADA,
            resuelto_por=self.usuario_barra,
            fecha_resolucion=timezone.now(),
        )

        response = self.pagar(
            total="2400.00",
            importes=[{"medio": "EFECTIVO", "importe": "2400.00"}],
        )

        self.assert_cobro_rechazado(response, "1 x Producto de cobro")

    def test_cancelacion_pendiente_sigue_bloqueando_aunque_todo_este_listo(self):
        SolicitudCancelacionDetalle.objects.create(
            detalle=self.detalle,
            solicitante=self.mozo,
            clave_idempotencia=uuid4(),
            cantidad=1,
            motivo="Pendiente de resolver",
        )

        response = self.pagar()

        self.assert_cobro_rechazado(response, "solicitudes de cancelación pendientes")

    def test_cuenta_sin_pedidos_cierra_con_total_cero(self):
        self.pedido.detalles.all().delete()
        self.pedido.delete()

        response = self.pagar(total="0.00", importes=[])

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.estado, Cuenta.Estado.CERRADA)

    def test_rechazo_por_pendientes_permite_cobrar_luego_con_nueva_clave(self):
        PreparacionPedidoSector.objects.filter(pedido=self.pedido).update(
            estado=PreparacionPedidoSector.Estado.EN_PREPARACION,
        )
        self.assert_cobro_rechazado(self.pagar())

        PreparacionPedidoSector.objects.filter(pedido=self.pedido).update(
            estado=PreparacionPedidoSector.Estado.LISTO,
        )
        response = self.pagar()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(CobroCuenta.objects.count(), 1)

    def test_cobro_usa_cantidad_vigente_y_conserva_cancelaciones(self):
        self.detalle.cantidad = 2
        self.detalle.save()
        solicitud = SolicitudCancelacionDetalle.objects.create(
            detalle=self.detalle,
            solicitante=self.mozo,
            clave_idempotencia=uuid4(),
            cantidad=1,
            motivo="Unidad cancelada",
            estado=SolicitudCancelacionDetalle.Estado.ACEPTADA,
            resuelto_por=self.usuario_barra,
            fecha_resolucion=timezone.now(),
        )

        response = self.pagar(total="1200.00")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.cuenta.refresh_from_db()
        solicitud.refresh_from_db()
        self.assertEqual(self.cuenta.pedidos.get().detalles.get().cantidad, 2)
        self.assertEqual(solicitud.estado, SolicitudCancelacionDetalle.Estado.ACEPTADA)
        self.assertEqual(
            CobroCuenta.objects.get(cuenta=self.cuenta).total_cobrado,
            Decimal("1200.00"),
        )

    def test_importes_invalidos_insuficientes_y_excesivos_no_cierran(self):
        casos = [
            [{"medio": "EFECTIVO", "importe": "-1.00"}],
            [{"medio": "EFECTIVO", "importe": "0.00"}],
            [{"medio": "EFECTIVO", "importe": "1199.99"}],
            [{"medio": "EFECTIVO", "importe": "1200.01"}],
        ]
        for importes in casos:
            with self.subTest(importes=importes):
                response = self.pagar(importes=importes)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(CobroCuenta.objects.count(), 0)
                self.cuenta.refresh_from_db()
                self.assertEqual(self.cuenta.estado, Cuenta.Estado.ABIERTA)

    def test_importe_enviado_no_reemplaza_total_calculado_por_servidor(self):
        response = self.pagar(
            total="999.00",
            importes=[{"medio": "EFECTIVO", "importe": "999.00"}],
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(CobroCuenta.objects.count(), 0)
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.estado, Cuenta.Estado.ABIERTA)

    def test_total_cero_cierra_sin_registrar_pagos(self):
        solicitud = SolicitudCancelacionDetalle.objects.create(
            detalle=self.detalle,
            solicitante=self.mozo,
            clave_idempotencia=uuid4(),
            cantidad=1,
            motivo="Se canceló toda la unidad",
            estado=SolicitudCancelacionDetalle.Estado.ACEPTADA,
            resuelto_por=self.usuario_barra,
            fecha_resolucion=timezone.now(),
        )
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.estado, Cuenta.Estado.ABIERTA)

        response = self.pagar(total="0.00", importes=[])

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["total_cobrado"], "0.00")
        self.assertEqual(response.data["importes"], [])
        self.cuenta.refresh_from_db()
        solicitud.refresh_from_db()
        self.assertEqual(self.cuenta.estado, Cuenta.Estado.CERRADA)
        self.assertEqual(solicitud.estado, SolicitudCancelacionDetalle.Estado.ACEPTADA)
        self.assertEqual(self.cuenta.pedidos.count(), 1)

    def test_cancelacion_pendiente_impide_cerrar(self):
        SolicitudCancelacionDetalle.objects.create(
            detalle=self.detalle,
            solicitante=self.mozo,
            clave_idempotencia=uuid4(),
            cantidad=1,
            motivo="A confirmar",
        )

        response = self.pagar()

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(CobroCuenta.objects.filter(cuenta=self.cuenta).exists())
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.estado, Cuenta.Estado.ABIERTA)

    def test_solo_responsable_puede_cobrar_y_cuenta_historica_no_se_asigna(self):
        for usuario in [self.otro_mozo, self.usuario_barra]:
            self.client.force_authenticate(user=usuario)
            response = self.client.post(
                self.url,
                {
                    "clave_idempotencia": uuid4(),
                    "total_esperado": "1200.00",
                    "importes": [
                        {"medio": "EFECTIVO", "importe": "1200.00"},
                    ],
                },
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.cuenta.mozo_responsable = None
        self.cuenta.save(update_fields=["mozo_responsable"])
        self.client.force_authenticate(user=self.mozo)
        response = self.client.post(
            self.url,
            {
                "clave_idempotencia": uuid4(),
                "total_esperado": "1200.00",
                "importes": [{"medio": "EFECTIVO", "importe": "1200.00"}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("histórica", str(response.data))
        self.assertFalse(CobroCuenta.objects.exists())

    def test_reintento_idempotente_devuelve_cobro_y_datos_distintos_conflictuan(self):
        clave = uuid4()
        importes = [
            {"medio": "EFECTIVO", "importe": "600.00"},
            {"medio": "TARJETA", "importe": "600.00"},
        ]
        primera = self.pagar(importes=importes, clave=clave)
        repetida = self.pagar(importes=importes, clave=clave)
        distinta = self.pagar(
            importes=[
                {"medio": "TRANSFERENCIA", "importe": "1200.00"},
            ],
            clave=clave,
        )

        self.assertEqual(primera.status_code, status.HTTP_201_CREATED)
        self.assertEqual(repetida.status_code, status.HTTP_200_OK)
        self.assertEqual(repetida.data["id"], primera.data["id"])
        self.assertEqual(distinta.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(CobroCuenta.objects.count(), 1)
        self.assertEqual(ImportePagoCuenta.objects.count(), 2)

    def test_error_al_preparar_respuesta_no_confirma_cobro_ni_cierre(self):
        with patch(
            "pedidos.views.CobroCuentaSerializer.data",
            new_callable=PropertyMock,
            side_effect=RuntimeError("Error simulado al serializar el cobro."),
        ):
            with self.assertRaises(RuntimeError):
                self.pagar()

        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.estado, Cuenta.Estado.ABIERTA)
        self.assertFalse(CobroCuenta.objects.filter(cuenta=self.cuenta).exists())
        self.assertFalse(ImportePagoCuenta.objects.exists())

    def test_cuenta_cerrada_rechaza_pedido_y_cancelacion_y_mesa_admite_nueva_cuenta(self):
        original_order_id = self.pedido.pk
        closed_response = self.pagar()
        self.assertEqual(closed_response.status_code, status.HTTP_201_CREATED)

        self.client.force_authenticate(user=self.mozo)
        old_account_order = self.client.post(
            reverse("pedido-create"),
            {
                "mesa": self.mesa.pk,
                "cuenta": self.cuenta.pk,
                "detalles": [{"producto": self.producto.pk, "cantidad": 1}],
            },
            format="json",
        )
        old_account_cancellation = self.client.post(
            reverse(
                "detalle-solicitar-cancelacion",
                args=[self.cuenta.pk, self.detalle.pk],
            ),
            {
                "clave_idempotencia": uuid4(),
                "cantidad": 1,
                "motivo": "Cuenta cerrada",
            },
            format="json",
        )
        self.assertEqual(old_account_order.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(old_account_cancellation.status_code, status.HTTP_404_NOT_FOUND)

        new_order_response = self.client.post(
            reverse("pedido-mesa-create"),
            {
                "mesa": self.mesa.pk,
                "detalles": [{"producto": self.producto.pk, "cantidad": 2}],
            },
            format="json",
        )
        self.assertEqual(new_order_response.status_code, status.HTTP_201_CREATED)
        new_account = Cuenta.objects.get(estado=Cuenta.Estado.ABIERTA, mesa=self.mesa)
        self.assertNotEqual(new_account.pk, self.cuenta.pk)
        self.assertEqual(new_account.mozo_responsable, self.mozo)
        self.assertEqual(new_account.pedidos.count(), 1)
        self.assertEqual(self.cuenta.pedidos.count(), 1)
        self.assertEqual(self.cuenta.pedidos.get().pk, original_order_id)
        self.assertEqual(CobroCuenta.objects.get(cuenta=self.cuenta).total_cobrado, Decimal("1200.00"))


class CobroCuentaConcurrenteTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_cobro_concurrente_con_carga_no_agrega_pedido_a_cuenta_cerrada(self):
        salon = Sector.objects.create(nombre=Sector.Nombre.SALON)
        barra = Sector.objects.create(nombre=Sector.Nombre.BARRA)
        mozo = User.objects.create_user(
            username="cobro_concurrente",
            password="test-password",
            rol=User.Rol.MOZO,
            sector=salon,
        )
        mesa = Mesa.objects.create(numero=131)
        cuenta = Cuenta.objects.create(mesa=mesa, mozo_responsable=mozo)
        categoria = Categoria.objects.create(nombre="Cobro concurrente")
        producto = Producto.objects.create(
            nombre="Producto concurrente",
            precio=Decimal("100.00"),
            categoria=categoria,
            sector_destino=barra,
        )
        pedido = Pedido.objects.create(mesa=mesa, cuenta=cuenta, mozo=mozo)
        DetallePedido.objects.create(pedido=pedido, producto=producto, cantidad=1)
        preparar_pedido_para_cierre(pedido)
        barrier = Barrier(2)
        results = {}
        errors = []

        def post(name, url, payload):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(user=User.objects.get(pk=mozo.pk))
                barrier.wait(timeout=5)
                results[name] = client.post(url, payload, format="json")
            except Exception as error:
                errors.append(error)
            finally:
                close_old_connections()

        payment_thread = Thread(
            target=post,
            args=(
                "payment",
                reverse("cuenta-cobrar", args=[cuenta.pk]),
                {
                    "clave_idempotencia": uuid4(),
                    "total_esperado": "100.00",
                    "importes": [{"medio": "EFECTIVO", "importe": "100.00"}],
                },
            ),
        )
        order_thread = Thread(
            target=post,
            args=(
                "order",
                reverse("pedido-mesa-create"),
                {
                    "mesa": mesa.pk,
                    "detalles": [{"producto": producto.pk, "cantidad": 1}],
                },
            ),
        )
        payment_thread.start()
        order_thread.start()
        payment_thread.join(timeout=10)
        order_thread.join(timeout=10)

        self.assertFalse(payment_thread.is_alive())
        self.assertFalse(order_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results["order"].status_code, status.HTTP_201_CREATED)
        cuenta.refresh_from_db()
        if results["payment"].status_code == status.HTTP_201_CREATED:
            self.assertEqual(cuenta.estado, Cuenta.Estado.CERRADA)
            self.assertEqual(cuenta.pedidos.count(), 1)
            self.assertEqual(
                Cuenta.objects.filter(mesa=mesa, estado=Cuenta.Estado.ABIERTA).count(),
                1,
            )
        else:
            self.assertEqual(results["payment"].status_code, status.HTTP_409_CONFLICT)
            self.assertEqual(cuenta.estado, Cuenta.Estado.ABIERTA)
            self.assertEqual(cuenta.pedidos.count(), 2)

    @skipUnlessDBFeature("has_select_for_update")
    def test_doble_envio_concurrente_devuelve_un_unico_cobro(self):
        salon = Sector.objects.create(nombre=Sector.Nombre.SALON)
        barra = Sector.objects.create(nombre=Sector.Nombre.BARRA)
        mozo = User.objects.create_user(
            username="cobro_duplicado_concurrente",
            password="test-password",
            rol=User.Rol.MOZO,
            sector=salon,
        )
        mesa = Mesa.objects.create(numero=132)
        cuenta = Cuenta.objects.create(mesa=mesa, mozo_responsable=mozo)
        categoria = Categoria.objects.create(nombre="Cobro duplicado")
        producto = Producto.objects.create(
            nombre="Producto duplicado",
            precio=Decimal("100.00"),
            categoria=categoria,
            sector_destino=barra,
        )
        pedido = Pedido.objects.create(mesa=mesa, cuenta=cuenta, mozo=mozo)
        DetallePedido.objects.create(pedido=pedido, producto=producto, cantidad=1)
        preparar_pedido_para_cierre(pedido)
        barrier = Barrier(2)
        results = []
        errors = []
        key = uuid4()

        def post_cobro():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(user=User.objects.get(pk=mozo.pk))
                barrier.wait(timeout=5)
                results.append(
                    client.post(
                        reverse("cuenta-cobrar", args=[cuenta.pk]),
                        {
                            "clave_idempotencia": key,
                            "total_esperado": "100.00",
                            "importes": [
                                {"medio": "EFECTIVO", "importe": "100.00"},
                            ],
                        },
                        format="json",
                    ),
                )
            except Exception as error:
                errors.append(error)
            finally:
                close_old_connections()

        threads = [Thread(target=post_cobro) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(result.status_code for result in results), [200, 201])
        self.assertEqual(CobroCuenta.objects.filter(cuenta=cuenta).count(), 1)
        self.assertEqual(ImportePagoCuenta.objects.filter(cobro__cuenta=cuenta).count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_cobro_concurrente_con_resolucion_no_cierra_con_total_desactualizado(self):
        salon = Sector.objects.create(nombre=Sector.Nombre.SALON)
        barra = Sector.objects.create(nombre=Sector.Nombre.BARRA)
        mozo = User.objects.create_user(
            username="cobro_cancelacion_concurrente",
            password="test-password",
            rol=User.Rol.MOZO,
            sector=salon,
        )
        usuario_barra = User.objects.create_user(
            username="barra_cobro_concurrente",
            password="test-password",
            rol=User.Rol.BARRA,
            sector=barra,
        )
        mesa = Mesa.objects.create(numero=133)
        cuenta = Cuenta.objects.create(mesa=mesa, mozo_responsable=mozo)
        categoria = Categoria.objects.create(nombre="Resolución concurrente")
        producto = Producto.objects.create(
            nombre="Producto resolución",
            precio=Decimal("100.00"),
            categoria=categoria,
            sector_destino=barra,
        )
        pedido = Pedido.objects.create(mesa=mesa, cuenta=cuenta, mozo=mozo)
        detalle = DetallePedido.objects.create(
            pedido=pedido,
            producto=producto,
            cantidad=1,
        )
        preparar_pedido_para_cierre(pedido)
        solicitud = SolicitudCancelacionDetalle.objects.create(
            detalle=detalle,
            solicitante=mozo,
            clave_idempotencia=uuid4(),
            cantidad=1,
            motivo="Resolución concurrente",
        )
        barrier = Barrier(2)
        results = {}
        errors = []

        def post(name, user, url, payload):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(user=User.objects.get(pk=user.pk))
                barrier.wait(timeout=5)
                results[name] = client.post(url, payload, format="json")
            except Exception as error:
                errors.append(error)
            finally:
                close_old_connections()

        payment_thread = Thread(
            target=post,
            args=(
                "payment",
                mozo,
                reverse("cuenta-cobrar", args=[cuenta.pk]),
                {
                    "clave_idempotencia": uuid4(),
                    "total_esperado": "100.00",
                    "importes": [{"medio": "EFECTIVO", "importe": "100.00"}],
                },
            ),
        )
        resolution_thread = Thread(
            target=post,
            args=(
                "resolution",
                usuario_barra,
                reverse("cancelacion-resolver", args=[solicitud.pk]),
                {"estado": SolicitudCancelacionDetalle.Estado.ACEPTADA},
            ),
        )
        payment_thread.start()
        resolution_thread.start()
        payment_thread.join(timeout=10)
        resolution_thread.join(timeout=10)

        self.assertFalse(payment_thread.is_alive())
        self.assertFalse(resolution_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results["payment"].status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(results["resolution"].status_code, status.HTTP_200_OK)
        cuenta.refresh_from_db()
        solicitud.refresh_from_db()
        self.assertEqual(cuenta.estado, Cuenta.Estado.ABIERTA)
        self.assertEqual(solicitud.estado, SolicitudCancelacionDetalle.Estado.ACEPTADA)
        self.assertFalse(CobroCuenta.objects.filter(cuenta=cuenta).exists())
