"""
Reportes y filtros (Etapa 6).

Decision de diseño que resuelve un riesgo concreto de la planeacion
("exportaciones distintas a los filtros"): cada reporte es UNA funcion que
devuelve un objeto `Reporte` con titulo, columnas y filas. La pantalla y los
archivos Excel y PDF consumen exactamente ese mismo objeto.

Como no existen dos consultas —una para mostrar y otra para exportar— es
imposible que el archivo descargado muestre un subconjunto distinto del que se ve
en pantalla. Si hay un error, esta en los dos lados por igual y se corrige en un
solo lugar.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from modelos import (
    SIGNO_POR_TIPO,
    Categoria,
    Movimiento,
    Producto,
    ValorAtributo,
    Variante,
    VarianteValor,
)

ETIQUETA_TIPO = {
    "entrada": "Entrada",
    "salida": "Salida",
    "ajuste_positivo": "Ajuste positivo",
    "ajuste_negativo": "Ajuste negativo",
}


@dataclass
class Filtros:
    """
    Los filtros de negocio de la planeacion, en un solo objeto.

    Van juntos y no como parametros sueltos para que la pantalla, el Excel y el
    PDF reciban literalmente la misma instancia. Un filtro que se olvide de pasar
    a una de las tres salidas seria justo el bug que queremos imposibilitar.
    """

    desde: date | None = None
    hasta: date | None = None
    categoria: str = ""
    producto_id: uuid.UUID | None = None
    estado: str = ""  # disponible | bajo | agotado
    solo_incidencias: bool = False
    tipo: str = ""

    def descripcion(self) -> str:
        """Texto legible de los filtros aplicados, para el encabezado y el PDF."""
        partes = []
        if self.desde:
            partes.append(f"desde {self.desde.strftime('%d/%m/%Y')}")
        if self.hasta:
            partes.append(f"hasta {self.hasta.strftime('%d/%m/%Y')}")
        if self.categoria:
            partes.append(f"categoria {self.categoria}")
        if self.estado:
            partes.append(f"estado {self.estado}")
        if self.tipo:
            partes.append(f"tipo {ETIQUETA_TIPO.get(self.tipo, self.tipo)}")
        if self.solo_incidencias:
            partes.append("solo incidencias")
        return ", ".join(partes) or "sin filtros"

    def limites(self):
        """Convierte las fechas en instantes con hora, o None."""
        inicio = (
            datetime.combine(self.desde, time.min, tzinfo=UTC)
            if self.desde
            else None
        )
        fin = (
            datetime.combine(self.hasta, time.max, tzinfo=UTC)
            if self.hasta
            else None
        )
        return inicio, fin


@dataclass
class Reporte:
    """Forma unica que consumen la pantalla, el Excel y el PDF."""

    clave: str
    titulo: str
    columnas: list[str]
    filas: list[list] = field(default_factory=list)
    nota: str = ""
    alineadas_derecha: set[int] = field(default_factory=set)

    @property
    def vacio(self) -> bool:
        return not self.filas


# ---------------------------------------------------------------------------
# Utilidades compartidas
# ---------------------------------------------------------------------------


def _descripcion_variante(sesion: Session, variante_id: uuid.UUID) -> str:
    valores = sesion.scalars(
        select(ValorAtributo.valor)
        .join(VarianteValor, VarianteValor.valor_atributo_id == ValorAtributo.id)
        .where(VarianteValor.variante_id == variante_id)
    ).all()
    return " / ".join(valores) if valores else "Simple"


def _estado(stock_total: int, minimo: int) -> str:
    if stock_total <= 0:
        return "Agotado"
    if stock_total <= minimo:
        return "Stock bajo"
    return "Disponible"


def _productos_filtrados(sesion: Session, empresa_id: uuid.UUID, filtros: Filtros):
    """Productos activos de la empresa que pasan los filtros de catalogo."""
    consulta = (
        select(Producto, func.coalesce(Categoria.nombre, "Sin categoria"))
        .outerjoin(Categoria, Categoria.id == Producto.categoria_id)
        .where(Producto.empresa_id == empresa_id, Producto.activo.is_(True))
        .order_by(Producto.nombre)
    )
    if filtros.categoria:
        consulta = consulta.where(Categoria.nombre == filtros.categoria)
    if filtros.producto_id:
        consulta = consulta.where(Producto.id == filtros.producto_id)
    return sesion.execute(consulta).all()


def _stock_de(sesion: Session, producto_id: uuid.UUID) -> int:
    total = sesion.scalar(
        select(func.coalesce(func.sum(Variante.stock), 0)).where(
            Variante.producto_id == producto_id, Variante.activa.is_(True)
        )
    )
    return int(total or 0)


# ---------------------------------------------------------------------------
# 1. Inventario actual
# ---------------------------------------------------------------------------


def inventario_actual(
    sesion: Session, empresa_id: uuid.UUID, filtros: Filtros
) -> Reporte:
    """Una fila por variante, con su stock y su valor a costo."""
    reporte = Reporte(
        clave="inventario",
        titulo="Inventario actual",
        columnas=["Producto", "Variante", "SKU", "Categoria", "Stock", "Costo", "Valor"],
        alineadas_derecha={4, 5, 6},
        nota="Una fila por variante activa. El valor es stock por costo unitario.",
    )

    for producto, categoria in _productos_filtrados(sesion, empresa_id, filtros):
        if filtros.estado:
            if _estado(_stock_de(sesion, producto.id), producto.minimo).lower() != (
                {"disponible": "disponible", "bajo": "stock bajo", "agotado": "agotado"}
                .get(filtros.estado, filtros.estado)
                .lower()
            ):
                continue

        variantes = sesion.scalars(
            select(Variante)
            .where(Variante.producto_id == producto.id, Variante.activa.is_(True))
            .order_by(Variante.sku)
        ).all()

        for variante in variantes:
            reporte.filas.append(
                [
                    producto.nombre,
                    _descripcion_variante(sesion, variante.id),
                    variante.sku,
                    categoria,
                    variante.stock,
                    float(producto.costo),
                    variante.stock * float(producto.costo),
                ]
            )
    return reporte


# ---------------------------------------------------------------------------
# 2. Historial de movimientos
# ---------------------------------------------------------------------------


def historial_movimientos(
    sesion: Session, empresa_id: uuid.UUID, filtros: Filtros
) -> Reporte:
    """Auditoria completa: incluye cancelados y compensaciones."""
    reporte = Reporte(
        clave="movimientos",
        titulo="Historial de movimientos",
        columnas=[
            "Fecha",
            "Producto",
            "Variante",
            "SKU",
            "Tipo",
            "Cantidad",
            "Antes",
            "Delta",
            "Despues",
            "Estado",
            "Motivo",
        ],
        alineadas_derecha={5, 6, 7, 8},
        nota=(
            "Incluye cancelaciones y compensaciones: es un reporte de auditoria, "
            "no de actividad comercial."
        ),
    )

    inicio, fin = filtros.limites()
    consulta = (
        select(Movimiento)
        .where(Movimiento.empresa_id == empresa_id)
        .order_by(Movimiento.registrado_en.desc(), Movimiento.id)
    )
    if inicio is not None:
        consulta = consulta.where(Movimiento.registrado_en >= inicio)
    if fin is not None:
        consulta = consulta.where(Movimiento.registrado_en <= fin)
    if filtros.tipo in SIGNO_POR_TIPO:
        consulta = consulta.where(Movimiento.tipo == filtros.tipo)
    if filtros.solo_incidencias:
        consulta = consulta.where(Movimiento.es_incidencia.is_(True))

    for movimiento in sesion.scalars(consulta).all():
        variante = sesion.get(Variante, movimiento.variante_id)
        producto = sesion.get(Producto, variante.producto_id) if variante else None

        if filtros.producto_id and (not producto or producto.id != filtros.producto_id):
            continue
        if filtros.categoria:
            categoria = (
                sesion.get(Categoria, producto.categoria_id)
                if producto and producto.categoria_id
                else None
            )
            nombre = categoria.nombre if categoria else "Sin categoria"
            if nombre != filtros.categoria:
                continue

        estados = []
        if movimiento.es_incidencia:
            estados.append("Incidencia")
        if movimiento.cancelado:
            estados.append("Cancelado")
        if movimiento.compensa_a:
            estados.append("Compensacion")

        reporte.filas.append(
            [
                movimiento.registrado_en.strftime("%d/%m/%Y %H:%M"),
                producto.nombre if producto else "",
                _descripcion_variante(sesion, movimiento.variante_id),
                variante.sku if variante else "",
                ETIQUETA_TIPO.get(movimiento.tipo, movimiento.tipo),
                movimiento.cantidad,
                movimiento.stock_anterior,
                movimiento.delta,
                movimiento.stock_posterior,
                " / ".join(estados) or "Vigente",
                movimiento.motivo or "",
            ]
        )
    return reporte


# ---------------------------------------------------------------------------
# 3. Valor del inventario
# ---------------------------------------------------------------------------


def valor_del_inventario(
    sesion: Session, empresa_id: uuid.UUID, filtros: Filtros
) -> Reporte:
    """Valor a costo agregado por producto, con su participacion en el total."""
    reporte = Reporte(
        clave="valor",
        titulo="Valor del inventario",
        columnas=["Producto", "Categoria", "Unidades", "Costo", "Valor", "% del total"],
        alineadas_derecha={2, 3, 4, 5},
        nota="El porcentaje se calcula sobre el total del subconjunto filtrado.",
    )

    acumulado = []
    for producto, categoria in _productos_filtrados(sesion, empresa_id, filtros):
        unidades = _stock_de(sesion, producto.id)
        if filtros.estado and _estado(unidades, producto.minimo).lower() != (
            {"disponible": "disponible", "bajo": "stock bajo", "agotado": "agotado"}
            .get(filtros.estado, filtros.estado)
            .lower()
        ):
            continue
        acumulado.append(
            [producto.nombre, categoria, unidades, float(producto.costo), unidades * float(producto.costo)]
        )

    total = sum(fila[4] for fila in acumulado) or 1
    for fila in sorted(acumulado, key=lambda f: f[4], reverse=True):
        reporte.filas.append(fila + [round(fila[4] / total * 100, 1)])
    return reporte


# ---------------------------------------------------------------------------
# 4. Mayor y menor movimiento
# ---------------------------------------------------------------------------


def movimiento_por_producto(
    sesion: Session, empresa_id: uuid.UUID, filtros: Filtros
) -> Reporte:
    """
    Ranking por volumen, de mayor a menor.

    Excluye cancelados y compensaciones: son correcciones, no actividad. El
    volumen suma valores absolutos, asi que entrar 100 y salir 100 cuenta como
    200 de movimiento y no como cero.
    """
    reporte = Reporte(
        clave="ranking",
        titulo="Productos con mayor y menor movimiento",
        columnas=["Producto", "Categoria", "Movimientos", "Entradas", "Salidas", "Volumen"],
        alineadas_derecha={2, 3, 4, 5},
        nota=(
            "Ordenado de mayor a menor volumen. Se excluyen movimientos cancelados "
            "y sus compensaciones."
        ),
    )

    inicio, fin = filtros.limites()
    condiciones = [
        Producto.empresa_id == empresa_id,
        Movimiento.cancelado.is_(False),
        Movimiento.compensa_a.is_(None),
    ]
    if inicio is not None:
        condiciones.append(Movimiento.registrado_en >= inicio)
    if fin is not None:
        condiciones.append(Movimiento.registrado_en <= fin)
    if filtros.producto_id:
        condiciones.append(Producto.id == filtros.producto_id)
    if filtros.categoria:
        condiciones.append(func.coalesce(Categoria.nombre, "Sin categoria") == filtros.categoria)

    filas = sesion.execute(
        select(
            Producto.nombre,
            func.coalesce(Categoria.nombre, "Sin categoria"),
            func.count(Movimiento.id),
            func.coalesce(
                func.sum(case((Movimiento.delta > 0, Movimiento.delta), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((Movimiento.delta < 0, -Movimiento.delta), else_=0)), 0
            ),
            func.coalesce(func.sum(func.abs(Movimiento.delta)), 0).label("volumen"),
        )
        .select_from(Producto)
        .outerjoin(Categoria, Categoria.id == Producto.categoria_id)
        .join(Variante, Variante.producto_id == Producto.id)
        .join(Movimiento, Movimiento.variante_id == Variante.id)
        .where(*condiciones)
        .group_by(Producto.id, Producto.nombre, Categoria.nombre)
        .order_by(func.coalesce(func.sum(func.abs(Movimiento.delta)), 0).desc())
    ).all()

    for nombre, categoria, cuantos, entradas, salidas, volumen in filas:
        reporte.filas.append(
            [nombre, categoria, int(cuantos), int(entradas), int(salidas), int(volumen)]
        )
    return reporte


# ---------------------------------------------------------------------------
# 5. Productos que requieren reposicion
# ---------------------------------------------------------------------------


def requieren_reposicion(
    sesion: Session, empresa_id: uuid.UUID, filtros: Filtros
) -> Reporte:
    """Productos cuyo stock total esta en o por debajo de su minimo."""
    reporte = Reporte(
        clave="reposicion",
        titulo="Productos que requieren reposicion",
        columnas=["Producto", "Categoria", "Stock", "Minimo", "Faltante", "Estado"],
        alineadas_derecha={2, 3, 4},
        nota=(
            "El minimo pertenece al producto: se compara contra la suma de sus "
            "variantes activas. El faltante es cuanto hay que reponer para "
            "superar el minimo."
        ),
    )

    for producto, categoria in _productos_filtrados(sesion, empresa_id, filtros):
        unidades = _stock_de(sesion, producto.id)
        estado = _estado(unidades, producto.minimo)
        if estado == "Disponible":
            continue
        reporte.filas.append(
            [
                producto.nombre,
                categoria,
                unidades,
                producto.minimo,
                max(producto.minimo - unidades + 1, 1) if producto.minimo else 1,
                estado,
            ]
        )
    reporte.filas.sort(key=lambda fila: fila[2])
    return reporte


# ---------------------------------------------------------------------------
# 6. Entradas y salidas por periodo
# ---------------------------------------------------------------------------


def entradas_salidas_por_periodo(
    sesion: Session, empresa_id: uuid.UUID, filtros: Filtros
) -> Reporte:
    """
    Flujo agrupado por dia.

    Un periodo sin movimientos devuelve un reporte vacio con su nota, nunca un
    error de division: es el caso limite "periodo sin movimientos".
    """
    reporte = Reporte(
        clave="flujo",
        titulo="Entradas y salidas por periodo",
        columnas=["Fecha", "Movimientos", "Entradas", "Salidas", "Neto"],
        alineadas_derecha={1, 2, 3, 4},
        nota="Agrupado por dia. Excluye cancelados y compensaciones.",
    )

    inicio, fin = filtros.limites()
    dia = func.date(Movimiento.registrado_en)
    consulta = (
        select(
            dia.label("dia"),
            func.count(Movimiento.id),
            func.coalesce(
                func.sum(case((Movimiento.delta > 0, Movimiento.delta), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((Movimiento.delta < 0, -Movimiento.delta), else_=0)), 0
            ),
        )
        .where(
            Movimiento.empresa_id == empresa_id,
            Movimiento.cancelado.is_(False),
            Movimiento.compensa_a.is_(None),
        )
        .group_by(dia)
        .order_by(dia.desc())
    )
    if inicio is not None:
        consulta = consulta.where(Movimiento.registrado_en >= inicio)
    if fin is not None:
        consulta = consulta.where(Movimiento.registrado_en <= fin)
    if filtros.tipo in SIGNO_POR_TIPO:
        consulta = consulta.where(Movimiento.tipo == filtros.tipo)

    for fecha, cuantos, entradas, salidas in sesion.execute(consulta).all():
        entradas, salidas = int(entradas or 0), int(salidas or 0)
        reporte.filas.append(
            [str(fecha), int(cuantos), entradas, salidas, entradas - salidas]
        )
    return reporte


# ---------------------------------------------------------------------------
# Registro de reportes disponibles
# ---------------------------------------------------------------------------

REPORTES = {
    "inventario": ("Inventario actual", inventario_actual),
    "movimientos": ("Historial de movimientos", historial_movimientos),
    "valor": ("Valor del inventario", valor_del_inventario),
    "ranking": ("Mayor y menor movimiento", movimiento_por_producto),
    "reposicion": ("Requieren reposicion", requieren_reposicion),
    "flujo": ("Entradas y salidas por periodo", entradas_salidas_por_periodo),
}


def generar(
    clave: str, sesion: Session, empresa_id: uuid.UUID, filtros: Filtros
) -> Reporte:
    """
    Punto de entrada unico.

    La pantalla y las dos exportaciones llaman a esta misma funcion con los
    mismos filtros. De ahi viene la garantia de que muestran lo mismo.
    """
    if clave not in REPORTES:
        raise KeyError(clave)
    return REPORTES[clave][1](sesion, empresa_id, filtros)
