
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import TestCase

from productos.models import Categoria, Producto
from usuarios.models import Sector, User

from .models import (
    AvisoRetiro,
    AvisoCargaBarra,
    Cuenta,
    DetallePedido,
    Mesa,
    Pedido,
    PreparacionPedidoSector,
)

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
            {"estado": PreparacionPedidoSector.Estado.EN_PREPARACION},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(
            response.data["estado"],
            PreparacionPedidoSector.Estado.EN_PREPARACION,
        )

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
            (PreparacionPedidoSector.Estado.PENDIENTE, PreparacionPedidoSector.Estado.LISTO),
            (PreparacionPedidoSector.Estado.PENDIENTE, PreparacionPedidoSector.Estado.PENDIENTE),
            (PreparacionPedidoSector.Estado.EN_PREPARACION, PreparacionPedidoSector.Estado.PENDIENTE),
            (PreparacionPedidoSector.Estado.EN_PREPARACION, PreparacionPedidoSector.Estado.EN_PREPARACION),
            (PreparacionPedidoSector.Estado.LISTO, PreparacionPedidoSector.Estado.EN_PREPARACION),
            (PreparacionPedidoSector.Estado.LISTO, PreparacionPedidoSector.Estado.PENDIENTE),
            (PreparacionPedidoSector.Estado.LISTO, PreparacionPedidoSector.Estado.LISTO),
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

    def marcar_lista(self, preparacion):
        usuarios = {
            Sector.Nombre.BARRA: self.usuario_barra,
            Sector.Nombre.PIZZA: self.cocina_pizza,
            Sector.Nombre.PLATOS: self.cocina_platos,
        }
        self.client.force_authenticate(
            user=usuarios[preparacion.sector.nombre],
        )
        for estado in [
            PreparacionPedidoSector.Estado.EN_PREPARACION,
            PreparacionPedidoSector.Estado.LISTO,
        ]:
            response = self.client.patch(
                self.estado_url(preparacion),
                {"estado": estado},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def confirmar_retiro(self, pedido, usuario=None):
        self.client.force_authenticate(user=usuario or self.usuario_barra)
        return self.client.post(self.confirmacion_url(pedido), format="json")

    def test_barra_only_enables_retiro_when_its_preparation_becomes_ready(self):
        pedido, preparaciones = self.crear_pedido(
            [self.barra],
            47,
            zona=Mesa.Zona.VEREDA_A,
        )

        self.marcar_lista(preparaciones[Sector.Nombre.BARRA])

        pedido.refresh_from_db()
        aviso = AvisoRetiro.objects.get(pedido=pedido)
        self.assertIsNotNone(pedido.retiro_habilitado_en)
        self.assertEqual(pedido.retiro_habilitado_por, self.usuario_barra)
        self.assertEqual(aviso.destinatario, self.mozo)
        self.assertEqual(aviso.mensaje, "Vereda A · Mesa 47")
        self.assertNotEqual(pedido.mesa_id, pedido.mesa.numero)

    def test_kitchen_only_order_waits_for_bar_confirmation(self):
        pedido, preparaciones = self.crear_pedido([self.pizza], 48)
        self.marcar_lista(preparaciones[Sector.Nombre.PIZZA])

        pedido.refresh_from_db()
        self.assertTrue(pedido.cocina_lista)
        self.assertIsNone(pedido.retiro_habilitado_en)
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
            response.data[0]["estados_sectores"],
            [{"sector": Sector.Nombre.PIZZA, "estado": "LISTO"}],
        )
        self.assertTrue(response.data[0]["puede_habilitar_retiro"])

        confirmation = self.confirmar_retiro(pedido)
        self.assertEqual(confirmation.status_code, status.HTTP_200_OK)
        pedido.refresh_from_db()
        self.assertEqual(pedido.retiro_habilitado_por, self.usuario_barra)

    def test_bar_ready_before_kitchen_does_not_create_early_notice(self):
        pedido, preparaciones = self.crear_pedido(
            [self.barra, self.pizza],
            49,
        )

        self.marcar_lista(preparaciones[Sector.Nombre.BARRA])

        pedido.refresh_from_db()
        self.assertIsNone(pedido.retiro_habilitado_en)
        self.assertFalse(AvisoRetiro.objects.filter(pedido=pedido).exists())

        self.marcar_lista(preparaciones[Sector.Nombre.PIZZA])
        pedido.refresh_from_db()
        self.assertIsNone(pedido.retiro_habilitado_en)
        self.assertFalse(AvisoRetiro.objects.filter(pedido=pedido).exists())

        response = self.confirmar_retiro(pedido)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            AvisoRetiro.objects.get(pedido=pedido).mensaje,
            "Salón · Mesa 49",
        )

    def test_pizza_and_platos_must_both_be_ready_before_confirmation(self):
        pedido, preparaciones = self.crear_pedido(
            [self.pizza, self.platos],
            50,
        )
        self.marcar_lista(preparaciones[Sector.Nombre.PIZZA])

        response = self.confirmar_retiro(pedido)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        pedido.refresh_from_db()
        self.assertFalse(pedido.cocina_lista)
        self.assertIsNone(pedido.retiro_habilitado_en)
        self.assertFalse(AvisoRetiro.objects.filter(pedido=pedido).exists())

        self.marcar_lista(preparaciones[Sector.Nombre.PLATOS])
        self.assertTrue(pedido.cocina_lista)
        response = self.confirmar_retiro(pedido)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_premature_confirmation_changes_nothing(self):
        pedido, _ = self.crear_pedido([self.pizza], 51)

        response = self.confirmar_retiro(pedido)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        pedido.refresh_from_db()
        self.assertIsNone(pedido.retiro_habilitado_en)
        self.assertIsNone(pedido.retiro_habilitado_por_id)
        self.assertFalse(AvisoRetiro.objects.filter(pedido=pedido).exists())

    def test_order_without_preparations_cannot_be_enabled_or_listed(self):
        pedido, _ = self.crear_pedido([], 52)

        response = self.confirmar_retiro(pedido)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.client.get(self.control_url).data, [])
        pedido.refresh_from_db()
        self.assertIsNone(pedido.retiro_habilitado_en)
        self.assertFalse(AvisoRetiro.objects.filter(pedido=pedido).exists())

    def test_repeated_confirmation_preserves_original_bar_user_and_timestamp(self):
        pedido, preparaciones = self.crear_pedido([self.pizza], 53)
        self.marcar_lista(preparaciones[Sector.Nombre.PIZZA])
        first_response = self.confirmar_retiro(pedido)
        self.assertEqual(first_response.status_code, status.HTTP_200_OK)

        pedido.refresh_from_db()
        aviso = AvisoRetiro.objects.get(pedido=pedido)
        fecha_original = pedido.retiro_habilitado_en
        fecha_aviso_original = aviso.fecha_creacion
        otro_usuario = self.otra_barra
        second_response = self.confirmar_retiro(pedido, otro_usuario)

        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        pedido.refresh_from_db()
        aviso.refresh_from_db()
        self.assertEqual(pedido.retiro_habilitado_en, fecha_original)
        self.assertEqual(pedido.retiro_habilitado_por, self.usuario_barra)
        self.assertEqual(aviso.fecha_creacion, fecha_aviso_original)
        self.assertEqual(AvisoRetiro.objects.filter(pedido=pedido).count(), 1)

    def test_confirming_after_account_closes_does_not_revalidate_order_creation(self):
        pedido, preparaciones = self.crear_pedido([self.pizza], 54)
        self.marcar_lista(preparaciones[Sector.Nombre.PIZZA])
        pedido.cuenta.cerrar()

        response = self.confirmar_retiro(pedido)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        pedido.refresh_from_db()
        self.assertIsNotNone(pedido.retiro_habilitado_en)

    def test_notice_failure_rolls_back_retiro_habilitation(self):
        pedido, preparaciones = self.crear_pedido([self.pizza], 55)
        self.marcar_lista(preparaciones[Sector.Nombre.PIZZA])
        self.client.force_authenticate(user=self.usuario_barra)

        with patch.object(AvisoRetiro, "save", side_effect=RuntimeError("fallo")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.confirmacion_url(pedido), format="json")

        pedido.refresh_from_db()
        self.assertIsNone(pedido.retiro_habilitado_en)
        self.assertIsNone(pedido.retiro_habilitado_por_id)
        self.assertFalse(AvisoRetiro.objects.filter(pedido=pedido).exists())

    def test_bar_control_uses_username_when_waiter_has_no_name(self):
        pedido, preparaciones = self.crear_pedido(
            [self.pizza],
            56,
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
        self.assertEqual(response.data[0]["mensaje"], "Salón · Mesa 57")

        self.client.force_authenticate(user=self.otro_mozo)
        response = self.client.get(self.avisos_url)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["pedido"], segundo_pedido.pk)
        self.assertEqual(response.data[0]["mensaje"], "Salón · Mesa 58")

    def test_bar_control_and_confirmation_are_exclusive_to_barra(self):
        pedido, preparaciones = self.crear_pedido([self.pizza], 59)
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
