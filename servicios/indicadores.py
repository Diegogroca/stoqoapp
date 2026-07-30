"""
Indicadores del dashboard (Etapa 5).

Los seis indicadores de la planeacion, calculados con agregaciones en SQL y no
trayendo todas las filas a memoria. Importa por dos razones: en una funcion
serverless la memoria es limitada, y una suma que Postgres hace con un indice es
mucho mas rapida que un bucle en Python sobre miles de movimientos.

Regla que atraviesa todo el modulo: los movimientos cancelados y sus
compensaciones se EXCLUYEN de los rankings de actividad, porque representan una
correccion y no movimiento real de mercancia. Siguen visibles en el historial de
auditoria, que es donde deben estar.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from modelos import Categoria, Movimiento, Producto, Variante


def _rango(desde: date | None, hasta: date | None):
    """Convierte fechas de formulario en limites con hora, o None."""
    inicio = (
        datetime.combine(desde, time.min, tzinfo=UTC) if desde else None
    )
    fin = datetime.combine(hasta, time.max, tzinfo=UTC) if hasta else None
    return inicio, fin


def unidades_disponibles(sesion: Session, empresa_id: uuid.UUID) -> int:
    """Indicador 1: suma del stock de todas las variantes activas."""
    total = sesion.scalar(
        select(func.coalesce(func.sum(Variante.stock), 0)).where(
            Variante.empresa_id == empresa_id, Variante.activa.is_(True)
        )
    )
    return int(total or 0)


def valor_inventario(sesion: Session, empresa_id: uuid.UUID) -> float:
    """
    Indicador 2: valor a costo, sumando stock x costo unitario del producto.

    Se hace con un JOIN y no en Python porque el costo vive en el producto y el
    stock en la variante: dejar que la base multiplique fila por fila evita
    traerse el catalogo completo.
    """
    total = sesion.scalar(
        select(func.coalesce(func.sum(Variante.stock * Producto.costo), 0))
        .join(Producto, Producto.id == Variante.producto_id)
        .where(
            Variante.empresa_id == empresa_id,
            Variante.activa.is_(True),
            Producto.activo.is_(True),
        )
    )
    return float(total or 0)


def estado_por_producto(sesion: Session, empresa_id: uuid.UUID) -> list[dict]:
    """
    Pseudocodigo D: estado de reposicion de cada producto.

    El minimo pertenece al PRODUCTO, no a la variante, asi que primero se suma el
    stock de las variantes activas y ese total se compara contra el minimo. Un
    polo con 2 unidades en cada una de sus 49 variantes tiene 98 en total y no
    esta bajo, aunque cada variante suelta parezca escasa.
    """
    filas = sesion.execute(
        select(
            Producto.id,
            Producto.nombre,
            Producto.minimo,
            func.coalesce(func.sum(Variante.stock), 0).label("stock_total"),
        )
        .outerjoin(
            Variante,
            (Variante.producto_id == Producto.id) & (Variante.activa.is_(True)),
        )
        .where(Producto.empresa_id == empresa_id, Producto.activo.is_(True))
        .group_by(Producto.id, Producto.nombre, Producto.minimo)
        .order_by(Producto.nombre)
    ).all()

    resultado = []
    for id_producto, nombre, minimo, stock_total in filas:
        stock_total = int(stock_total or 0)
        if stock_total <= 0:
            estado = "agotado"
        elif stock_total <= (minimo or 0):
            estado = "bajo"
        else:
            estado = "disponible"
        resultado.append(
            {
                "id": id_producto,
                "nombre": nombre,
                "minimo": int(minimo or 0),
                "stock_total": stock_total,
                "estado": estado,
            }
        )
    return resultado


def productos_con_alerta(sesion: Session, empresa_id: uuid.UUID) -> list[dict]:
    """Indicador 3: productos agotados o por debajo de su minimo."""
    return [
        fila
        for fila in estado_por_producto(sesion, empresa_id)
        if fila["estado"] != "disponible"
    ]


def _movimientos_operativos(empresa_id: uuid.UUID):
    """
    Base de consulta para actividad real.

    Excluye cancelados y compensaciones: son correcciones contables, no
    mercancia que se movio.
    """
    return select(Movimiento).where(
        Movimiento.empresa_id == empresa_id,
        Movimiento.cancelado.is_(False),
        Movimiento.compensa_a.is_(None),
    )


def entradas_y_salidas(
    sesion: Session,
    empresa_id: uuid.UUID,
    desde: date | None = None,
    hasta: date | None = None,
) -> dict:
    """
    Indicador 4: unidades que entraron y que salieron en el periodo.

    Se separan sumando condicionalmente el delta segun su signo, en una sola
    pasada, en lugar de hacer dos consultas.
    """
    inicio, fin = _rango(desde, hasta)

    consulta = select(
        func.coalesce(
            func.sum(case((Movimiento.delta > 0, Movimiento.delta), else_=0)), 0
        ).label("entradas"),
        func.coalesce(
            func.sum(case((Movimiento.delta < 0, -Movimiento.delta), else_=0)), 0
        ).label("salidas"),
        func.count(Movimiento.id).label("total"),
    ).where(
        Movimiento.empresa_id == empresa_id,
        Movimiento.cancelado.is_(False),
        Movimiento.compensa_a.is_(None),
    )
    if inicio is not None:
        consulta = consulta.where(Movimiento.registrado_en >= inicio)
    if fin is not None:
        consulta = consulta.where(Movimiento.registrado_en <= fin)

    entradas, salidas, total = sesion.execute(consulta).one()
    return {
        "entradas": int(entradas or 0),
        "salidas": int(salidas or 0),
        "movimientos": int(total or 0),
    }


def productos_mas_movidos(
    sesion: Session,
    empresa_id: uuid.UUID,
    limite: int = 5,
    *,
    ascendente: bool = False,
) -> list[dict]:
    """
    Indicador 5: ranking por volumen de movimiento.

    El volumen es la suma de VALORES ABSOLUTOS de los deltas: un producto que
    entro 100 y salio 100 tuvo mucha actividad aunque su neto sea cero.
    """
    volumen = func.coalesce(func.sum(func.abs(Movimiento.delta)), 0).label("volumen")
    orden = volumen.asc() if ascendente else volumen.desc()

    filas = sesion.execute(
        select(Producto.nombre, volumen, func.count(Movimiento.id).label("cuantos"))
        .join(Variante, Variante.producto_id == Producto.id)
        .join(
            Movimiento,
            (Movimiento.variante_id == Variante.id)
            & Movimiento.cancelado.is_(False)
            & Movimiento.compensa_a.is_(None),
        )
        .where(Producto.empresa_id == empresa_id)
        .group_by(Producto.id, Producto.nombre)
        .order_by(orden)
        .limit(limite)
    ).all()

    return [
        {"nombre": nombre, "volumen": int(vol or 0), "movimientos": int(cuantos or 0)}
        for nombre, vol, cuantos in filas
    ]


def resumen_por_categoria(sesion: Session, empresa_id: uuid.UUID) -> list[dict]:
    """Indicador 6: unidades y valor a costo agrupados por categoria."""
    filas = sesion.execute(
        select(
            func.coalesce(Categoria.nombre, "Sin categoria").label("categoria"),
            func.coalesce(func.sum(Variante.stock), 0).label("unidades"),
            func.coalesce(func.sum(Variante.stock * Producto.costo), 0).label("valor"),
        )
        .select_from(Producto)
        .outerjoin(Categoria, Categoria.id == Producto.categoria_id)
        .outerjoin(
            Variante,
            (Variante.producto_id == Producto.id) & (Variante.activa.is_(True)),
        )
        .where(Producto.empresa_id == empresa_id, Producto.activo.is_(True))
        .group_by(Categoria.nombre)
        .order_by(func.coalesce(Categoria.nombre, "Sin categoria"))
    ).all()

    return [
        {"categoria": categoria, "unidades": int(u or 0), "valor": float(v or 0)}
        for categoria, u, v in filas
    ]


def actividad_reciente(
    sesion: Session, empresa_id: uuid.UUID, limite: int = 10
) -> list[Movimiento]:
    """Ultimos movimientos registrados, incluyendo cancelaciones.

    Aqui SI se muestran las correcciones: esta lista es auditoria, no ranking.
    """
    return list(
        sesion.scalars(
            select(Movimiento)
            .where(Movimiento.empresa_id == empresa_id)
            .order_by(Movimiento.registrado_en.desc(), Movimiento.id)
            .limit(limite)
        ).all()
    )


def resumen_completo(sesion: Session, empresa_id: uuid.UUID) -> dict:
    """Arma los seis indicadores de un jalon para la pantalla del dashboard."""
    alertas = productos_con_alerta(sesion, empresa_id)
    return {
        "unidades": unidades_disponibles(sesion, empresa_id),
        "valor": valor_inventario(sesion, empresa_id),
        "alertas": alertas,
        "total_alertas": len(alertas),
        "flujo": entradas_y_salidas(sesion, empresa_id),
        "mas_movidos": productos_mas_movidos(sesion, empresa_id),
        "categorias": resumen_por_categoria(sesion, empresa_id),
        "recientes": actividad_reciente(sesion, empresa_id),
    }


# ---------------------------------------------------------------------------
# Series para graficas
# ---------------------------------------------------------------------------


def stock_por_atributo(sesion: Session, empresa_id: uuid.UUID) -> dict[str, list[dict]]:
    """
    Unidades disponibles agrupadas por cada valor de cada atributo.

    Responde una pregunta que el catalogo no contesta: "de que talla tengo mas
    inventario" o "que color se me acumula". Con 49 variantes esa informacion
    esta en la pantalla pero es ilegible; agregada por atributo se vuelve
    accionable.

    Una sola consulta para todos los atributos: se agrupa por (atributo, valor) y
    se reparte en Python.
    """
    from modelos import Atributo, ValorAtributo, VarianteValor

    filas = sesion.execute(
        select(
            Atributo.nombre,
            ValorAtributo.valor,
            func.coalesce(func.sum(Variante.stock), 0),
        )
        .select_from(VarianteValor)
        .join(ValorAtributo, ValorAtributo.id == VarianteValor.valor_atributo_id)
        .join(Atributo, Atributo.id == ValorAtributo.atributo_id)
        .join(Variante, Variante.id == VarianteValor.variante_id)
        .where(Variante.empresa_id == empresa_id, Variante.activa.is_(True))
        .group_by(Atributo.nombre, ValorAtributo.valor)
        .order_by(Atributo.nombre, func.coalesce(func.sum(Variante.stock), 0).desc())
    ).all()

    agrupado: dict[str, list[dict]] = {}
    for atributo, valor, unidades in filas:
        agrupado.setdefault(atributo, []).append(
            {"valor": valor, "unidades": int(unidades or 0)}
        )
    return agrupado


def flujo_diario(
    sesion: Session, empresa_id: uuid.UUID, dias: int = 14
) -> list[dict]:
    """
    Entradas y salidas por dia de los ultimos N dias.

    Se rellenan los dias sin movimiento con ceros: una grafica con huecos miente
    sobre el ritmo del negocio, porque un dia sin ventas parece no existir en
    lugar de aparecer como un valle.
    """
    hoy = datetime.now(UTC).date()
    primero = hoy - timedelta(days=dias - 1)
    inicio = datetime.combine(primero, time.min, tzinfo=UTC)

    dia = func.date(Movimiento.registrado_en)
    filas = sesion.execute(
        select(
            dia,
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
            Movimiento.registrado_en >= inicio,
        )
        .group_by(dia)
    ).all()

    por_dia = {
        str(fecha): {"entradas": int(e or 0), "salidas": int(s or 0)}
        for fecha, e, s in filas
    }

    serie = []
    for desplazamiento in range(dias):
        fecha = primero + timedelta(days=desplazamiento)
        datos = por_dia.get(str(fecha), {"entradas": 0, "salidas": 0})
        serie.append(
            {
                "fecha": fecha,
                "etiqueta": fecha.strftime("%d/%m"),
                "entradas": datos["entradas"],
                "salidas": datos["salidas"],
            }
        )
    return serie


def concentracion_del_valor(
    sesion: Session, empresa_id: uuid.UUID, limite: int = 8
) -> list[dict]:
    """
    Productos ordenados por el valor que inmovilizan, con porcentaje acumulado.

    Es el analisis ABC clasico de inventarios: normalmente una minoria de
    productos concentra la mayoria del dinero detenido en almacen. Saber cuales
    son dice donde vigilar el stock y donde se puede ser laxo.
    """
    filas = sesion.execute(
        select(
            Producto.nombre,
            func.coalesce(func.sum(Variante.stock * Producto.costo), 0).label("valor"),
        )
        .join(Variante, Variante.producto_id == Producto.id)
        .where(
            Producto.empresa_id == empresa_id,
            Producto.activo.is_(True),
            Variante.activa.is_(True),
        )
        .group_by(Producto.id, Producto.nombre)
        .order_by(func.coalesce(func.sum(Variante.stock * Producto.costo), 0).desc())
    ).all()

    total = sum(float(valor or 0) for _, valor in filas)
    if total <= 0:
        return []

    serie = []
    acumulado = 0.0
    for nombre, valor in filas[:limite]:
        valor = float(valor or 0)
        acumulado += valor
        serie.append(
            {
                "nombre": nombre,
                "valor": valor,
                "porcentaje": round(valor / total * 100, 1),
                "acumulado": round(acumulado / total * 100, 1),
            }
        )
    return serie
