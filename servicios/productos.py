"""
Servicio de productos y variantes (Etapa 3).

Aqui vive el algoritmo del Pseudocodigo A de la planeacion. Tres piezas:

1. Generacion de SKU unico por empresa, con reintento si hay colision.
2. Producto cartesiano de los valores de atributos para crear variantes.
3. Creacion atomica: producto, atributos, valores y variantes se guardan juntos
   o no se guarda nada.

Complejidad del cartesiano: con k atributos de n1, n2 ... nk valores, el total
de variantes es n1 x n2 x ... x nk. Crece multiplicativamente, no de forma
lineal: dos atributos de 7 valores dan 49 variantes, pero tres de 10 dan 1000.
Por eso se calcula el total ANTES de crear nada y se rechaza si excede el limite.
"""

from __future__ import annotations

import itertools
import re
import unicodedata
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from modelos import (
    Atributo,
    Categoria,
    Movimiento,
    Producto,
    ValorAtributo,
    Variante,
    VarianteValor,
)

# Tope defensivo: el riesgo "explosion de combinaciones" de la planeacion.
MAXIMO_VARIANTES = 300


class DemasiadasVariantes(Exception):
    """La combinacion de atributos generaria mas variantes de las permitidas."""

    def __init__(self, total: int) -> None:
        self.total = total
        super().__init__(
            f"La configuracion generaria {total} variantes; el maximo es "
            f"{MAXIMO_VARIANTES}. Reduce valores o captura variantes a mano."
        )


class CombinacionRepetida(Exception):
    """Se intento crear dos veces la misma combinacion de valores."""


# ---------------------------------------------------------------------------
# SKU
# ---------------------------------------------------------------------------


def prefijo_desde_nombre(nombre: str) -> str:
    """
    Deriva un prefijo legible de 3 letras del nombre del producto.

    'Polo Premium' -> 'POL'. Se quitan acentos para que el SKU sea siempre
    ASCII y no cause problemas al exportar a Excel o CSV.
    """
    sin_acentos = "".join(
        caracter
        for caracter in unicodedata.normalize("NFD", nombre)
        if unicodedata.category(caracter) != "Mn"
    )
    letras = re.sub(r"[^A-Za-z]", "", sin_acentos).upper()
    return (letras[:3] or "SKU").ljust(3, "X")


def generar_sku(sesion: Session, empresa_id: uuid.UUID, nombre_producto: str) -> str:
    """
    Genera un SKU unico dentro de la empresa.

    El consecutivo arranca en el total de variantes existentes y avanza hasta
    encontrar un hueco libre. Es el caso limite "SKU generado ya existe" de la
    planeacion: no se asume que el consecutivo esta libre, se verifica.
    """
    prefijo = prefijo_desde_nombre(nombre_producto)
    existentes = sesion.scalar(
        select(func.count(Variante.id)).where(Variante.empresa_id == empresa_id)
    )
    consecutivo = (existentes or 0) + 1

    while True:
        candidato = f"{prefijo}-{consecutivo:04d}"
        ocupado = sesion.scalars(
            select(Variante.id).where(
                Variante.empresa_id == empresa_id, Variante.sku == candidato
            )
        ).first()
        if ocupado is None:
            return candidato
        consecutivo += 1


# ---------------------------------------------------------------------------
# Producto cartesiano
# ---------------------------------------------------------------------------


def combinaciones(atributos: list[dict]) -> list[tuple[str, ...]]:
    """
    Producto cartesiano de los valores de cada atributo.

    Entrada: [{'nombre': 'Talla', 'valores': ['S','M']},
              {'nombre': 'Color', 'valores': ['negro','blanco']}]
    Salida:  [('S','negro'), ('S','blanco'), ('M','negro'), ('M','blanco')]

    Caso limite de la planeacion: un atributo sin valores impide generar, porque
    el producto cartesiano con un conjunto vacio da cero combinaciones y el
    producto quedaria sin ninguna variante.
    """
    if not atributos:
        return []
    listas = []
    for atributo in atributos:
        valores = [v.strip() for v in atributo["valores"] if v.strip()]
        if not valores:
            raise ValueError(
                f"El atributo '{atributo['nombre']}' no tiene valores. "
                "Agrega al menos uno para generar variantes."
            )
        if len(set(valores)) != len(valores):
            raise ValueError(
                f"El atributo '{atributo['nombre']}' tiene valores repetidos."
            )
        listas.append(valores)
    return list(itertools.product(*listas))


def contar_combinaciones(atributos: list[dict]) -> int:
    """
    Calcula cuantas variantes se crearian SIN crearlas.

    Se usa para mostrar el total al usuario antes de confirmar, tal como pide la
    mitigacion de riesgo de la planeacion.
    """
    total = 1
    for atributo in atributos:
        valores = [v for v in atributo["valores"] if v.strip()]
        total *= len(valores)
    return total if atributos else 0


# ---------------------------------------------------------------------------
# Creacion del producto
# ---------------------------------------------------------------------------


