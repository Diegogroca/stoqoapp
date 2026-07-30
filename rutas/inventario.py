"""
Rutas de inventario y alta de productos (Etapas 2 y 3).

/inventario cumple doble funcion segun el estado de la empresa: si no hay
productos muestra el onboarding, y si ya hay muestra el catalogo. Es la
aplicacion del criterio "una pantalla vacia es una invitacion a actuar", no un
mensaje de que no hay nada.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from alcance import AlcanceEmpresa
from dependencias import identidad, obtener_sesion, redirigir_a_entrada
from modelos import Categoria, Empresa, Producto, Variante
from servicios.movimientos import CantidadInvalida, cargar_existencias
from servicios.productos import (
    MAXIMO_VARIANTES,
    CombinacionRepetida,
    DemasiadasVariantes,
    actualizar_producto,
    contar_combinaciones,
    crear_producto,
    descripciones_de,
    reactivar_producto,
    retirar_producto,
)
from vistas import templates

router = APIRouter()


def _resumen_productos(
    sesion: Session, alcance: AlcanceEmpresa, *, retirados: bool = False
) -> list[dict]:
    """
    Arma el catalogo con el estado de stock de cada producto.

    El stock de un producto es la SUMA de sus variantes activas, y ese total es
    lo que se compara contra el minimo. Es la regla de la planeacion: el minimo
    pertenece al producto, no a la variante.
    """
    productos = [
        # Los productos retirados solo se listan cuando se piden explicitamente:
        # siguen existiendo para el historial, pero no estorban en el catalogo.
        producto
        for producto in alcance.todos(Producto)
        if producto.activo != retirados
    ]
    if not productos:
        return []

    # Todas las variantes del catalogo en UNA consulta, agrupadas en memoria.
    # Antes se consultaba producto por producto: con 10 productos eran 10
    # consultas mas 49 por las descripciones de cada variante.
    todas = sesion.scalars(
        select(Variante)
        .where(Variante.producto_id.in_([p.id for p in productos]))
        .order_by(Variante.sku)
    ).all()

    por_producto: dict = {}
    for variante in todas:
        por_producto.setdefault(variante.producto_id, []).append(variante)

    # Y todas las descripciones en UNA consulta mas.
    descripciones = descripciones_de(sesion, [v.id for v in todas])

    resumen = []
    for producto in productos:
        variantes = por_producto.get(producto.id, [])
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
                    {"variante": v, "descripcion": descripciones[v.id]}
                    for v in variantes
                ],
                "stock_total": stock_total,
                "valor": stock_total * float(producto.costo),
                "estado": estado,
            }
        )
    return resumen


@router.get("/inventario", response_class=HTMLResponse)
def ver_inventario(
    request: Request,
    retirados: int = 0,
    sesion: Session = Depends(obtener_sesion),
):
    datos = identidad(request)
    if datos is None:
        return redirigir_a_entrada()

    alcance = AlcanceEmpresa(sesion, datos["empresa"])
    empresa = sesion.get(Empresa, datos["empresa"])
    catalogo = _resumen_productos(sesion, alcance, retirados=bool(retirados))

    return templates.TemplateResponse(
        request=request,
        name="inventario.html",
        context={
            "empresa": empresa,
            "catalogo": catalogo,
            "viendo_retirados": bool(retirados),
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
        producto = crear_producto(
            sesion,
            identificado["empresa"],
            nombre,
            categoria=categoria,
            unidad=unidad,
            costo=costo,
            minimo=minimo,
            atributos=atributos,
        )
    except DemasiadasVariantes as problema:
        return con_error(str(problema))
    except CombinacionRepetida:
        return con_error("Hay una combinacion repetida entre los valores capturados.")
    except ValueError as problema:
        return con_error(str(problema))

    # El producto ya existe pero todavia no tiene existencias. Se manda a la
    # pantalla de captura por variante, porque cada combinacion tiene su propia
    # cantidad: 5 medianas azules no es lo mismo que 4 grandes azules.
    return RedirectResponse(
        f"/productos/{producto.id}/existencias", status_code=303
    )


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

# ---------------------------------------------------------------------------
# Captura de existencias por variante
# ---------------------------------------------------------------------------


def _variantes_del_producto(sesion: Session, producto: Producto) -> list[dict]:
    """Lista las variantes activas con su descripcion legible y su stock."""
    variantes = sesion.scalars(
        select(Variante)
        .where(Variante.producto_id == producto.id, Variante.activa.is_(True))
        .order_by(Variante.sku)
    ).all()
    descripciones = descripciones_de(sesion, [v.id for v in variantes])
    return [
        {"variante": variante, "descripcion": descripciones[variante.id]}
        for variante in variantes
    ]


@router.get("/productos/{producto_id}/existencias", response_class=HTMLResponse)
def mostrar_existencias(
    producto_id: uuid.UUID,
    request: Request,
    sesion: Session = Depends(obtener_sesion),
):
    datos = identidad(request)
    if datos is None:
        return redirigir_a_entrada()

    alcance = AlcanceEmpresa(sesion, datos["empresa"])
    producto = alcance.obtener(Producto, producto_id)
    if producto is None:
        # Un id de otra empresa se comporta como inexistente, no como prohibido.
        return RedirectResponse("/inventario", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="existencias.html",
        context={
            "producto": producto,
            "filas": _variantes_del_producto(sesion, producto),
        },
    )


@router.post("/productos/{producto_id}/existencias")
async def procesar_existencias(
    producto_id: uuid.UUID,
    request: Request,
    sesion: Session = Depends(obtener_sesion),
):
    """
    Aplica las cantidades capturadas variante por variante.

    Los campos del formulario llegan como cantidad_<id_de_variante>, asi que se
    leen dinamicamente: no se puede declarar un parametro por variante cuando el
    numero de variantes lo decide el usuario.
    """
    datos = identidad(request)
    if datos is None:
        return redirigir_a_entrada()

    alcance = AlcanceEmpresa(sesion, datos["empresa"])
    producto = alcance.obtener(Producto, producto_id)
    if producto is None:
        return RedirectResponse("/inventario", status_code=303)

    formulario = await request.form()
    motivo = (formulario.get("motivo") or "Inventario inicial").strip()

    cantidades: dict[uuid.UUID, int] = {}
    invalidos: list[str] = []

    for clave, valor in formulario.items():
        if not clave.startswith("cantidad_"):
            continue
        texto = (valor or "").strip()
        if not texto:
            continue
        try:
            cantidad = int(texto)
        except ValueError:
            invalidos.append(clave.removeprefix("cantidad_"))
            continue
        if cantidad < 0:
            invalidos.append(clave.removeprefix("cantidad_"))
            continue
        if cantidad > 0:
            # Solo se aceptan variantes que pertenezcan a esta empresa.
            variante = alcance.obtener(Variante, uuid.UUID(clave.removeprefix("cantidad_")))
            if variante is not None and variante.producto_id == producto.id:
                cantidades[variante.id] = cantidad

    def con_error(mensaje: str):
        return templates.TemplateResponse(
            request=request,
            name="existencias.html",
            context={
                "producto": producto,
                "filas": _variantes_del_producto(sesion, producto),
                "error": mensaje,
            },
            status_code=400,
        )

    if invalidos:
        return con_error(
            "Las cantidades deben ser numeros enteros mayores o iguales a cero."
        )

    if not cantidades:
        # No es un error: puede que el producto se cargue mas tarde.
        return RedirectResponse("/inventario", status_code=303)

    try:
        cargar_existencias(sesion, datos["empresa"], cantidades, motivo=motivo)
    except CantidadInvalida as problema:
        return con_error(str(problema))

    return RedirectResponse("/inventario", status_code=303)


# ---------------------------------------------------------------------------
# Editar y retirar
# ---------------------------------------------------------------------------


@router.get("/productos/{producto_id}/editar", response_class=HTMLResponse)
def mostrar_edicion(
    producto_id: uuid.UUID,
    request: Request,
    sesion: Session = Depends(obtener_sesion),
):
    datos = identidad(request)
    if datos is None:
        return redirigir_a_entrada()

    alcance = AlcanceEmpresa(sesion, datos["empresa"])
    producto = alcance.obtener(Producto, producto_id)
    if producto is None:
        return RedirectResponse("/inventario", status_code=303)

    categoria = None
    if producto.categoria_id:
        categoria = sesion.get(Categoria, producto.categoria_id)

    return templates.TemplateResponse(
        request=request,
        name="producto_editar.html",
        context={
            "producto": producto,
            "categoria_actual": categoria.nombre if categoria else "",
            "categorias": list(
                sesion.scalars(
                    select(Categoria.nombre).where(
                        Categoria.empresa_id == datos["empresa"]
                    )
                ).all()
            ),
            "filas": _variantes_del_producto(sesion, producto),
        },
    )


@router.post("/productos/{producto_id}/editar")
def procesar_edicion(
    producto_id: uuid.UUID,
    request: Request,
    nombre: str = Form(...),
    categoria: str = Form(""),
    unidad: str = Form("pieza"),
    costo: float = Form(0),
    minimo: int = Form(0),
    sesion: Session = Depends(obtener_sesion),
):
    datos = identidad(request)
    if datos is None:
        return redirigir_a_entrada()

    alcance = AlcanceEmpresa(sesion, datos["empresa"])
    producto = alcance.obtener(Producto, producto_id)
    if producto is None:
        return RedirectResponse("/inventario", status_code=303)

    try:
        actualizar_producto(
            sesion,
            producto,
            nombre=nombre,
            categoria=categoria,
            unidad=unidad,
            costo=costo,
            minimo=minimo,
        )
    except ValueError as problema:
        return templates.TemplateResponse(
            request=request,
            name="producto_editar.html",
            context={
                "producto": producto,
                "categoria_actual": categoria,
                "categorias": [],
                "filas": _variantes_del_producto(sesion, producto),
                "error": str(problema),
            },
            status_code=400,
        )

    return RedirectResponse("/inventario", status_code=303)


@router.post("/productos/{producto_id}/retirar")
def procesar_retiro(
    producto_id: uuid.UUID,
    request: Request,
    sesion: Session = Depends(obtener_sesion),
):
    """
    Retira el producto del catalogo conservando su historial (CE-18).

    No es un DELETE: los movimientos pasados siguen apuntando a estas variantes y
    un reporte de hace meses debe seguir siendo legible.
    """
    datos = identidad(request)
    if datos is None:
        return redirigir_a_entrada()

    alcance = AlcanceEmpresa(sesion, datos["empresa"])
    producto = alcance.obtener(Producto, producto_id)
    if producto is not None:
        retirar_producto(sesion, producto)

    return RedirectResponse("/inventario", status_code=303)


@router.post("/productos/{producto_id}/reactivar")
def procesar_reactivacion(
    producto_id: uuid.UUID,
    request: Request,
    sesion: Session = Depends(obtener_sesion),
):
    datos = identidad(request)
    if datos is None:
        return redirigir_a_entrada()

    alcance = AlcanceEmpresa(sesion, datos["empresa"])
    producto = alcance.obtener(Producto, producto_id)
    if producto is not None:
        reactivar_producto(sesion, producto)

    return RedirectResponse("/inventario?retirados=1", status_code=303)
