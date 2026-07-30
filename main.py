"""
Stoqo - Plataforma multiempresa de control de inventario.

Punto de entrada de la aplicacion. Vercel busca una instancia de FastAPI llamada
`app` en este archivo y despliega todo como una sola funcion.

Este modulo solo ensambla: registra los routers y expone la pantalla publica de
inicio y la verificacion tecnica. La logica de negocio vive en servicios/ y las
pantallas en rutas/.
"""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from dependencias import SesionRequerida, identidad
from rutas import cuentas, inventario
from vistas import templates

ZONA = ZoneInfo("America/Mexico_City")

app = FastAPI(
    title="Stoqo",
    description="MVP multiempresa de control de inventario",
    version="0.3.0",
)

app.include_router(cuentas.router)
app.include_router(inventario.router)


@app.exception_handler(Exception)
def error_no_previsto(request: Request, error: Exception):
    """
    Muestra el tipo y el mensaje del error en pantalla.

    Sin esto, cualquier fallo en produccion devuelve un "Internal Server Error"
    vacio y hay que ir a buscar los logs de la plataforma. Para un MVP academico
    es mas util que la aplicacion diga que fallo: se puede diagnosticar desde el
    navegador. Se muestra el tipo y el mensaje, nunca el traceback completo ni
    valores de variables de entorno.
    """
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={
            "tipo": type(error).__name__,
            "mensaje": str(error) or "Sin mensaje.",
            "ruta": request.url.path,
        },
        status_code=500,
    )


@app.exception_handler(SesionRequerida)
def sin_sesion(request: Request, _error: SesionRequerida):
    """Cualquier ruta protegida sin sesion manda a la pantalla de entrada."""
    return RedirectResponse("/entrar", status_code=303)


def estado_configuracion() -> dict:
    """Revisa que variables de entorno existen, sin exponer su valor."""
    variables = {
        "DATABASE_URL": "Base de datos",
        "SESSION_SECRET": "Firma de sesiones",
    }
    return {
        nombre: {"descripcion": desc, "configurada": bool(os.getenv(nombre))}
        for nombre, desc in variables.items()
    }


def estado_base_de_datos() -> dict:
    """Comprueba la conexion sin tumbar la aplicacion si falla."""
    try:
        from db import base_de_datos_responde

        responde, detalle = base_de_datos_responde()
    except Exception as error:  # noqa: BLE001
        responde, detalle = False, type(error).__name__
    return {"responde": responde, "detalle": detalle}


@app.get("/health")
def health() -> JSONResponse:
    """Verificacion tecnica del despliegue. Devuelve datos, no HTML."""
    configuracion = estado_configuracion()
    return JSONResponse(
        {
            "aplicacion": "Stoqo",
            "version": app.version,
            "etapa": "3 - productos, atributos y variantes",
            "python": sys.version.split()[0],
            "base_de_datos": estado_base_de_datos(),
            "hora_servidor": datetime.now(ZONA).isoformat(timespec="seconds"),
            "configuracion_pendiente": [
                nombre
                for nombre, datos in configuracion.items()
                if not datos["configurada"]
            ],
        }
    )


@app.get("/")
def inicio(request: Request):
    """Pantalla publica. Si ya hay sesion, va directo al inventario."""
    if identidad(request):
        return RedirectResponse("/inventario", status_code=303)

    etapas = [
        ("0", "Esqueleto y despliegue", "listo"),
        ("1", "Modelo de datos y aislamiento por empresa", "listo"),
        ("2", "Registro de cuenta y onboarding", "listo"),
        ("3", "Productos, atributos y variantes", "actual"),
        ("4", "Motor de movimientos", "pendiente"),
        ("5", "Dashboard y alertas", "pendiente"),
        ("6", "Reportes, filtros y exportaciones", "pendiente"),
        ("7", "Calidad de interfaz", "pendiente"),
        ("8", "Pruebas y demostracion", "pendiente"),
    ]
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "version": app.version,
            "python": sys.version.split()[0],
            "hora": datetime.now(ZONA).strftime("%d/%m/%Y %H:%M"),
            "configuracion": estado_configuracion(),
            "base_de_datos": estado_base_de_datos(),
            "etapas": etapas,
        },
    )
