"""
Rutas de reportes y exportaciones (Etapa 6).

Las tres salidas —pantalla, Excel y PDF— pasan por `_reporte_desde_peticion`, que
construye los filtros a partir de los mismos parametros de URL. Los enlaces de
descarga en la pantalla arrastran la query string completa, asi que el archivo se
genera con los filtros que el usuario esta viendo, no con otros.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from dependencias import identidad, obtener_sesion, redirigir_a_entrada
from modelos import Categoria, Empresa, Producto
from servicios.exportar import a_excel, a_pdf, nombre_archivo
from servicios.reportes import ETIQUETA_TIPO, REPORTES, Filtros, generar

router = APIRouter()

TIPO_EXCEL = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _fecha(texto: str | None) -> date | None:
    """Lee una fecha de formulario; texto vacio o invalido se ignora."""
    if not texto:
        return None
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return None


def _uuid(texto: str | None) -> uuid.UUID | None:
    if not texto:
        return None
    try:
        return uuid.UUID(texto)
    except ValueError:
        return None


def _filtros_desde(request: Request) -> Filtros:
    """Construye los filtros desde la query string. Un solo lugar, tres salidas."""
    parametros = request.query_params
    return Filtros(
        desde=_fecha(parametros.get("desde")),
        hasta=_fecha(parametros.get("hasta")),
        categoria=parametros.get("categoria", "").strip(),
        producto_id=_uuid(parametros.get("producto")),
        estado=parametros.get("estado", "").strip(),
        solo_incidencias=parametros.get("incidencias") == "1",
        tipo=parametros.get("tipo", "").strip(),
    )


def _clave_valida(clave: str) -> str:
    return clave if clave in REPORTES else "inventario"


@router.get("/reportes", response_class=HTMLResponse)
def ver_reportes(
    request: Request,
    reporte: str = "inventario",
    sesion: Session = Depends(obtener_sesion),
):
    datos = identidad(request)
    if datos is None:
        return redirigir_a_entrada()

    clave = _clave_valida(reporte)
    filtros = _filtros_desde(request)
    empresa = sesion.get(Empresa, datos["empresa"])
    resultado = generar(clave, sesion, datos["empresa"], filtros)

    # La query string se reenvia tal cual a los enlaces de descarga: es lo que
    # garantiza que el archivo refleje exactamente lo que se ve en pantalla.
    parametros = str(request.query_params)

    return templates_reportes(
        request,
        empresa=empresa,
        clave=clave,
        reporte=resultado,
        filtros=filtros,
        parametros=parametros,
        categorias=list(
            sesion.scalars(
                select(Categoria.nombre)
                .where(Categoria.empresa_id == datos["empresa"])
                .order_by(Categoria.nombre)
            ).all()
        ),
        productos=list(
            sesion.execute(
                select(Producto.id, Producto.nombre)
                .where(
                    Producto.empresa_id == datos["empresa"], Producto.activo.is_(True)
                )
                .order_by(Producto.nombre)
            ).all()
        ),
    )


def templates_reportes(request: Request, **contexto):
    from vistas import templates

    contexto.update(
        {
            "reportes_disponibles": {
                clave: titulo for clave, (titulo, _) in REPORTES.items()
            },
            "tipos": ETIQUETA_TIPO,
        }
    )
    return templates.TemplateResponse(
        request=request, name="reportes.html", context=contexto
    )


@router.get("/reportes/{clave}.xlsx")
def descargar_excel(
    clave: str, request: Request, sesion: Session = Depends(obtener_sesion)
):
    datos = identidad(request)
    if datos is None:
        return redirigir_a_entrada()

    clave = _clave_valida(clave)
    filtros = _filtros_desde(request)
    empresa = sesion.get(Empresa, datos["empresa"])
    resultado = generar(clave, sesion, datos["empresa"], filtros)

    contenido = a_excel(resultado, filtros, empresa.nombre)
    return Response(
        content=contenido,
        media_type=TIPO_EXCEL,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{nombre_archivo(resultado, "xlsx")}"'
            )
        },
    )


@router.get("/reportes/{clave}.pdf")
def descargar_pdf(
    clave: str, request: Request, sesion: Session = Depends(obtener_sesion)
):
    datos = identidad(request)
    if datos is None:
        return redirigir_a_entrada()

    clave = _clave_valida(clave)
    filtros = _filtros_desde(request)
    empresa = sesion.get(Empresa, datos["empresa"])
    resultado = generar(clave, sesion, datos["empresa"], filtros)

    contenido = a_pdf(resultado, filtros, empresa.nombre)
    return Response(
        content=contenido,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{nombre_archivo(resultado, "pdf")}"'
            )
        },
    )
