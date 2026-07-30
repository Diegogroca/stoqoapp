"""
Pruebas de las Etapas 2 y 3: cuentas, sesiones, productos y variantes.

Cubre los criterios CE-01 (aislamiento), CE-03 (producto simple), CE-04
(variantes automaticas: 7 x 7 = 49) y CE-05 (variantes manuales) de la
planeacion, mas los casos limite del pseudocodigo A.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from alcance import AlcanceEmpresa
from db import crear_tablas_para_pruebas
from modelos import Movimiento, Producto, Propietario, Variante
from seguridad import crear_token, hash_password, leer_token, verificar_password
from servicios.cuentas import (
    CorreoYaRegistrado,
    CredencialesInvalidas,
    autenticar,
    registrar,
)
from servicios.productos import (
    DemasiadasVariantes,
    combinaciones,
    contar_combinaciones,
    crear_producto,
    generar_sku,
    prefijo_desde_nombre,
)


@pytest.fixture()
def sesion() -> Session:
    motor = crear_tablas_para_pruebas()
    with Session(motor) as sesion:
        yield sesion


@pytest.fixture(autouse=True)
def secreto_de_prueba(monkeypatch):
    """Las sesiones necesitan SESSION_SECRET; en pruebas se usa uno fijo."""
    monkeypatch.setenv("SESSION_SECRET", "secreto_solo_para_pruebas_1234567890")


# ---------------------------------------------------------------------------
# Contraseñas
# ---------------------------------------------------------------------------


def test_el_hash_no_contiene_la_contrasena():
    guardado = hash_password("contrasena_segura")
    assert "contrasena_segura" not in guardado
    assert guardado.startswith("pbkdf2_sha256$")


def test_la_contrasena_correcta_se_verifica():
    guardado = hash_password("contrasena_segura")
    assert verificar_password("contrasena_segura", guardado)
    assert not verificar_password("otra_contrasena", guardado)


def test_dos_cuentas_con_la_misma_contrasena_tienen_hashes_distintos():
    """La sal aleatoria evita que un hash filtrado delate a otras cuentas."""
    assert hash_password("misma_clave_123") != hash_password("misma_clave_123")


def test_una_contrasena_corta_se_rechaza():
    with pytest.raises(ValueError):
        hash_password("corta")


# ---------------------------------------------------------------------------
# Sesiones firmadas
# ---------------------------------------------------------------------------


def test_el_token_conserva_la_identidad():
    empresa, propietario = uuid.uuid4(), uuid.uuid4()
    datos = leer_token(crear_token(empresa, propietario))
    assert datos == {"empresa": empresa, "propietario": propietario}


def test_un_token_alterado_se_rechaza():
    """Editar la cookie para entrar a otra empresa invalida la firma."""
    token = crear_token(uuid.uuid4(), uuid.uuid4())
    cuerpo, firma = token.rsplit(".", 1)
    otro = crear_token(uuid.uuid4(), uuid.uuid4()).rsplit(".", 1)[0]
    assert leer_token(f"{otro}.{firma}") is None


def test_un_token_ausente_o_basura_devuelve_none():
    assert leer_token(None) is None
    assert leer_token("cualquier_cosa") is None


# ---------------------------------------------------------------------------
# CE-01 y CE-02: registro y empresa automatica
# ---------------------------------------------------------------------------


def test_el_registro_crea_empresa_y_propietario(sesion: Session):
    empresa, propietario = registrar(sesion, "KOVA", "dueno@kova.com", "clave_seguraKOVA")
    assert empresa.nombre == "KOVA"
    assert propietario.empresa_id == empresa.id
    assert propietario.correo == "dueno@kova.com"


def test_el_correo_se_normaliza_a_minusculas(sesion: Session):
    _, propietario = registrar(sesion, "KOVA", "  Dueno@KOVA.com ", "clave_seguraKOVA")
    assert propietario.correo == "dueno@kova.com"


def test_no_se_puede_registrar_el_mismo_correo_dos_veces(sesion: Session):
    registrar(sesion, "KOVA", "dueno@kova.com", "clave_seguraKOVA")
    with pytest.raises(CorreoYaRegistrado):
        registrar(sesion, "Otra marca", "dueno@kova.com", "otra_clave_1234")


def test_un_correo_invalido_se_rechaza(sesion: Session):
    with pytest.raises(ValueError):
        registrar(sesion, "KOVA", "no-es-correo", "clave_seguraKOVA")


def test_la_empresa_necesita_nombre(sesion: Session):
    with pytest.raises(ValueError):
        registrar(sesion, "   ", "dueno@kova.com", "clave_seguraKOVA")


def test_si_falla_el_propietario_no_queda_empresa_huerfana(sesion: Session):
    """
    Registrarse y crear empresa son un solo acto.

    Al fallar el segundo registro por correo duplicado, no debe quedar una
    empresa sin dueño en la base.
    """
    registrar(sesion, "KOVA", "dueno@kova.com", "clave_seguraKOVA")
    with pytest.raises(CorreoYaRegistrado):
        registrar(sesion, "Fantasma", "dueno@kova.com", "clave_seguraKOVA")

    nombres = sesion.scalars(select(Propietario.correo)).all()
    assert len(nombres) == 1


def test_autenticar_devuelve_al_propietario(sesion: Session):
    registrar(sesion, "KOVA", "dueno@kova.com", "clave_seguraKOVA")
    propietario = autenticar(sesion, "DUENO@kova.com", "clave_seguraKOVA")
    assert propietario.correo == "dueno@kova.com"


def test_credenciales_incorrectas_fallan_igual_que_correo_inexistente(sesion: Session):
    """El mismo error en ambos casos: no se revela que correos tienen cuenta."""
    registrar(sesion, "KOVA", "dueno@kova.com", "clave_seguraKOVA")
    with pytest.raises(CredencialesInvalidas):
        autenticar(sesion, "dueno@kova.com", "clave_equivocada")
    with pytest.raises(CredencialesInvalidas):
        autenticar(sesion, "nadie@kova.com", "clave_seguraKOVA")


# ---------------------------------------------------------------------------
# SKU
# ---------------------------------------------------------------------------


def test_el_prefijo_ignora_acentos_y_espacios():
    assert prefijo_desde_nombre("Polo Premium") == "POL"
    assert prefijo_desde_nombre("Camisón") == "CAM"
    assert prefijo_desde_nombre("A1") == "AXX"


def test_el_sku_no_se_repite_dentro_de_la_empresa(sesion: Session):
    empresa, _ = registrar(sesion, "KOVA", "dueno@kova.com", "clave_seguraKOVA")
    crear_producto(sesion, empresa.id, "Polo", atributos=[
        {"nombre": "Talla", "valores": ["S", "M", "L"]}
    ])
    skus = sesion.scalars(select(Variante.sku)).all()
    assert len(skus) == len(set(skus)) == 3


def test_el_sku_salta_un_consecutivo_ocupado(sesion: Session):
    """Caso limite: 'SKU generado ya existe' se resuelve con otro consecutivo."""
    empresa, _ = registrar(sesion, "KOVA", "dueno@kova.com", "clave_seguraKOVA")
    crear_producto(sesion, empresa.id, "Polo")
    ocupado = sesion.scalars(select(Variante.sku)).first()
    nuevo = generar_sku(sesion, empresa.id, "Polo")
    assert nuevo != ocupado


# ---------------------------------------------------------------------------
# Producto cartesiano
# ---------------------------------------------------------------------------


def test_el_cartesiano_combina_dos_atributos():
    resultado = combinaciones([
        {"nombre": "Talla", "valores": ["S", "M"]},
        {"nombre": "Color", "valores": ["negro", "blanco"]},
    ])
    assert resultado == [
        ("S", "negro"), ("S", "blanco"), ("M", "negro"), ("M", "blanco")
    ]


def test_ce04_siete_por_siete_da_cuarenta_y_nueve():
    """CE-04 de la planeacion: dos atributos de 7 valores dan 49 combinaciones."""
    atributos = [
        {"nombre": "Talla", "valores": list("ABCDEFG")},
        {"nombre": "Color", "valores": list("HIJKLMN")},
    ]
    assert contar_combinaciones(atributos) == 49
    assert len(combinaciones(atributos)) == 49
    assert len(set(combinaciones(atributos))) == 49


def test_un_atributo_sin_valores_impide_generar():
    with pytest.raises(ValueError):
        combinaciones([{"nombre": "Talla", "valores": []}])


def test_valores_repetidos_en_un_atributo_se_rechazan():
    with pytest.raises(ValueError):
        combinaciones([{"nombre": "Talla", "valores": ["M", "M"]}])


def test_contar_no_crea_nada(sesion: Session):
    """El conteo previo es aritmetica pura: no toca la base de datos."""
    antes = len(sesion.scalars(select(Variante)).all())
    contar_combinaciones([{"nombre": "Talla", "valores": ["S", "M", "L"]}])
    assert len(sesion.scalars(select(Variante)).all()) == antes


# ---------------------------------------------------------------------------
# CE-03 a CE-05: creacion de productos
# ---------------------------------------------------------------------------


def test_ce03_un_producto_simple_tiene_una_variante_base(sesion: Session):
    empresa, _ = registrar(sesion, "KOVA", "dueno@kova.com", "clave_seguraKOVA")
    producto = crear_producto(sesion, empresa.id, "Gorra", costo=120, minimo=3)

    variantes = sesion.scalars(
        select(Variante).where(Variante.producto_id == producto.id)
    ).all()
    assert len(variantes) == 1
    assert producto.es_variable is False
    assert variantes[0].stock == 0


def test_ce04_el_caso_kova_genera_cuarenta_y_nueve_variantes(sesion: Session):
    empresa, _ = registrar(sesion, "KOVA", "dueno@kova.com", "clave_seguraKOVA")
    producto = crear_producto(
        sesion,
        empresa.id,
        "Polo Premium",
        categoria="Playeras",
        costo=Decimal("250.00"),
        minimo=10,
        atributos=[
            {"nombre": "Talla", "valores": ["XXS", "XS", "S", "M", "L", "XL", "XXL"]},
            {"nombre": "Color", "valores": [
                "negro", "blanco", "azul", "verde", "rojo", "gris", "beige"
            ]},
        ],
    )
    variantes = sesion.scalars(
        select(Variante).where(Variante.producto_id == producto.id)
    ).all()
    assert len(variantes) == 49
    assert len({v.sku for v in variantes}) == 49
    assert producto.es_variable is True


def test_ce05_se_puede_capturar_una_sola_combinacion(sesion: Session):
    """Variantes manuales: un valor por atributo crea exactamente una variante."""
    empresa, _ = registrar(sesion, "KOVA", "dueno@kova.com", "clave_seguraKOVA")
    producto = crear_producto(
        sesion,
        empresa.id,
        "Polo edicion",
        atributos=[
            {"nombre": "Talla", "valores": ["M"]},
            {"nombre": "Color", "valores": ["negro"]},
        ],
    )
    variantes = sesion.scalars(
        select(Variante).where(Variante.producto_id == producto.id)
    ).all()
    assert len(variantes) == 1


def test_la_existencia_inicial_entra_como_movimiento(sesion: Session):
    """El inventario inicial se traza; no es una edicion silenciosa del stock."""
    empresa, _ = registrar(sesion, "KOVA", "dueno@kova.com", "clave_seguraKOVA")
    crear_producto(sesion, empresa.id, "Gorra", existencia_inicial=10)

    variante = sesion.scalars(select(Variante)).one()
    movimiento = sesion.scalars(select(Movimiento)).one()

    assert variante.stock == 10
    assert movimiento.tipo == "entrada"
    assert movimiento.stock_anterior == 0
    assert movimiento.stock_posterior == 10
    assert movimiento.delta == 10


def test_demasiadas_variantes_se_rechaza_antes_de_crear(sesion: Session):
    empresa, _ = registrar(sesion, "KOVA", "dueno@kova.com", "clave_seguraKOVA")
    atributos = [
        {"nombre": "A", "valores": [str(n) for n in range(30)]},
        {"nombre": "B", "valores": [str(n) for n in range(30)]},
    ]
    with pytest.raises(DemasiadasVariantes) as fallo:
        crear_producto(sesion, empresa.id, "Explosivo", atributos=atributos)

    assert fallo.value.total == 900
    # Nada se creo: la validacion ocurre antes de escribir.
    assert sesion.scalars(select(Producto)).all() == []


def test_el_costo_negativo_se_rechaza(sesion: Session):
    empresa, _ = registrar(sesion, "KOVA", "dueno@kova.com", "clave_seguraKOVA")
    with pytest.raises(ValueError):
        crear_producto(sesion, empresa.id, "Polo", costo=-5)


def test_la_categoria_se_reutiliza_dentro_de_la_empresa(sesion: Session):
    empresa, _ = registrar(sesion, "KOVA", "dueno@kova.com", "clave_seguraKOVA")
    primero = crear_producto(sesion, empresa.id, "Polo", categoria="Playeras")
    segundo = crear_producto(sesion, empresa.id, "Camiseta", categoria="Playeras")
    assert primero.categoria_id == segundo.categoria_id


def test_dos_empresas_no_ven_los_productos_de_la_otra(sesion: Session):
    """CE-01 aplicado al flujo real: dos registros independientes y aislados."""
    kova, _ = registrar(sesion, "KOVA", "dueno@kova.com", "clave_seguraKOVA")
    otra, _ = registrar(sesion, "Panaderia", "dueno@pan.com", "clave_seguraPAN")

    crear_producto(sesion, kova.id, "Polo Premium")
    crear_producto(sesion, otra.id, "Concha de chocolate")

    productos_kova = AlcanceEmpresa(sesion, kova.id).todos(Producto)
    assert len(productos_kova) == 1
    assert productos_kova[0].nombre == "Polo Premium"
