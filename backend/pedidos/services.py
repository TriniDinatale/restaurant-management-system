from decimal import Decimal

from django.db.models import Exists, F, IntegerField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from .models import (
    AvisoRetiro,
    DetallePedido,
    PreparacionPedidoSector,
    Sector,
    SolicitudCancelacionDetalle,
)


def cantidades_cancelacion(detalle):
    cache = getattr(detalle, "_cantidades_cancelacion_cache", None)
    if cache is not None:
        return cache

    canceladas = 0
    pendientes = 0
    for solicitud in detalle.solicitudes_cancelacion.all():
        if solicitud.estado == SolicitudCancelacionDetalle.Estado.ACEPTADA:
            canceladas += solicitud.cantidad
        elif solicitud.estado == SolicitudCancelacionDetalle.Estado.PENDIENTE:
            pendientes += solicitud.cantidad

    cache = {
        "canceladas": canceladas,
        "pendientes": pendientes,
        "vigentes": detalle.cantidad - canceladas,
        "disponibles": detalle.cantidad - canceladas - pendientes,
    }
    detalle._cantidades_cancelacion_cache = cache
    return cache


def cantidad_vigente(detalle):
    return cantidades_cancelacion(detalle)["vigentes"]


def cantidad_disponible_cancelacion(detalle):
    return cantidades_cancelacion(detalle)["disponibles"]


def total_vigente_cuenta(cuenta):
    return sum(
        (
            detalle.precio_unitario * cantidad_vigente(detalle)
            for pedido in cuenta.pedidos.all()
            for detalle in pedido.detalles.all()
        ),
        Decimal("0.00"),
    )


def productos_pendientes_para_cierre(cuenta):
    """Describe los productos vigentes que impiden cobrar y cerrar la cuenta.

    Un producto vigente de BARRA está listo para el cierre cuando su preparación
    está LISTO y existe el aviso de retiro de BARRA. Los productos de PIZZA y
    PLATOS no pasan por una pantalla de cocina: les basta el aviso de retiro de
    COCINA, que Barra confirma manualmente al verificarlos físicamente.
    """
    estados_preparacion = {
        (prep.pedido_id, prep.sector_id): prep.estado
        for prep in PreparacionPedidoSector.objects.filter(
            pedido__cuenta=cuenta,
        )
    }
    retiros_habilitados = set(
        AvisoRetiro.objects.filter(
            pedido__cuenta=cuenta,
            origen__isnull=False,
        ).values_list("pedido_id", "origen")
    )
    detalles = (
        DetallePedido.objects.filter(pedido__cuenta=cuenta)
        .select_related("producto", "sector_destino")
        .prefetch_related("solicitudes_cancelacion")
        .order_by("pedido_id", "pk")
    )

    pendientes = []
    for detalle in detalles:
        cantidad = cantidad_vigente(detalle)
        if cantidad <= 0:
            continue
        origen = origen_entrega(detalle)
        retiro_habilitado = (
            origen is not None
            and (detalle.pedido_id, origen) in retiros_habilitados
        )
        if origen == AvisoRetiro.Origen.COCINA:
            if retiro_habilitado:
                continue
            motivo = "sin retiro habilitado por Barra"
        else:
            estado = estados_preparacion.get(
                (detalle.pedido_id, detalle.sector_destino_id),
            )
            if estado != PreparacionPedidoSector.Estado.LISTO:
                motivo = (
                    "en preparación"
                    if estado == PreparacionPedidoSector.Estado.EN_PREPARACION
                    else "pendiente de preparación"
                )
            elif not retiro_habilitado:
                motivo = "listo, sin retiro habilitado por Barra"
            else:
                continue
        pendientes.append(
            f"Pedido {detalle.pedido_id}: {cantidad} x "
            f"{detalle.producto.nombre} ({motivo})"
        )
    return pendientes


def origen_entrega(detalle):
    if detalle.sector_destino.nombre == Sector.Nombre.BARRA:
        return AvisoRetiro.Origen.BARRA
    if detalle.sector_destino.nombre in {
        Sector.Nombre.PIZZA,
        Sector.Nombre.PLATOS,
    }:
        return AvisoRetiro.Origen.COCINA
    return None


def estado_entrega(detalle):
    if cantidad_vigente(detalle) == 0:
        return "CANCELADO"

    origen = origen_entrega(detalle)
    if origen is None:
        return "PENDIENTE"

    avisos = detalle.pedido.avisos_retiro.all()
    if any(aviso.origen == origen for aviso in avisos):
        return "RETIRO_HABILITADO"
    return "PENDIENTE"


def resumen_detalle_consumo(detalle):
    cantidades = cantidades_cancelacion(detalle)
    estado = estado_entrega(detalle)
    disponibles = cantidades["disponibles"]
    if estado == "RETIRO_HABILITADO":
        disponibles = 0
    return {
        **cantidades,
        "estado_entrega": estado,
        "puede_solicitar_cancelacion": (
            estado != "RETIRO_HABILITADO" and disponibles > 0
        ),
        "cantidad_disponible_cancelacion": disponibles,
    }


def detalles_vigentes_de_origen(pedido, origen, *, lock=False):
    sectores = {
        AvisoRetiro.Origen.BARRA: {Sector.Nombre.BARRA},
        AvisoRetiro.Origen.COCINA: {
            Sector.Nombre.PIZZA,
            Sector.Nombre.PLATOS,
        },
    }.get(origen, set())
    queryset = DetallePedido.objects.filter(
        pedido=pedido,
        sector_destino__nombre__in=sectores,
    ).select_related("sector_destino").order_by("pk")
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    return [
        detalle
        for detalle in queryset.prefetch_related("solicitudes_cancelacion")
        if cantidad_vigente(detalle) > 0
    ]


def detalles_vigentes_de_sector(pedido, sector_id, *, lock=False):
    queryset = DetallePedido.objects.filter(
        pedido=pedido,
        sector_destino_id=sector_id,
    ).select_related("sector_destino").order_by("pk")
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    return [
        detalle
        for detalle in queryset.prefetch_related("solicitudes_cancelacion")
        if cantidad_vigente(detalle) > 0
    ]


def detalles_vigentes_exist(
    en_pedido="pedido_id",
    en_sector="sector_id",
    sectores=None,
):
    cancelaciones_aceptadas = (
        SolicitudCancelacionDetalle.objects.filter(
            detalle_id=OuterRef("pk"),
            estado=SolicitudCancelacionDetalle.Estado.ACEPTADA,
        )
        .values("detalle_id")
        .annotate(total=Sum("cantidad"))
        .values("total")
    )
    detalles = DetallePedido.objects.filter(
        pedido_id=OuterRef(en_pedido),
    )
    if en_sector:
        detalles = detalles.filter(sector_destino_id=OuterRef(en_sector))
    if sectores is not None:
        detalles = detalles.filter(sector_destino__nombre__in=sectores)
    detalles = detalles.annotate(
        cantidad_cancelada=Coalesce(
            Subquery(
                cancelaciones_aceptadas,
                output_field=IntegerField(),
            ),
            Value(0),
            output_field=IntegerField(),
        ),
    ).filter(cantidad__gt=F("cantidad_cancelada"))
    return Exists(detalles)
