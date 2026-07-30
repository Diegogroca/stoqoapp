"""
Prueba de sintaxis de plantillas.

Existe por un error real: un `{% elif %}` colocado despues de un `{% else %}` es
sintaxis invalida de Jinja2, y ninguna prueba lo detecto porque el error solo
aparece al RENDERIZAR esa plantilla concreta con datos que entren por esa rama.
El resultado en produccion fue un "Internal Server Error" sin pista alguna.

Esta prueba compila todas las plantillas del proyecto. No verifica que se vean
bien, solo que sean sintacticamente validas, que es exactamente la clase de
fallo que se nos escapo.
"""

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, TemplateSyntaxError

DIRECTORIO = Path(__file__).resolve().parent.parent / "templates"

PLANTILLAS = sorted(archivo.name for archivo in DIRECTORIO.glob("*.html"))


def test_hay_plantillas_que_revisar():
    """Si el glob no encuentra nada, la prueba de abajo pasaria en falso."""
    assert PLANTILLAS, "No se encontraron plantillas en templates/"


@pytest.mark.parametrize("nombre", PLANTILLAS)
def test_la_plantilla_compila(nombre: str):
    entorno = Environment(loader=FileSystemLoader(str(DIRECTORIO)))
    try:
        entorno.get_template(nombre)
    except TemplateSyntaxError as error:
        pytest.fail(f"{nombre}, linea {error.lineno}: {error.message}")
