"""
Rutas de movimientos, historial y dashboard (Etapas 4 y 5).

El flujo de stock negativo merece explicacion, porque es el unico caso del MVP
donde una operacion se interrumpe para preguntar:

1. El usuario envia una salida mayor al stock.
2. El motor detecta que quedaria negativo, revierte y lanza la excepcion.
3. Esta ruta atrapa la excepcion y devuelve la MISMA pantalla con una
   advertencia, un campo de motivo obligatorio y una casilla de confirmacion.
4. Si el usuario confirma, se reenvia con confirmar_negativo=True y ahora si se
   registra, marcado como incidencia.

En ningun momento se guarda algo a medias: el paso 2 revierte la transaccion.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alcance import AlcanceEmpresa
from dependencias import identidad, obtener_sesion, redirigir_a_entrada
from modelos import SIGNO_POR_TIPO, Empresa, Movimiento, Producto, Variante
from servicios.indicadores import (
    concentracion_del_valor,
    entradas_y_salidas,
    flujo_diario,
    resumen_completo,
    stock_por_atributo,
)
from servicios.movimientos import (
    CantidadInvalida,
    MotivoRequerido,
    MovimientoNoCancelable,
    StockNegativoRequiereConfirmacion,
    TipoInvalido,
    VarianteNoDisponible,
    cancelar_movimiento,
    registrar_movimiento,
)
from servicios.productos import descripcion_variante, descripciones_de
from vistas import templates

router = APIRouter()

ETIQUETA_TIPO = {
    "entrada": "Entrada",
    "salida": "Salida",
    "ajuste_positivo": "Ajuste positivo",
    "ajuste_negativo": "Ajuste negativo",
}


# ---------------------------------------------------------------------------
# Registrar un movimiento
# ---------------------------------------------------------------------------


def _contexto_movimiento(sesion: Session, variante: Variante, **extra) -> dict:
    producto = sesion.get(Producto, variante.producto_id)
    contexto = {
        "variante": variante,
        "producto": producto,
        "descripcion": descripcion_variante(sesion, variante),
        "tipos": ETIQUETA_TIPO,
    }
    contexto.update(extra)
    return contexto


@router.get("/variantes/{variante_id}/movimiento", response_class=HTMLResponse)
def mostrar_movimiento(
    variante_id: uuid.UUID,
    request: Request,
    sesion: Session = Depends(obtener_sesion),
):
    datos = identidad(request)
    if datos is None:
        return redirigir_a_entrada()

    alcance = AlcanceEmpresa(sesion, datos["empresa"])
    variante = alcance.obtener(Variante, variante_id)
    if variante is None:
        return RedirectResponse("/inventario", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="movimiento_nuevo.html",
        context=_contexto_movimiento(sesion, variante, datos={}),
    )


@router.post("/variantes/{variante_id}/movimiento")
def procesar_movimiento(
    variante_id: uuid.UUID,
    request: Request,
    tipo: str = Form(...),
    cantidad: str = Form(...),
    motivo: str = Form(""),
    confirmar_negativo: str = Form(""),
    sesion: Session = Depends(obtener_sesion),
):
    identificado = identidad(request)
    if identificado is None:
        return redirigir_a_entrada()

    alcance = AlcanceEmpresa(sesion, identificado["empresa"])
    variante = alcance.obtener(Variante, variante_id)
    if variante is None:
        return RedirectResponse("/inventario", status_code=303)

    formulario = {"tipo": tipo, "cantidad": cantidad, "motivo": motivo}

    def responder(codigo: int = 400, **extra):
        return templates.TemplateResponse(
            request=request,
            name="movimiento_nuevo.html",
            context=_contexto_movimiento(sesion, variante, datos=formulario, **extra),
            status_code=codigo,
        )

    # La cantidad llega como texto para poder distinguir "abc" de un entero y dar
    # un mensaje util en lugar de un error 422 generico de validacion.
    try:
        unidades = int(cantidad)
    except (TypeError, ValueError):
        return responder(error="La cantidad debe ser un numero entero.")

    try:
        registrar_movimiento(
            sesion,
            identificado["empresa"],
            variante.id,
            tipo,
            unidades,
            motivo=motivo or None,
            confirmar_negativo=bool(confirmar_negativo),
        )
    except StockNegativoRequiereConfirmacion as aviso:
        # No es un error: es una decision del propietario. Se le muestra el
        # numero exacto al que quedaria y se le pide motivo.
        return responder(
            codigo=200,
            advertencia_negativo=True,
            stock_resultante=aviso.stock_posterior,
        )
    except MotivoRequerido as problema:
        return responder(
            codigo=200,
            advertencia_negativo=True,
            stock_resultante=variante.stock + SIGNO_POR_TIPO.get(tipo, 0) * unidades,
            error=str(problema),
        )
    except (CantidadInvalida, TipoInvalido, VarianteNoDisponible) as problema:
        return responder(error=str(problema))

    return RedirectResponse("/historial", status_code=303)


# ---------------------------------------------------------------------------
# Historial y cancelacion
# ---------------------------------------------------------------------------


POR_PAGINA = 50


def _filas_historial(
    sesion: Session,
    empresa_id: uuid.UUID,
    *,
    tipo: str = "",
    solo_incidencias: bool = False,
    desde: date | None = None,
    hasta: date | None = None,
    pagina: int = 1,
    por_pagina: int = POR_PAGINA,
    vista: str = "operativo",
) -> tuple[list[dict], dict]:
    """
    Historial con filtros y paginacion, mas reciente primero.

    Antes esta funcion cortaba en 300 filas SIN avisar: un usuario con 400
    movimientos creia estar viendo todo su historial y no era cierto. Un limite
    silencioso es peor que una pantalla que dice cuantas paginas hay, porque el
    usuario toma decisiones sobre datos incompletos sin saberlo.

    Devuelve (filas, paginacion) donde paginacion trae el total real, la pagina
    actual y cuantas paginas existen.
    """
    consulta = (
        select(Movimiento)
        .where(Movimiento.empresa_id == empresa_id)
        .order_by(Movimiento.registrado_en.desc(), Movimiento.id)
    )

    # Dos audiencias, dos vistas.
    #
    # "operativo" responde a la pregunta del dueño: que mercancia se movio de
    # verdad. Oculta los movimientos cancelados y sus compensaciones, porque son
    # correcciones administrativas y no entradas ni salidas reales.
    #
    # "auditoria" responde a la pregunta del contador: que ocurrio en el sistema,
    # incluidos los errores y como se corrigieron.
    #
    # Ningun dato se pierde en ningun caso: la diferencia es que se muestra por
    # omision. Mostrar el registro contable a quien solo queria el resumen es lo
    # que hacia confusa la pantalla.
    if vista != "auditoria":
        consulta = consulta.where(
            Movimiento.cancelado.is_(False), Movimiento.compensa_a.is_(None)
        )
    if tipo in SIGNO_POR_TIPO:
        consulta = consulta.where(Movimiento.tipo == tipo)
    if solo_incidencias:
        consulta = consulta.where(Movimiento.es_incidencia.is_(True))
    if desde:
        consulta = consulta.where(Movimiento.registrado_en >= desde)
    if hasta:
        consulta = consulta.where(Movimiento.registrado_en <= hasta)

    # El total se cuenta con los MISMOS filtros: es lo que permite decir
    # "pagina 2 de 9" en lugar de esconder el corte.
    total = sesion.scalar(
        select(func.count()).select_from(consulta.order_by(None).subquery())
    ) or 0

    paginas = max((total + por_pagina - 1) // por_pagina, 1)
    pagina = min(max(pagina, 1), paginas)
    desplazamiento = (pagina - 1) * por_pagina

    movimientos = list(
        sesion.scalars(consulta.limit(por_pagina).offset(desplazamiento)).all()
    )

    # Variantes, productos y descripciones en tres consultas, no en tres por fila.
    variantes = {
        v.id: v
        for v in sesion.scalars(
            select(Variante).where(
                Variante.id.in_([m.variante_id for m in movimientos])
            )
        ).all()
    }
    productos = {
        p.id: p
        for p in sesion.scalars(
            select(Producto).where(
                Producto.id.in_([v.producto_id for v in variantes.values()])
            )
        ).all()
    }
    descripciones = descripciones_de(sesion, list(variantes))

    filas = []
    for movimiento in movimientos:
        variante = variantes.get(movimiento.variante_id)
        producto = productos.get(variante.producto_id) if variante else None
        filas.append(
            {
                "movimiento": movimiento,
                "variante": variante,
                "producto": producto,
                "descripcion": descripciones.get(movimiento.variante_id, ""),
                "etiqueta": ETIQUETA_TIPO.get(movimiento.tipo, movimiento.tipo),
                # Solo se puede cancelar un original vivo.
                "cancelable": (
                    not movimiento.cancelado and movimiento.compensa_a is None
                ),
            }
        )
    return filas, {
        "total": total,
        "pagina": pagina,
        "paginas": paginas,
        "por_pagina": por_pagina,
        "primero": desplazamiento + 1 if total else 0,
        "ultimo": min(desplazamiento + por_pagina, total),
        "vista": vista,
    }


def _contexto_historial(
    sesion: Session, empresa_id: uuid.UUID, vista: str = "operativo"
) -> dict:
    """Contexto del historial sin filtros, para las respuestas de error."""
    filas, paginacion = _filas_historial(sesion, empresa_id, vista=vista)
    return {
        "filas": filas,
        "paginacion": paginacion,
        "tipos": ETIQUETA_TIPO,
        "tipo_activo": "",
        "solo_incidencias": False,
        "vista": vista,
        "flujo": entradas_y_salidas(sesion, empresa_id),
    }


@router.get("/historial", response_class=HTMLResponse)
def ver_historial(
    request: Request,
    tipo: str = "",
    incidencias: int = 0,
    pagina: int = 1,
    vista: str = "operativo",
    sesion: Session = Depends(obtener_sesion),
):
    datos = identidad(request)
    if datos is None:
        return redirigir_a_entrada()

    empresa = sesion.get(Empresa, datos["empresa"])
    filas, paginacion = _filas_historial(
        sesion,
        datos["empresa"],
        tipo=tipo,
        solo_incidencias=bool(incidencias),
        pagina=pagina,
        vista=vista,
    )
    return templates.TemplateResponse(
        request=request,
        name="historial.html",
        context={
            "empresa": empresa,
            "filas": filas,
            "paginacion": paginacion,
            "vista": vista,
            "tipos": ETIQUETA_TIPO,
            "tipo_activo": tipo,
            "solo_incidencias": bool(incidencias),
            "flujo": entradas_y_salidas(sesion, datos["empresa"]),
        },
    )


def _cancelacion_con_error(
    request: Request,
    sesion: Session,
    movimiento_id: uuid.UUID,
    mensaje: str,
    *,
    permitir_negativo: bool = False,
):
    """Vuelve a la pantalla de cancelacion mostrando que salio mal."""
    respuesta = mostrar_cancelacion(movimiento_id, request, sesion)
    if not hasattr(respuesta, "context"):
        return respuesta
    respuesta.context["error"] = mensaje
    respuesta.context["permitir_negativo"] = permitir_negativo
    respuesta.status_code = 400
    return templates.TemplateResponse(
        request=request, name="cancelar.html", context=respuesta.context, status_code=400
    )


@router.get("/movimientos/{movimiento_id}/cancelar", response_class=HTMLResponse)
def mostrar_cancelacion(
    movimiento_id: uuid.UUID,
    request: Request,
    sesion: Session = Depends(obtener_sesion),
):
    """
    Pantalla dedicada para cancelar un movimiento.

    Antes el formulario vivia dentro de una celda de la tabla del historial. Dos
    problemas: el aviso del navegador por el campo obligatorio quedaba recortado
    por el scroll horizontal de la tabla (parecia que el boton no hacia nada), y
    el boton decia "Cancelar", que en cualquier interfaz significa "abortar" y no
    "revertir este movimiento".

    Una pantalla aparte permite ademas mostrar exactamente que va a pasar con el
    stock antes de confirmar.
    """
    datos = identidad(request)
    if datos is None:
        return redirigir_a_entrada()

    movimiento = sesion.scalars(
        select(Movimiento).where(
            Movimiento.id == movimiento_id,
            Movimiento.empresa_id == datos["empresa"],
        )
    ).first()
    if movimiento is None:
        return RedirectResponse("/historial", status_code=303)

    variante = sesion.get(Variante, movimiento.variante_id)
    producto = sesion.get(Producto, variante.producto_id) if variante else None

    return templates.TemplateResponse(
        request=request,
        name="cancelar.html",
        context={
            "movimiento": movimiento,
            "variante": variante,
            "producto": producto,
            "descripcion": descripcion_variante(sesion, variante) if variante else "",
            "etiqueta": ETIQUETA_TIPO.get(movimiento.tipo, movimiento.tipo),
            # Lo que quedara despues de compensar: el stock actual menos el
            # efecto del movimiento original.
            "stock_resultante": variante.stock - movimiento.delta if variante else 0,
            "cancelable": (
                not movimiento.cancelado and movimiento.compensa_a is None
            ),
        },
    )


@router.post("/movimientos/{movimiento_id}/cancelar")
def procesar_cancelacion(
    movimiento_id: uuid.UUID,
    request: Request,
    motivo: str = Form(...),
    confirmar_negativo: str = Form(""),
    sesion: Session = Depends(obtener_sesion),
):
    datos = identidad(request)
    if datos is None:
        return redirigir_a_entrada()

    try:
        cancelar_movimiento(
            sesion,
            datos["empresa"],
            movimiento_id,
            motivo,
            confirmar_negativo=bool(confirmar_negativo),
        )
    except (MovimientoNoCancelable, MotivoRequerido) as problema:
        return _cancelacion_con_error(request, sesion, movimiento_id, str(problema))
    except StockNegativoRequiereConfirmacion as aviso:
        return _cancelacion_con_error(
            request,
            sesion,
            movimiento_id,
            f"Revertir este movimiento dejaria el stock en {aviso.stock_posterior}. "
            "Registra primero una entrada, o confirma la incidencia marcando la "
            "casilla de abajo.",
            permitir_negativo=True,
        )

    return RedirectResponse("/historial", status_code=303)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/panel", response_class=HTMLResponse)
def ver_panel(request: Request, sesion: Session = Depends(obtener_sesion)):
    datos = identidad(request)
    if datos is None:
        return redirigir_a_entrada()

    empresa = sesion.get(Empresa, datos["empresa"])
    resumen = resumen_completo(sesion, datos["empresa"])

    # Los movimientos recientes se enriquecen con el nombre del producto para que
    # la lista sea legible sin abrir cada uno.
    # Mismo criterio que en el historial: por lotes, no fila por fila.
    variantes = {
        v.id: v
        for v in sesion.scalars(
            select(Variante).where(
                Variante.id.in_([m.variante_id for m in resumen["recientes"]])
            )
        ).all()
    }
    productos = {
        p.id: p
        for p in sesion.scalars(
            select(Producto).where(
                Producto.id.in_([v.producto_id for v in variantes.values()])
            )
        ).all()
    }
    descripciones = descripciones_de(sesion, list(variantes))

    recientes = []
    for movimiento in resumen["recientes"]:
        variante = variantes.get(movimiento.variante_id)
        producto = productos.get(variante.producto_id) if variante else None
        recientes.append(
            {
                "movimiento": movimiento,
                "etiqueta": ETIQUETA_TIPO.get(movimiento.tipo, movimiento.tipo),
                "producto": producto.nombre if producto else "",
                "descripcion": descripciones.get(movimiento.variante_id, ""),
            }
        )

    # Series para las graficas. Se calculan aparte de los indicadores porque
    # responden preguntas distintas: los indicadores dicen COMO ESTA el
    # inventario, las graficas dicen COMO SE DISTRIBUYE y COMO SE MUEVE.
    por_atributo = stock_por_atributo(sesion, datos["empresa"])
    diario = flujo_diario(sesion, datos["empresa"])
    concentracion = concentracion_del_valor(sesion, datos["empresa"])

    return templates.TemplateResponse(
        request=request,
        name="panel.html",
        context={
            "empresa": empresa,
            "r": resumen,
            "recientes": recientes,
            "por_atributo": por_atributo,
            "diario": diario,
            "tope_diario": max(
                [max(d["entradas"], d["salidas"]) for d in diario] or [0]
            ),
            "concentracion": concentracion,
        },
    )
