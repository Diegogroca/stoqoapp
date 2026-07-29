"""
Rutas de cuentas: registro, entrada y salida (Etapa 2).

Cada formulario se maneja con dos rutas: un GET que muestra la pantalla y un
POST que procesa el envio. Cuando el POST falla, se vuelve a renderizar la misma
pantalla con el error junto al campo y los datos que el usuario ya habia
escrito, en lugar de mandarlo a una pagina de error en blanco.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from dependencias import identidad, obtener_sesion
from seguridad import NOMBRE_COOKIE, crear_token
from servicios.cuentas import (
    CorreoYaRegistrado,
    CredencialesInvalidas,
    autenticar,
    registrar,
)
from vistas import templates

router = APIRouter()

# Duracion de la sesion: una semana.
DURACION_COOKIE = 60 * 60 * 24 * 7


def _guardar_sesion(
    request: Request, respuesta: RedirectResponse, empresa_id, propietario_id
):
    """
    Escribe la cookie de sesion con las banderas de seguridad habituales.

    httponly: JavaScript no puede leerla, lo que limita el robo por XSS.
    samesite lax: no se envia en peticiones cruzadas de otros sitios.
    secure: solo viaja por HTTPS. Se activa segun el esquema de la peticion y no
    de forma fija, porque una cookie secure NO se envia por http: con secure=True
    siempre, la sesion funcionaria en Vercel pero seria imposible probar la app
    en local o desde pytest.
    """
    respuesta.set_cookie(
        NOMBRE_COOKIE,
        crear_token(empresa_id, propietario_id),
        max_age=DURACION_COOKIE,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
    )
    return respuesta


@router.get("/registro", response_class=HTMLResponse)
def mostrar_registro(request: Request):
    if identidad(request):
        return RedirectResponse("/inventario", status_code=303)
    return templates.TemplateResponse(
        request=request, name="registro.html", context={"datos": {}}
    )


@router.post("/registro")
def procesar_registro(
    request: Request,
    empresa: str = Form(...),
    correo: str = Form(...),
    password: str = Form(...),
    sesion: Session = Depends(obtener_sesion),
):
    datos = {"empresa": empresa, "correo": correo}
    try:
        empresa_creada, propietario = registrar(sesion, empresa, correo, password)
    except CorreoYaRegistrado:
        return templates.TemplateResponse(
            request=request,
            name="registro.html",
            context={
                "datos": datos,
                "error": "Ese correo ya tiene una cuenta. Entra con tu contraseña.",
            },
            status_code=400,
        )
    except ValueError as problema:
        return templates.TemplateResponse(
            request=request,
            name="registro.html",
            context={"datos": datos, "error": str(problema)},
            status_code=400,
        )

    respuesta = RedirectResponse("/inventario", status_code=303)
    return _guardar_sesion(request, respuesta, empresa_creada.id, propietario.id)


@router.get("/entrar", response_class=HTMLResponse)
def mostrar_entrada(request: Request):
    if identidad(request):
        return RedirectResponse("/inventario", status_code=303)
    return templates.TemplateResponse(
        request=request, name="entrar.html", context={"datos": {}}
    )


@router.post("/entrar")
def procesar_entrada(
    request: Request,
    correo: str = Form(...),
    password: str = Form(...),
    sesion: Session = Depends(obtener_sesion),
):
    try:
        propietario = autenticar(sesion, correo, password)
    except CredencialesInvalidas:
        return templates.TemplateResponse(
            request=request,
            name="entrar.html",
            context={
                "datos": {"correo": correo},
                "error": "Correo o contraseña incorrectos.",
            },
            status_code=401,
        )

    respuesta = RedirectResponse("/inventario", status_code=303)
    return _guardar_sesion(request, respuesta, propietario.empresa_id, propietario.id)


@router.post("/salir")
def salir():
    """Cerrar sesion es borrar la cookie: el servidor no guarda estado."""
    respuesta = RedirectResponse("/entrar", status_code=303)
    respuesta.delete_cookie(NOMBRE_COOKIE)
    return respuesta
