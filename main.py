"""
Stoqo - Plataforma multiempresa de control de inventario.

Punto de entrada de la aplicacion. Vercel busca una instancia de FastAPI
llamada `app` en este archivo y despliega todo como una sola funcion.

Etapa 1: al esqueleto de la Etapa 0 se suma el modelo de datos. La pantalla de
estado ahora reporta si la conexion con la base de datos responde, para poder
verificar la configuracion sin abrir Supabase.
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

# Rutas absolutas: en Vercel el directorio de trabajo no siempre es la raiz
# del proyecto, asi que nunca usamos rutas relativas para las plantillas.
BASE_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

ZONA = ZoneInfo("America/Mexico_City")

app = FastAPI(
    title="Stoqo",
    description="MVP multiempresa de control de inventario",
    version="0.1.0",
)


def estado_configuracion() -> dict:
    """
    Revisa que variables de entorno estan presentes sin exponer su valor.

    Se usa en la pantalla de estado y en /health para saber en que punto del
    checkpoint tecnologico esta el proyecto: primero despliegue, despues datos.
    """
    variables = {
        "DATABASE_URL": "Base de datos Supabase",
        "SUPABASE_URL": "Proyecto de Supabase",
        "SUPABASE_ANON_KEY": "Llave publica de Supabase",
        "SESSION_SECRET": "Firma de sesiones",
    }
    return {
        nombre: {"descripcion": desc, "configurada": bool(os.getenv(nombre))}
        for nombre, desc in variables.items()
    }


def estado_base_de_datos() -> dict:
    """
    Comprueba la conexion con Postgres sin tumbar la aplicacion si falla.

    Se importa db aqui dentro y no arriba: si la base de datos no esta
    configurada, la pantalla de estado debe seguir cargando para poder
    diagnosticar el problema.
    """
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
            "etapa": "1 - modelo de datos y aislamiento por empresa",
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
    """Pantalla de estado del proyecto: que ya funciona y que sigue."""
    etapas = [
        ("0", "Esqueleto y despliegue", "listo"),
        ("1", "Modelo de datos y aislamiento por empresa", "actual"),
        ("2", "Registro de cuenta y onboarding", "pendiente"),
        ("3", "Productos, atributos y variantes", "pendiente"),
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
            "base_de_datos": estado_base_de_datos(),
            "hora": datetime.now(ZONA).strftime("%d/%m/%Y %H:%M"),
            "configuracion": estado_configuracion(),
            "base_de_datos": estado_base_de_datos(),
            "etapas": etapas,
        },
    )
