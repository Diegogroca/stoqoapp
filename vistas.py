"""
Configuracion unica de las plantillas Jinja2.

Vive en su propio modulo para que main.py y los routers puedan usar las mismas
plantillas sin importarse entre si (lo que crearia un ciclo de importaciones).
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

# Ruta absoluta: en Vercel el directorio de trabajo no siempre es la raiz.
BASE_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