def crear_producto(
    sesion: Session,
    empresa_id: uuid.UUID,
    nombre: str,
    *,
    categoria: str | None = None,
    unidad: str = "pieza",
    costo: float = 0,
    minimo: int = 0,
    atributos: list[dict] | None = None,
    existencia_inicial: int = 0,
) -> Producto:
    """
    Crea un producto simple o con variantes en una sola transaccion.

    Producto simple (sin atributos): se crea exactamente una variante base, para
    que el resto del sistema no tenga que distinguir casos. Todo el inventario
    vive siempre en variantes.

    existencia_inicial aplica la MISMA cantidad a todas las variantes, asi que
    solo es util para productos simples. Para un producto con variantes, las
    cantidades se capturan una por una despues, en la pantalla de existencias
    (ver servicios.movimientos.cargar_existencias): 5 medianas azules y 4 grandes
    azules son cifras distintas y cada una necesita su propio movimiento.
    """
    nombre = nombre.strip()
    if not nombre:
        raise ValueError("El nombre del producto es obligatorio.")
    if costo < 0:
        raise ValueError("El costo no puede ser negativo.")
    if minimo < 0:
        raise ValueError("El minimo de reposicion no puede ser negativo.")
    if existencia_inicial < 0 or int(existencia_inicial) != existencia_inicial:
        raise ValueError("La existencia inicial debe ser un entero mayor o igual a 0.")

    atributos = atributos or []

    # Se valida el tamaño antes de tocar la base de datos.
    if atributos:
        total = contar_combinaciones(atributos)
        if total > MAXIMO_VARIANTES:
            raise DemasiadasVariantes(total)

    categoria_id = None
    if categoria and categoria.strip():
        categoria_id = _obtener_o_crear_categoria(sesion, empresa_id, categoria.strip())

    producto = Producto(
        empresa_id=empresa_id,
        categoria_id=categoria_id,
        nombre=nombre,
        unidad=unidad.strip() or "pieza",
        costo=costo,
        minimo=minimo,
        es_variable=bool(atributos),
    )
    sesion.add(producto)
    sesion.flush()

    if not atributos:
        variantes = [_crear_variante(sesion, producto, [], [])]
    else:
        valores_por_atributo = _crear_atributos(sesion, producto, atributos)
        variantes = []
        vistas: set[tuple[str, ...]] = set()
        for combinacion in combinaciones(atributos):
            if combinacion in vistas:
                raise CombinacionRepetida(str(combinacion))
            vistas.add(combinacion)
            ids_valores = [
                valores_por_atributo[atributos[posicion]["nombre"]][valor]
                for posicion, valor in enumerate(combinacion)
            ]
            variantes.append(_crear_variante(sesion, producto, list(combinacion), ids_valores))

    if existencia_inicial > 0:
        for variante in variantes:
            _registrar_inventario_inicial(sesion, variante, int(existencia_inicial))

    sesion.commit()
    return producto


def _obtener_o_crear_categoria(
    sesion: Session, empresa_id: uuid.UUID, nombre: str
) -> uuid.UUID:
    """Reutiliza la categoria si ya existe en esta empresa; si no, la crea."""
    existente = sesion.scalars(
        select(Categoria).where(
            Categoria.empresa_id == empresa_id, Categoria.nombre == nombre
        )
    ).first()
    if existente:
        return existente.id
    categoria = Categoria(empresa_id=empresa_id, nombre=nombre)
    sesion.add(categoria)
    sesion.flush()
    return categoria.id


def _crear_atributos(
    sesion: Session, producto: Producto, atributos: list[dict]
) -> dict[str, dict[str, uuid.UUID]]:
    """Persiste atributos y valores; devuelve un indice nombre -> valor -> id."""
    indice: dict[str, dict[str, uuid.UUID]] = {}
    for definicion in atributos:
        atributo = Atributo(producto_id=producto.id, nombre=definicion["nombre"].strip())
        sesion.add(atributo)
        sesion.flush()
        indice[atributo.nombre] = {}
        for valor in definicion["valores"]:
            valor = valor.strip()
            if not valor:
                continue
            fila = ValorAtributo(atributo_id=atributo.id, valor=valor)
            sesion.add(fila)
            sesion.flush()
            indice[atributo.nombre][valor] = fila.id
    return indice


def _crear_variante(
    sesion: Session,
    producto: Producto,
    valores: list[str],
    ids_valores: list[uuid.UUID],
) -> Variante:
    """Crea una variante con SKU unico y la enlaza con sus valores."""
    variante = Variante(
        empresa_id=producto.empresa_id,
        producto_id=producto.id,
        sku=generar_sku(sesion, producto.empresa_id, producto.nombre),
        stock=0,
    )
    sesion.add(variante)
    sesion.flush()
    for id_valor in ids_valores:
        sesion.add(VarianteValor(variante_id=variante.id, valor_atributo_id=id_valor))
    return variante


def _registrar_inventario_inicial(sesion: Session, variante: Variante, cantidad: int):
    """
    El inventario inicial entra como movimiento, no como edicion del stock.

    Delega en el motor de movimientos para que exista un unico lugar en todo el
    sistema que modifica el stock de una variante.
    """
    from servicios.movimientos import registrar_movimiento

    registrar_movimiento(
        sesion,
        variante.empresa_id,
        variante.id,
        "entrada",
        cantidad,
        motivo="Inventario inicial",
        confirmar=False,
    )


def descripcion_variante(sesion: Session, variante: Variante) -> str:
    """Texto legible de una variante: 'M / negro', o su SKU si es simple."""
    valores = sesion.scalars(
        select(ValorAtributo.valor)
        .join(VarianteValor, VarianteValor.valor_atributo_id == ValorAtributo.id)
        .where(VarianteValor.variante_id == variante.id)
    ).all()
    return " / ".join(valores) if valores else "Producto simple"
