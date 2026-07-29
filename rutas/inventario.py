"""
Rutas de inventario y alta de productos (Etapas 2 y 3).

/inventario cumple doble funcion segun el estado de la empresa: si no hay
productos muestra el onboarding, y si ya hay muestra el catalogo. Es la
aplicacion del criterio "una pantalla vacia es una invitacion a actuar", no un
mensaje de que no hay nada.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from alcance import AlcanceEmpresa
from dependencias import identidad, obtener_sesion, redirigir_a_entrada
from modelos import Categoria, Empresa, Producto, Variante
from servicios.productos import (
    MAXIMO_VARIANTES,
    CombinacionRepetida,
    DemasiadasVariantes,
    contar_combinaciones,
    crear_producto,
    descripcion_variante,
)
from vistas import templates

router = APIRouter()


def _resumen_productos(sesion: Session, alcance: AlcanceEmpresa) -> list[dict]:
    """
    Arma el catalogo con el estado de stock de cada producto.

    El stock de un producto es la SUMA de sus variantes activas, y ese total es
    lo que se compara contra el minimo. Es la regla de la planeacion: el minimo
    pertenece al producto, no a la variante.
    """
    resumen = []
    for producto in alcance.todos(Producto):
        if not producto.activo:
            continue
        variantes = sesion.scalars(
            select(Variante).where(
                Variante.producto_id == producto.id, Variante.activa == True  # noqa: E712
            )
        ).all()
        stock_total = sum(v.stock for v in variantes)

        if stock_total <= 0:
            estado = "agotado"
        elif stock_total <= producto.minimo:
            estado = "bajo"
        else:
            estado = "disponible"

        resumen.append(
            {
                "producto": producto,
                "variantes": [
                    {"variante": v, "descripcion": descripcion_variante(sesion, v)}
                    for v in variantes
                ],
                "stock_total": stock_total,
                "valor": stock_total * float(producto.costo),
                "estado": estado,
            }
        )
    return resumen


@router.get("/inventario", response_class=HTMLResponse)
def ver_inventario(request: Request, sesion: Session = Depends(obtener_sesion)):
    datos = identidad(request)
    if datos is None:
        return redirigir_a_entrada()

    alcance = AlcanceEmpresa(sesion, datos["empresa"])
    empresa = sesion.get(Empresa, datos["empresa"])
    catalogo = _resumen_productos(sesion, alcance)

    return templates.TemplateResponse(
        request=request,
        name="inventario.html",
        context={
            "empresa": empresa,
            "catalogo": catalogo,
            "unidades": sum(fila["stock_total"] for fila in catalogo),
            "valor_total": sum(fila["valor"] for fila in catalogo),
            "con_alerta": sum(1 for fila in catalogo if fila["estado"] != "disponible"),
        },
    )


@router.get("/productos/nuevo", response_class=HTMLResponse)
def mostrar_alta(request: Request, sesion: Session = Depends(obtener_sesion)):
    datos = identidad(request)
    if datos is None:
        return redirigir_a_entrada()

    categorias = sesion.scalars(
        select(Categoria.nombre).where(Categoria.empresa_id == datos["empresa"])
    ).all()

    return templates.TemplateResponse(
        request=request,
        name="producto_nuevo.html",
        context={
            "datos": {},
            "categorias": list(categorias),
            "maximo_variantes": MAXIMO_VARIANTES,
        },
    )


def _leer_atributos(
    atributo_1: str, valores_1: str, atributo_2: str, valores_2: str
) -> list[dict]:
    """
    Convierte los dos campos del formulario en la estructura de atributos.

    El MVP permite hasta dos atributos, que es lo que necesita el caso KOVA
    (talla y color). Los valores llegan separados por coma.
    """
    atributos = []
    for nombre, valores in ((atributo_1, valores_1), (atributo_2, valores_2)):
        nombre = (nombre or "").strip()
        if not nombre:
            continue
        lista = [parte.strip() for parte in (valores or "").split(",") if parte.strip()]
        atributos.append({"nombre": nombre, "valores": lista})
    return atributos


@router.post("/productos/nuevo")
def procesar_alta(
    request: Request,
    nombre: str = Form(...),
    categoria: str = Form(""),
    unidad: str = Form("pieza"),
    costo: float = Form(0),
    minimo: int = Form(0),
    existencia_inicial: int = Form(0),
    atributo_1: str = Form(""),
    valores_1: str = Form(""),
    atributo_2: str = Form(""),
    valores_2: str = Form(""),
    sesion: Session = Depends(obtener_sesion),
):
    identificado = identidad(request)
    if identificado is None:
        return redirigir_a_entrada()

    formulario = {
        "nombre": nombre,
        "categoria": categoria,
        "unidad": unidad,
        "costo": costo,
        "minimo": minimo,
        "existencia_inicial": existencia_inicial,
        "atributo_1": atributo_1,
        "valores_1": valores_1,
        "atributo_2": atributo_2,
        "valores_2": valores_2,
    }

    def con_error(mensaje: str, codigo: int = 400):
        categorias = sesion.scalars(
            select(Categoria.nombre).where(
                Categoria.empresa_id == identificado["empresa"]
            )
        ).all()
        return templates.TemplateResponse(
            request=request,
            name="producto_nuevo.html",
            context={
                "datos": formulario,
                "categorias": list(categorias),
                "maximo_variantes": MAXIMO_VARIANTES,
                "error": mensaje,
            },
            status_code=codigo,
        )

    atributos = _leer_atributos(atributo_1, valores_1, atributo_2, valores_2)

    try:
        crear_producto(
            sesion,
            identificado["empresa"],
            nombre,
            categoria=categoria,
            unidad=unidad,
            costo=costo,
            minimo=minimo,
            atributos=atributos,
            existencia_inicial=existencia_inicial,
        )
    except DemasiadasVariantes as problema:
        return con_error(str(problema))
    except CombinacionRepetida:
        return con_error("Hay una combinacion repetida entre los valores capturados.")
    except ValueError as problema:
        return con_error(str(problema))

    return RedirectResponse("/inventario", status_code=303)


@router.post("/productos/vista-previa", response_class=HTMLResponse)
def vista_previa(
    request: Request,
    atributo_1: str = Form(""),
    valores_1: str = Form(""),
    atributo_2: str = Form(""),
    valores_2: str = Form(""),
):
    """
    Cuenta las variantes que se crearian, sin crear nada.

    Es la mitigacion del riesgo "explosion de combinaciones": el usuario ve el
    total antes de confirmar.
    """
    atributos = _leer_atributos(atributo_1, valores_1, atributo_2, valores_2)
    total = contar_combinaciones(atributos)
    return HTMLResponse(f"{total}")
