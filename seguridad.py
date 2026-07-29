"""
Seguridad de cuentas y sesiones (Etapa 2).

Decision: todo se hace con la biblioteca estandar de Python (hashlib, hmac,
secrets). No se agrega bcrypt ni passlib porque cada dependencia extra pesa en
el bundle de la funcion serverless, y pbkdf2_hmac con suficientes iteraciones es
adecuado para un MVP academico.

Dos mecanismos distintos que conviene no confundir:

- Contraseñas: hash de UN SOLO SENTIDO. Nunca se puede recuperar la original,
  solo comprobar si una candidata produce el mismo hash. Cada contraseña lleva
  su propia sal aleatoria para que dos usuarios con la misma clave tengan hashes
  distintos.
- Sesiones: firma REVERSIBLE pero no falsificable. La cookie dice quien eres y
  va firmada con SESSION_SECRET; sin el secreto nadie puede fabricar una cookie
  valida para otra empresa.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import uuid

ALGORITMO = "pbkdf2_sha256"
ITERACIONES = 200_000
NOMBRE_COOKIE = "stoqo_sesion"


# ---------------------------------------------------------------------------
# Contraseñas
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """
    Devuelve el hash almacenable de una contraseña.

    Formato: algoritmo$iteraciones$sal$hash. Guardar los parametros junto al
    hash permite subir las iteraciones en el futuro sin invalidar las cuentas
    existentes.
    """
    if not password or len(password) < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres.")
    sal = secrets.token_hex(16)
    derivado = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), sal.encode(), ITERACIONES
    ).hex()
    return f"{ALGORITMO}${ITERACIONES}${sal}${derivado}"


def verificar_password(password: str, almacenado: str) -> bool:
    """
    Comprueba una contraseña contra su hash almacenado.

    Usa compare_digest y no ==: la comparacion normal termina en el primer
    caracter distinto, y ese tiempo de respuesta puede filtrar informacion.
    """
    try:
        algoritmo, iteraciones, sal, esperado = almacenado.split("$")
    except ValueError:
        return False
    if algoritmo != ALGORITMO:
        return False
    calculado = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), sal.encode(), int(iteraciones)
    ).hex()
    return hmac.compare_digest(calculado, esperado)


# ---------------------------------------------------------------------------
# Sesiones firmadas
# ---------------------------------------------------------------------------


def secreto_de_sesion() -> bytes:
    """Lee SESSION_SECRET del entorno. Sin secreto no hay sesiones."""
    secreto = os.getenv("SESSION_SECRET")
    if not secreto:
        raise RuntimeError(
            "SESSION_SECRET no esta configurada. "
            "Definela en Vercel > Settings > Environment Variables."
        )
    return secreto.encode()


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).decode().rstrip("=")


def _desde_b64(texto: str) -> bytes:
    relleno = "=" * (-len(texto) % 4)
    return base64.urlsafe_b64decode(texto + relleno)


def crear_token(empresa_id: uuid.UUID, propietario_id: uuid.UUID) -> str:
    """Empaqueta la identidad de la sesion y la firma."""
    cuerpo = _b64(
        json.dumps(
            {"empresa": str(empresa_id), "propietario": str(propietario_id)}
        ).encode()
    )
    firma = _b64(hmac.new(secreto_de_sesion(), cuerpo.encode(), hashlib.sha256).digest())
    return f"{cuerpo}.{firma}"


def leer_token(token: str | None) -> dict | None:
    """
    Valida la firma y devuelve la identidad, o None si el token no sirve.

    Cualquier alteracion del cuerpo invalida la firma: no se puede editar la
    cookie para entrar a la empresa de otra marca.
    """
    if not token or "." not in token:
        return None
    cuerpo, firma = token.rsplit(".", 1)
    esperada = _b64(
        hmac.new(secreto_de_sesion(), cuerpo.encode(), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(firma, esperada):
        return None
    try:
        datos = json.loads(_desde_b64(cuerpo))
        return {
            "empresa": uuid.UUID(datos["empresa"]),
            "propietario": uuid.UUID(datos["propietario"]),
        }
    except (ValueError, KeyError):
        return None
