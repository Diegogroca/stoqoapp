"""
Pruebas de la Etapa 1: modelo de datos y aislamiento por empresa.

Estas pruebas NO se conectan a Supabase. El CI de GitHub no tiene ni debe tener
las credenciales de produccion, y una prueba que depende de la red no es
repetible. En su lugar se levanta SQLite en memoria con el mismo esquema
declarado en modelos.py, se llena con datos controlados y se verifica el
comportamiento. Cada prueba corre sobre una base limpia.

Cubre el criterio CE-01 de la planeacion: una empresa no puede consultar datos
de otra.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alcance import AlcanceEmpresa, EmpresaNoAutorizada
from db import crear_tablas_para_pruebas
from modelos import Empresa, Movimiento, Producto, Propietario, Variante


@pytest.fixture()
def sesion() -> Session:
    """Base de datos limpia en memoria para cada prueba."""
    motor = crear_tablas_para_pruebas()
    with Session(motor) as sesion:
        yield sesion


def crear_empresa_con_inventario(sesion: Session, nombre: str, sku: str) -> Empresa:
    """Arma una empresa completa: propietario, producto y una variante."""
    empresa = Empresa(nombre=nombre)
    sesion.add(empresa)
    sesion.flush()

    sesion.add(
        Propietario(
            empresa_id=empresa.id,
            correo=f"dueno@{nombre.lower()}.com",
            hash_password="hash_de_prueba",
        )
    )

    producto = Producto(
        empresa_id=empresa.id,
        nombre=f"Producto de {nombre}",
        costo=Decimal("250.00"),
        minimo=5,
    )
    sesion.add(producto)
    sesion.flush()

    sesion.add(
        Variante(
            empresa_id=empresa.id,
            producto_id=producto.id,
            sku=sku,
            stock=10,
        )
    )
    sesion.commit()
    return empresa


# ---------------------------------------------------------------------------
# CE-01: aislamiento entre empresas
# ---------------------------------------------------------------------------


def test_una_empresa_solo_ve_sus_propios_productos(sesion: Session):
    kova = crear_empresa_con_inventario(sesion, "KOVA", "KOV-0001")
    otra = crear_empresa_con_inventario(sesion, "Panaderia", "PAN-0001")

    alcance_kova = AlcanceEmpresa(sesion, kova.id)
    alcance_otra = AlcanceEmpresa(sesion, otra.id)

    # Hay dos productos en la base, pero cada alcance ve exactamente uno.
    assert len(sesion.scalars(select(Producto)).all()) == 2
    assert len(alcance_kova.todos(Producto)) == 1
    assert len(alcance_otra.todos(Producto)) == 1
    assert alcance_kova.todos(Producto)[0].nombre == "Producto de KOVA"


def test_el_id_de_otra_empresa_se_comporta_como_inexistente(sesion: Session):
    kova = crear_empresa_con_inventario(sesion, "KOVA", "KOV-0001")
    otra = crear_empresa_con_inventario(sesion, "Panaderia", "PAN-0001")

    producto_ajeno = AlcanceEmpresa(sesion, otra.id).todos(Producto)[0]

    # Conocer el id no da acceso: para KOVA ese producto no existe.
    assert AlcanceEmpresa(sesion, kova.id).obtener(Producto, producto_ajeno.id) is None


def test_escribir_sobre_un_registro_ajeno_falla_de_forma_ruidosa(sesion: Session):
    kova = crear_empresa_con_inventario(sesion, "KOVA", "KOV-0001")
    otra = crear_empresa_con_inventario(sesion, "Panaderia", "PAN-0001")

    variante_ajena = AlcanceEmpresa(sesion, otra.id).todos(Variante)[0]

    with pytest.raises(EmpresaNoAutorizada):
        AlcanceEmpresa(sesion, kova.id).exigir(Variante, variante_ajena.id)


def test_el_alcance_exige_una_empresa(sesion: Session):
    with pytest.raises(ValueError):
        AlcanceEmpresa(sesion, None)


def test_no_se_puede_crear_una_variante_con_producto_de_otra_empresa(sesion: Session):
    """
    Aqui se prueba la primera capa de aislamiento: la llave foranea compuesta.

    Aunque el codigo de Python fallara y no filtrara nada, la base de datos
    rechaza la mezcla porque la variante apunta al par (empresa, producto).
    """
    kova = crear_empresa_con_inventario(sesion, "KOVA", "KOV-0001")
    otra = crear_empresa_con_inventario(sesion, "Panaderia", "PAN-0001")

    producto_ajeno = AlcanceEmpresa(sesion, otra.id).todos(Producto)[0]

    sesion.add(
        Variante(
            empresa_id=kova.id,  # empresa de KOVA
            producto_id=producto_ajeno.id,  # producto de la otra marca
            sku="KOV-9999",
        )
    )
    with pytest.raises(IntegrityError):
        sesion.commit()
    sesion.rollback()


# ---------------------------------------------------------------------------
# Reglas de negocio declaradas en el esquema
# ---------------------------------------------------------------------------


def test_el_sku_es_unico_dentro_de_la_empresa(sesion: Session):
    kova = crear_empresa_con_inventario(sesion, "KOVA", "KOV-0001")
    producto = AlcanceEmpresa(sesion, kova.id).todos(Producto)[0]

    sesion.add(
        Variante(empresa_id=kova.id, producto_id=producto.id, sku="KOV-0001")
    )
    with pytest.raises(IntegrityError):
        sesion.commit()
    sesion.rollback()


def test_dos_empresas_pueden_usar_el_mismo_sku(sesion: Session):
    """El SKU es unico por empresa, no globalmente: son inventarios separados."""
    crear_empresa_con_inventario(sesion, "KOVA", "SKU-COMPARTIDO")
    crear_empresa_con_inventario(sesion, "Panaderia", "SKU-COMPARTIDO")

    assert len(sesion.scalars(select(Variante)).all()) == 2


def test_una_empresa_solo_admite_un_propietario(sesion: Session):
    kova = crear_empresa_con_inventario(sesion, "KOVA", "KOV-0001")

    sesion.add(
        Propietario(
            empresa_id=kova.id,
            correo="segundo@kova.com",
            hash_password="hash_de_prueba",
        )
    )
    with pytest.raises(IntegrityError):
        sesion.commit()
    sesion.rollback()


def test_el_costo_no_puede_ser_negativo(sesion: Session):
    empresa = Empresa(nombre="KOVA")
    sesion.add(empresa)
    sesion.flush()

    sesion.add(
        Producto(empresa_id=empresa.id, nombre="Polo", costo=Decimal("-10.00"))
    )
    with pytest.raises(IntegrityError):
        sesion.commit()
    sesion.rollback()


# ---------------------------------------------------------------------------
# Integridad aritmetica del historial
# ---------------------------------------------------------------------------


def nuevo_movimiento(empresa_id, variante_id, **campos) -> Movimiento:
    base = dict(
        empresa_id=empresa_id,
        variante_id=variante_id,
        tipo="entrada",
        cantidad=10,
        delta=10,
        stock_anterior=0,
        stock_posterior=10,
    )
    base.update(campos)
    return Movimiento(**base)


def test_un_movimiento_valido_se_guarda(sesion: Session):
    kova = crear_empresa_con_inventario(sesion, "KOVA", "KOV-0001")
    variante = AlcanceEmpresa(sesion, kova.id).todos(Variante)[0]

    sesion.add(nuevo_movimiento(kova.id, variante.id))
    sesion.commit()

    assert len(AlcanceEmpresa(sesion, kova.id).todos(Movimiento)) == 1


def test_el_stock_posterior_debe_ser_el_anterior_mas_el_delta(sesion: Session):
    """Un historial que se contradice no puede llegar a la base de datos."""
    kova = crear_empresa_con_inventario(sesion, "KOVA", "KOV-0001")
    variante = AlcanceEmpresa(sesion, kova.id).todos(Variante)[0]

    sesion.add(
        nuevo_movimiento(
            kova.id, variante.id, stock_anterior=0, delta=10, stock_posterior=99
        )
    )
    with pytest.raises(IntegrityError):
        sesion.commit()
    sesion.rollback()


def test_una_salida_debe_llevar_delta_negativo(sesion: Session):
    kova = crear_empresa_con_inventario(sesion, "KOVA", "KOV-0001")
    variante = AlcanceEmpresa(sesion, kova.id).todos(Variante)[0]

    sesion.add(
        nuevo_movimiento(
            kova.id,
            variante.id,
            tipo="salida",
            cantidad=2,
            delta=2,  # incorrecto: una salida resta
            stock_anterior=10,
            stock_posterior=12,
        )
    )
    with pytest.raises(IntegrityError):
        sesion.commit()
    sesion.rollback()


def test_la_cantidad_debe_ser_positiva(sesion: Session):
    """El signo lo lleva el delta; la cantidad siempre es positiva."""
    kova = crear_empresa_con_inventario(sesion, "KOVA", "KOV-0001")
    variante = AlcanceEmpresa(sesion, kova.id).todos(Variante)[0]

    sesion.add(
        nuevo_movimiento(
            kova.id, variante.id, cantidad=0, delta=0, stock_posterior=0
        )
    )
    with pytest.raises(IntegrityError):
        sesion.commit()
    sesion.rollback()


def test_una_incidencia_siempre_trae_motivo(sesion: Session):
    kova = crear_empresa_con_inventario(sesion, "KOVA", "KOV-0001")
    variante = AlcanceEmpresa(sesion, kova.id).todos(Variante)[0]

    sesion.add(
        nuevo_movimiento(
            kova.id,
            variante.id,
            tipo="salida",
            cantidad=50,
            delta=-50,
            stock_anterior=10,
            stock_posterior=-40,
            es_incidencia=True,
            motivo=None,  # falta el motivo obligatorio
        )
    )
    with pytest.raises(IntegrityError):
        sesion.commit()
    sesion.rollback()


def test_un_movimiento_admite_como_maximo_una_compensacion(sesion: Session):
    """
    Regla de la planeacion: cada original puede tener una sola compensacion.
    Sin esto, cancelar dos veces duplicaria la correccion del stock.
    """
    kova = crear_empresa_con_inventario(sesion, "KOVA", "KOV-0001")
    variante = AlcanceEmpresa(sesion, kova.id).todos(Variante)[0]

    original = nuevo_movimiento(kova.id, variante.id)
    sesion.add(original)
    sesion.commit()

    primera = nuevo_movimiento(
        kova.id,
        variante.id,
        tipo="salida",
        cantidad=10,
        delta=-10,
        stock_anterior=10,
        stock_posterior=0,
        compensa_a=original.id,
    )
    sesion.add(primera)
    sesion.commit()

    segunda = nuevo_movimiento(
        kova.id,
        variante.id,
        tipo="salida",
        cantidad=10,
        delta=-10,
        stock_anterior=0,
        stock_posterior=-10,
        compensa_a=original.id,  # ya compensado
    )
    sesion.add(segunda)
    with pytest.raises(IntegrityError):
        sesion.commit()
    sesion.rollback()


def test_el_id_de_empresa_se_genera_en_python(sesion: Session):
    """Los ids no dependen de gen_random_uuid(), asi el modelo es portable."""
    empresa = Empresa(nombre="KOVA")
    sesion.add(empresa)
    sesion.flush()
    assert isinstance(empresa.id, uuid.UUID)


# ---------------------------------------------------------------------------
# Configuracion del motor de produccion
# ---------------------------------------------------------------------------


def test_el_motor_desactiva_los_prepared_statements():
    """
    Regresion de un fallo real en produccion.

    psycopg3 crea prepared statements con nombres correlativos y el pooler de
    Supabase reutiliza conexiones entre peticiones, lo que produce
    DuplicatePreparedStatement. Esta prueba fija la configuracion para que nadie
    la quite sin darse cuenta al refactorizar.

    Se comprueba la constante y no el motor porque crear el motor exige tener el
    driver de Postgres instalado, y las pruebas deben poder correr sin el.
    """
    from db import OPCIONES_CONEXION

    assert OPCIONES_CONEXION["prepare_threshold"] is None


def test_la_url_usa_el_driver_psycopg(monkeypatch):
    import db

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:6543/postgres")
    assert db.url_base_de_datos().startswith("postgresql+psycopg://")


def test_sin_variable_no_hay_url(monkeypatch):
    import db

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert db.url_base_de_datos() is None
