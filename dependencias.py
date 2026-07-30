"""
Dependencias compartidas de las rutas (Etapa 2).

FastAPI resuelve estas funciones antes de ejecutar cada ruta. Concentrarlas aqui
tiene un efecto concreto: ninguna ruta abre la base de datos por su cuenta ni
decide sola quien esta autenticado. El aislamiento por empresa entra al sistema
por un unico punto.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from alcance import AlcanceEmpresa
from seguridad import NOMBRE_COOKIE, leer_token


class SesionRequerida(Exception):
    """No hay sesion valida; la ruta debe redirigir a la pantalla de entrada."""


def obtener_sesion() -> Iterator[Session]:
    """Abre una sesion de base de datos y la cierra al terminar la peticion."""
    from db import sesion as abrir_sesion

    sesion = abrir_sesion()
    try:
        yield sesion
    finally:
        sesion.close()


def identidad(request: Request) -> dict | None:
    """Lee la cookie firmada. Devuelve None si no hay sesion valida."""
    try:
        return leer_token(request.cookies.get(NOMBRE_COOKIE))
    except RuntimeError:
        # SESSION_SECRET no configurada: se trata como sesion ausente.
        return None


def alcance_actual(
    request: Request, sesion: Session = Depends(obtener_sesion)
) -> AlcanceEmpresa:
    """
    Devuelve el alcance acotado a la empresa autenticada.

    Toda ruta que lea o escriba inventario depende de esto, de modo que es
    imposible escribir una consulta sin empresa: no hay forma de llegar a los
    datos sin pasar por aqui.
    """
    datos = identidad(request)
    if datos is None:
        raise SesionRequerida()
    return AlcanceEmpresa(sesion, datos["empresa"])


def redirigir_a_entrada() -> RedirectResponse:
    """Respuesta estandar cuando falta la sesion."""
    return RedirectResponse("/entrar", status_code=303)
