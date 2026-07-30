"""
Pruebas del motor de movimientos y de la captura de existencias por variante.

El caso que motiva esta pantalla: KOVA tiene 5 polos medianos azules y 4 grandes
azules. Son cifras distintas y cada una necesita su propio movimiento. Aplicar la
misma cantidad a las 49 variantes no describe ningun inventario real.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import dependencias
import main
from db import crear_tablas_para_pruebas
from modelos import Incidencia, Movimiento, Variante
from servicios.cuentas import registrar
from servicios.movimientos import (
    CantidadInvalida,
    MotivoRequerido,
    StockNegativoRequiereConfirmacion,
    TipoInvalido,
    VarianteNoDisponible,
    cargar_existencias,
    registrar_movimiento,
)
from servicios.productos import crear_producto


@pytest.fixture(autouse=True)
def secreto_de_prueba(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "secreto_solo_para_pruebas_1234567890")


@pytest.fixture()
def sesion() -> Session:
    motor = crear_tablas_para_pruebas()
    with Session(motor) as sesion:
        yield sesion


@pytest.fixture()
def polo(sesion: Session):
    """Un polo de KOVA con 6 variantes: 3 tallas x 2 colores."""
    empresa, _ = registrar(sesion, "KOVA", "dueno@kova.com", "clave_seguraKOVA")
    producto = crear_producto(
        sesion,
        empresa.id,
        "Polo Premium",
        costo=250,
        minimo=10,
        atributos=[
            {"nombre": "Talla", "valores": ["S", "M", "L"]},
            {"nombre": "Color", "valores": ["azul", "negro"]},
        ],
    )
    return empresa, producto


# ---------------------------------------------------------------------------
# Cantidades distintas por variante
# ---------------------------------------------------------------------------


def test_cada_variante_recibe_su_propia_cantidad(sesion: Session, polo):
    """El caso real: 5 medianas azules, 4 grandes azules, cero del resto."""
    empresa, producto = polo
    variantes = sesion.scalars(
        select(Variante).where(Variante.producto_id == producto.id).order_by(Variante.sku)
    ).all()

    cantidades = {variantes[0].id: 5, variantes[1].id: 4}
    cargar_existencias(sesion, empresa.id, cantidades)

    assert variantes[0].stock == 5
    assert variantes[1].stock == 4
    assert all(v.stock == 0 for v in variantes[2:])


def test_cada_cantidad_deja_su_propio_movimiento(sesion: Session, polo):
    empresa, producto = polo
    variantes = sesion.scalars(
        select(Variante).where(Variante.producto_id == producto.id)
    ).all()

    cargar_existencias(sesion, empresa.id, {variantes[0].id: 5, variantes[1].id: 4})

    movimientos = sesion.scalars(select(Movimiento)).all()
    assert len(movimientos) == 2
    assert {m.cantidad for m in movimientos} == {5, 4}
    assert all(m.stock_anterior == 0 for m in movimientos)
    assert all(m.stock_posterior == m.cantidad for m in movimientos)


def test_las_cantidades_en_cero_no_generan_movimiento(sesion: Session, polo):
    """No existe un movimiento de cero unidades."""
    empresa, producto = polo
    variantes = sesion.scalars(
        select(Variante).where(Variante.producto_id == producto.id)
    ).all()

    cargar_existencias(sesion, empresa.id, {variantes[0].id: 3, variantes[1].id: 0})

    assert len(sesion.scalars(select(Movimiento)).all()) == 1


def test_cargar_dos_veces_acumula(sesion: Session, polo):
    """Una segunda carga suma sobre la existente; no reemplaza."""
    empresa, producto = polo
    variante = sesion.scalars(
        select(Variante).where(Variante.producto_id == producto.id)
    ).first()

    cargar_existencias(sesion, empresa.id, {variante.id: 5})
    cargar_existencias(sesion, empresa.id, {variante.id: 3})

    assert variante.stock == 8
    movimientos = sesion.scalars(
        select(Movimiento).order_by(Movimiento.stock_anterior)
    ).all()
    assert [(m.stock_anterior, m.stock_posterior) for m in movimientos] == [(0, 5), (5, 8)]


def test_una_cantidad_negativa_rechaza_toda_la_carga(sesion: Session, polo):
    empresa, producto = polo
    variantes = sesion.scalars(
        select(Variante).where(Variante.producto_id == producto.id)
    ).all()

    with pytest.raises(CantidadInvalida):
        cargar_existencias(sesion, empresa.id, {variantes[0].id: 5, variantes[1].id: -2})

    sesion.rollback()
    assert sesion.scalars(select(Movimiento)).all() == []


# ---------------------------------------------------------------------------
# Motor de movimientos
# ---------------------------------------------------------------------------


def test_una_entrada_suma_y_una_salida_resta(sesion: Session, polo):
    empresa, producto = polo
    variante = sesion.scalars(
        select(Variante).where(Variante.producto_id == producto.id)
    ).first()

    registrar_movimiento(sesion, empresa.id, variante.id, "entrada", 10)
    assert variante.stock == 10

    registrar_movimiento(sesion, empresa.id, variante.id, "salida", 2)
    assert variante.stock == 8


def test_el_signo_lo_decide_el_tipo_no_el_formulario(sesion: Session, polo):
    """CE-06 y CE-07: una entrada de 10 suma 10; una salida de 2 resta 2."""
    empresa, producto = polo
    variante = sesion.scalars(
        select(Variante).where(Variante.producto_id == producto.id)
    ).first()

    entrada = registrar_movimiento(sesion, empresa.id, variante.id, "entrada", 10)
    salida = registrar_movimiento(sesion, empresa.id, variante.id, "salida", 2)

    assert entrada.delta == 10
    assert salida.delta == -2
    assert salida.cantidad == 2  # la cantidad siempre es positiva


def test_los_ajustes_aplican_el_delta_correcto(sesion: Session, polo):
    """CE-08: un ajuste en cualquier sentido queda trazado."""
    empresa, producto = polo
    variante = sesion.scalars(
        select(Variante).where(Variante.producto_id == producto.id)
    ).first()

    registrar_movimiento(sesion, empresa.id, variante.id, "entrada", 10)
    registrar_movimiento(sesion, empresa.id, variante.id, "ajuste_positivo", 3)
    assert variante.stock == 13
    registrar_movimiento(sesion, empresa.id, variante.id, "ajuste_negativo", 5)
    assert variante.stock == 8


def test_un_tipo_desconocido_se_rechaza(sesion: Session, polo):
    empresa, producto = polo
    variante = sesion.scalars(
        select(Variante).where(Variante.producto_id == producto.id)
    ).first()
    with pytest.raises(TipoInvalido):
        registrar_movimiento(sesion, empresa.id, variante.id, "regalo", 1)


@pytest.mark.parametrize("cantidad", [0, -3, 2.5])
def test_la_cantidad_debe_ser_entero_positivo(sesion: Session, polo, cantidad):
    """Caso limite: cantidad cero, negativa o decimal se rechaza."""
    empresa, producto = polo
    variante = sesion.scalars(
        select(Variante).where(Variante.producto_id == producto.id)
    ).first()
    with pytest.raises(CantidadInvalida):
        registrar_movimiento(sesion, empresa.id, variante.id, "entrada", cantidad)


def test_no_se_puede_mover_una_variante_de_otra_empresa(sesion: Session, polo):
    """CE-01 en el motor: la empresa del movimiento debe ser la de la variante."""
    empresa, producto = polo
    otra, _ = registrar(sesion, "Panaderia", "dueno@pan.com", "clave_seguraPAN")
    variante = sesion.scalars(
        select(Variante).where(Variante.producto_id == producto.id)
    ).first()

    with pytest.raises(VarianteNoDisponible):
        registrar_movimiento(sesion, otra.id, variante.id, "entrada", 5)


# ---------------------------------------------------------------------------
# CE-09: stock negativo
# ---------------------------------------------------------------------------


def test_sin_confirmacion_el_stock_negativo_no_cambia_nada(sesion: Session, polo):
    empresa, producto = polo
    variante = sesion.scalars(
        select(Variante).where(Variante.producto_id == producto.id)
    ).first()
    registrar_movimiento(sesion, empresa.id, variante.id, "entrada", 3)

    with pytest.raises(StockNegativoRequiereConfirmacion) as aviso:
        registrar_movimiento(sesion, empresa.id, variante.id, "salida", 10)

    assert aviso.value.stock_anterior == 3
    assert aviso.value.stock_posterior == -7
    # El stock no se movio y no se guardo ningun movimiento nuevo.
    sesion.refresh(variante)
    assert variante.stock == 3
    assert len(sesion.scalars(select(Movimiento)).all()) == 1


def test_confirmado_sin_motivo_tampoco_procede(sesion: Session, polo):
    empresa, producto = polo
    variante = sesion.scalars(
        select(Variante).where(Variante.producto_id == producto.id)
    ).first()
    registrar_movimiento(sesion, empresa.id, variante.id, "entrada", 3)

    with pytest.raises(MotivoRequerido):
        registrar_movimiento(
            sesion, empresa.id, variante.id, "salida", 10, confirmar_negativo=True
        )


def test_confirmado_con_motivo_crea_incidencia(sesion: Session, polo):
    empresa, producto = polo
    variante = sesion.scalars(
        select(Variante).where(Variante.producto_id == producto.id)
    ).first()
    registrar_movimiento(sesion, empresa.id, variante.id, "entrada", 3)

    movimiento = registrar_movimiento(
        sesion,
        empresa.id,
        variante.id,
        "salida",
        10,
        confirmar_negativo=True,
        motivo="Venta registrada tarde",
    )

    assert movimiento.es_incidencia is True
    assert movimiento.stock_posterior == -7
    assert variante.stock == -7

    incidencia = sesion.scalars(select(Incidencia)).one()
    assert incidencia.movimiento_id == movimiento.id
    assert incidencia.motivo == "Venta registrada tarde"


# ---------------------------------------------------------------------------
# La pantalla de existencias
# ---------------------------------------------------------------------------


@pytest.fixture()
def cliente() -> TestClient:
    motor = crear_tablas_para_pruebas()

    def sesion_de_prueba():
        sesion = Session(motor)
        try:
            yield sesion
        finally:
            sesion.close()

    main.app.dependency_overrides[dependencias.obtener_sesion] = sesion_de_prueba
    with TestClient(main.app) as cliente:
        yield cliente
    main.app.dependency_overrides.clear()


def crear_polo_por_http(cliente: TestClient):
    cliente.post(
        "/registro",
        data={
            "empresa": "KOVA",
            "correo": "dueno@kova.com",
            "password": "clave_seguraKOVA",
        },
        follow_redirects=True,
    )
    return cliente.post(
        "/productos/nuevo",
        data={
            "nombre": "Polo Premium",
            "categoria": "Playeras",
            "unidad": "pieza",
            "costo": 250,
            "minimo": 10,
            "atributo_1": "Talla",
            "valores_1": "S, M, L",
            "atributo_2": "Color",
            "valores_2": "azul, negro",
        },
        follow_redirects=True,
    )


def campos_de_cantidad(html: str) -> list[str]:
    """Extrae los ids de variante de los campos del formulario."""
    import re

    return re.findall(r'name="cantidad_([0-9a-f-]+)"', html)


def test_la_pantalla_muestra_un_campo_por_variante(cliente: TestClient):
    respuesta = crear_polo_por_http(cliente)
    assert "Captura las existencias" in respuesta.text
    assert len(campos_de_cantidad(respuesta.text)) == 6


def test_se_guardan_cantidades_distintas_por_variante(cliente: TestClient):
    """Lo que faltaba: 5 de una variante y 4 de otra, no 5 en todas."""
    respuesta = crear_polo_por_http(cliente)
    ids = campos_de_cantidad(respuesta.text)

    inventario = cliente.post(
        f"/productos/{_id_producto(respuesta.text)}/existencias",
        data={
            "motivo": "Inventario inicial",
            f"cantidad_{ids[0]}": "5",
            f"cantidad_{ids[1]}": "4",
        },
        follow_redirects=True,
    )

    assert inventario.status_code == 200
    # 5 + 4 = 9 unidades en total, no 30.
    assert ">9<" in inventario.text


def test_una_cantidad_con_letras_se_explica(cliente: TestClient):
    respuesta = crear_polo_por_http(cliente)
    ids = campos_de_cantidad(respuesta.text)

    fallo = cliente.post(
        f"/productos/{_id_producto(respuesta.text)}/existencias",
        data={f"cantidad_{ids[0]}": "cinco"},
    )
    assert fallo.status_code == 400
    assert "numeros enteros" in fallo.text


def test_un_producto_de_otra_empresa_no_es_accesible(cliente: TestClient):
    respuesta = crear_polo_por_http(cliente)
    id_producto = _id_producto(respuesta.text)
    cliente.post("/salir")

    cliente.post(
        "/registro",
        data={
            "empresa": "Panaderia",
            "correo": "dueno@pan.com",
            "password": "clave_seguraPAN",
        },
        follow_redirects=True,
    )
    ajena = cliente.get(
        f"/productos/{id_producto}/existencias", follow_redirects=False
    )
    assert ajena.status_code == 303
    assert ajena.headers["location"] == "/inventario"


def _id_producto(html: str) -> str:
    """Saca el id del producto de la accion del formulario."""
    import re

    return re.search(r'action="/productos/([0-9a-f-]+)/existencias"', html).group(1)
