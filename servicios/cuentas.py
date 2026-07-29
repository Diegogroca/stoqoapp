"""
Servicio de cuentas (Etapa 2).

Regla del MVP: registrarse y crear empresa son un solo acto indivisible. No
existe una cuenta sin empresa ni una empresa sin propietario, asi que ambas
filas se crean dentro de la misma transaccion. Si la segunda falla, la primera
tampoco se guarda: nunca queda una cuenta huerfana que no pueda entrar a ningun
inventario.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from modelos import Empresa, Propietario
from seguridad import hash_password, verificar_password


class CorreoYaRegistrado(Exception):
    """El correo ya tiene una cuenta."""


class CredencialesInvalidas(Exception):
    """Correo o contraseña incorrectos."""


def normalizar_correo(correo: str) -> str:
    """Los correos no distinguen mayusculas para efectos de identidad."""
    return correo.strip().lower()


def registrar(
    sesion: Session, nombre_empresa: str, correo: str, password: str
) -> tuple[Empresa, Propietario]:
    """
    Crea la empresa y su propietario unico en una sola operacion.

    Devuelve el par (empresa, propietario) ya persistido.
    """
    nombre_empresa = nombre_empresa.strip()
    if not nombre_empresa:
        raise ValueError("El nombre de la empresa es obligatorio.")

    correo = normalizar_correo(correo)
    if "@" not in correo or "." not in correo.split("@")[-1]:
        raise ValueError("El correo no tiene un formato valido.")

    # Se comprueba antes por claridad del mensaje, pero el unique de la base es
    # quien realmente garantiza la regla ante dos registros simultaneos.
    if sesion.scalars(select(Propietario).where(Propietario.correo == correo)).first():
        raise CorreoYaRegistrado(correo)

    hash_guardado = hash_password(password)

    empresa = Empresa(nombre=nombre_empresa)
    sesion.add(empresa)
    sesion.flush()  # necesitamos el id de la empresa antes de crear al dueño

    propietario = Propietario(
        empresa_id=empresa.id, correo=correo, hash_password=hash_guardado
    )
    sesion.add(propietario)

    try:
        sesion.commit()
    except IntegrityError as error:
        sesion.rollback()
        raise CorreoYaRegistrado(correo) from error

    return empresa, propietario


def autenticar(sesion: Session, correo: str, password: str) -> Propietario:
    """
    Comprueba las credenciales y devuelve al propietario.

    Un correo inexistente y una contraseña incorrecta producen exactamente el
    mismo error: distinguirlos permitiria averiguar que correos tienen cuenta.
    """
    correo = normalizar_correo(correo)
    propietario = sesion.scalars(
        select(Propietario).where(Propietario.correo == correo)
    ).first()

    if propietario is None or not verificar_password(password, propietario.hash_password):
        raise CredencialesInvalidas()

    return propietario
