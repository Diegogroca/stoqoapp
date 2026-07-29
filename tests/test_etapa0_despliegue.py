"""
Pruebas de la Etapa 0.

No prueban inventario todavia. Prueban que la aplicacion arranca, responde y
reporta su estado, que es exactamente lo que esta etapa promete entregar.
Cada etapa siguiente agrega su propio archivo de pruebas.
"""

from fastapi.testclient import TestClient

from main import app

cliente = TestClient(app)


def test_health_responde_ok():
    """La verificacion tecnica responde 200 y en formato de datos."""
    respuesta = cliente.get("/health")
    assert respuesta.status_code == 200
    assert respuesta.json()["aplicacion"] == "Stoqo"


def test_health_reporta_version_de_python():
    """El entorno de ejecucion se reporta, no se asume."""
    datos = cliente.get("/health").json()
    assert datos["python"].startswith("3.")


def test_inicio_devuelve_html_en_espanol():
    """La pantalla de estado renderiza la plantilla, no un error de servidor."""
    respuesta = cliente.get("/")
    assert respuesta.status_code == 200
    assert "text/html" in respuesta.headers["content-type"]
    assert 'lang="es"' in respuesta.text
    assert "Stock anterior" in respuesta.text


def test_configuracion_pendiente_se_reporta_sin_exponer_valores():
    """
    Sin variables de entorno, /health las lista como pendientes.
    Nunca devuelve el valor de una llave, solo si existe.
    """
    datos = cliente.get("/health").json()
    assert "DATABASE_URL" in datos["configuracion_pendiente"]
    assert "password" not in respuesta_texto(datos)


def respuesta_texto(datos: dict) -> str:
    """Convierte la respuesta a texto plano para revisar que no haya secretos."""
    return str(datos)
