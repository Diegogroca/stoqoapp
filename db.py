"""
Conexion a la base de datos.

Decision tecnica importante: en Vercel cada peticion puede ejecutarse en una
instancia distinta y de vida corta. Mantener un pool de conexiones abiertas no
sirve de nada y agota el limite de Postgres. Por eso se usa NullPool: se abre
una conexion, se usa y se cierra. El pooler de Supabase (puerto 6543, modo
transaccion) es quien realmente reutiliza las conexiones del lado del servidor.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from modelos import Base

_motor = None
_FabricaSesion = None

# Incompatibilidad real entre psycopg3 y el pooler de Supabase en modo
# transaccion: psycopg3 crea prepared statements del lado del servidor con
# nombres correlativos (_pg3_0, _pg3_1...), pero el pooler reutiliza la misma
# conexion de servidor para peticiones distintas. La segunda peticion intenta
# declarar un nombre que ya existe y Postgres responde
# DuplicatePreparedStatement.
#
# prepare_threshold=None desactiva los prepared statements. Se pierde una
# optimizacion menor (Postgres reanaliza cada consulta) y a cambio la aplicacion
# funciona detras del pooler, que es obligatorio en serverless.
OPCIONES_CONEXION = {"prepare_threshold": None}


def url_base_de_datos() -> str | None:
    """Devuelve la cadena de conexion, normalizada para SQLAlchemy."""
    url = os.getenv("DATABASE_URL")
    if not url:
        return None
    # Supabase entrega la URL con el prefijo postgresql://; SQLAlchemy necesita
    # saber que driver usar, asi que lo hacemos explicito.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def motor():
    """
    Crea el motor una sola vez por instancia (lazy).

    Se crea al primer uso y no al importar el modulo: asi la aplicacion puede
    arrancar y mostrar su pantalla de estado incluso si la base de datos
    todavia no esta configurada.
    """
    global _motor, _FabricaSesion
    if _motor is None:
        url = url_base_de_datos()
        if url is None:
            raise RuntimeError(
                "DATABASE_URL no esta configurada. "
                "Definela en Vercel > Settings > Environment Variables."
            )
        _motor = create_engine(
            url,
            poolclass=NullPool,
            future=True,
            connect_args=OPCIONES_CONEXION,
        )
        _FabricaSesion = sessionmaker(bind=_motor, expire_on_commit=False)
    return _motor


def sesion() -> Session:
    """Abre una sesion nueva. Quien la abre es responsable de cerrarla."""
    motor()
    return _FabricaSesion()


def base_de_datos_responde() -> tuple[bool, str]:
    """
    Comprueba la conexion sin tumbar la aplicacion si falla.

    Devuelve (responde, detalle) para mostrarlo en la pantalla de estado.
    """
    from sqlalchemy import text

    if url_base_de_datos() is None:
        return False, "DATABASE_URL no configurada"
    try:
        with motor().connect() as conexion:
            conexion.execute(text("select 1"))
        return True, "conexion correcta"
    except Exception as error:  # noqa: BLE001 - el detalle se muestra al usuario
        return False, type(error).__name__


def crear_tablas_para_pruebas(url: str = "sqlite+pysqlite:///:memory:"):
    """
    Crea el esquema en una base de datos temporal para las pruebas.

    Las pruebas no se conectan a Supabase: el CI de GitHub no tiene (ni debe
    tener) las credenciales de produccion. Los modelos se declaran una sola vez
    y sirven para ambos motores, asi que las reglas de estructura y aislamiento
    se pueden verificar sin red.
    """
    from sqlalchemy import event
    from sqlalchemy.pool import StaticPool

    # StaticPool es imprescindible con SQLite en memoria: sin el, cada conexion
    # nueva abre una base de datos VACIA y distinta, asi que las pruebas que
    # abren varias sesiones (por ejemplo las que llaman a la app por HTTP) no
    # encontrarian las tablas. StaticPool reutiliza una sola conexion, de modo
    # que todas las sesiones ven la misma base.
    motor_pruebas = create_engine(
        url,
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    # SQLite ignora las llaves foraneas salvo que se activen por conexion.
    # El listener se registra ANTES de crear las tablas: si se registrara
    # despues, la conexion que ya esta en el pool nunca recibiria el PRAGMA y
    # las pruebas de integridad pasarian sin probar nada.
    @event.listens_for(motor_pruebas, "connect")
    def _activar_llaves_foraneas(conexion, _registro):
        cursor = conexion.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(motor_pruebas)
    return motor_pruebas
