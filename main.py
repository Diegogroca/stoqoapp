"""
Stoqo - Plataforma multiempresa de control de inventario.

Punto de entrada de la aplicacion. Vercel busca una instancia de FastAPI
llamada `app` en este archivo y despliega todo como una sola funcion.

Etapa 0: esqueleto verificable. Todavia no hay base de datos ni inventario;
lo unico que esta ruta demuestra es que el despliegue funciona de punta a punta.
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


@app.get("/health")
def health() -> JSONResponse:
    """Verificacion tecnica del despliegue. Devuelve datos, no HTML."""
    configuracion = estado_configuracion()
    return JSONResponse(
        {
            "aplicacion": "Stoqo",
            "version": app.version,
            "etapa": "0 - esqueleto y despliegue",
            "python": sys.version.split()[0],
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
        ("0", "Esqueleto y despliegue", "actual"),
        ("1", "Modelo de datos y aislamiento por empresa", "pendiente"),
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
            "hora": datetime.now(ZONA).strftime("%d/%m/%Y %H:%M"),
            "configuracion": estado_configuracion(),
            "etapas": etapas,
        },
    )
