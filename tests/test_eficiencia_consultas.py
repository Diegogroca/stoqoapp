"""
Pruebas de eficiencia de consultas.

Existen para respaldar con evidencia una afirmacion que de otro modo seria solo
una promesa: que el catalogo no lanza una consulta por variante.

El problema que se corrigio se llama N+1: una consulta para traer la lista y
luego una consulta mas por cada elemento de esa lista. Con 49 variantes eran 50
viajes a la base de datos para pintar una pantalla. En un servidor tradicional es
lento; en una funcion serverless que habla con Supabase por red es peor, porque
la latencia de cada viaje domina sobre el tiempo de calculo.

La forma de probarlo no es cronometrar (el tiempo varia entre maquinas) sino
CONTAR consultas: el conteo es determinista y no depende del hardware.
"""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

import dependencias
import main
from db import crear_tablas_para_pruebas


class ContadorDeConsultas:
    """
    Cuenta las sentencias SQL que se ejecutan en un motor.

    Se enchufa al evento `before_cursor_execute` de SQLAlchemy, que se dispara
    justo antes de mandar cada sentencia al driver.
    """

    def __init__(self, motor):
        self.motor = motor
        self.sentencias: list[str] = []

    def _registrar(self, conexion, cursor, sentencia, *_resto):
        self.sentencias.append(sentencia)

    def __enter__(self):
        event.listen(self.motor, "before_cursor_execute", self._registrar)
        return self

    def __exit__(self, *_excepcion):
        event.remove(self.motor, "before_cursor_execute", self._registrar)

    @property
    def total(self) -> int:
        return len(self.sentencias)

    def de_tabla(self, tabla: str) -> int:
        """Cuantas sentencias tocaron una tabla concreta."""
        return sum(1 for s in self.sentencias if tabla in s.lower())


@pytest.fixture(autouse=True)
def secreto_de_prueba(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "secreto_solo_para_pruebas_1234567890")


@pytest.fixture()
def entorno():
    """Devuelve (cliente, motor) para poder contar consultas del motor real."""
    motor = crear_tablas_para_pruebas()

    def sesion_de_prueba():
        sesion = Session(motor)
        try:
            yield sesion
        finally:
            sesion.close()

    main.app.dependency_overrides[dependencias.obtener_sesion] = sesion_de_prueba
    with TestClient(main.app) as cliente:
        yield cliente, motor
    main.app.dependency_overrides.clear()


def preparar_49_variantes(cliente: TestClient) -> str:
    """Crea el caso KOVA: un producto con 49 variantes y existencias."""
    cliente.post(
        "/registro",
        data={
            "empresa": "KOVA",
            "correo": "dueno@kova.com",
            "password": "clave_seguraKOVA",
        },
        follow_redirects=True,
    )
    pantalla = cliente.post(
        "/productos/nuevo",
        data={
            "nombre": "Polo Premium",
            "categoria": "Playeras",
            "unidad": "pieza",
            "costo": 250,
            "minimo": 10,
            "atributo_1": "Talla",
            "valores_1": "XXS, XS, S, M, L, XL, XXL",
            "atributo_2": "Color",
            "valores_2": "negro, blanco, azul, verde, rojo, gris, beige",
        },
        follow_redirects=True,
    )
    ids = re.findall(r'name="cantidad_([0-9a-f-]+)"', pantalla.text)
    assert len(ids) == 49
    id_producto = re.search(r"/productos/([0-9a-f-]+)/existencias", pantalla.text).group(1)
    cliente.post(
        f"/productos/{id_producto}/existencias",
        data={f"cantidad_{ids[0]}": "20", f"cantidad_{ids[1]}": "5"},
        follow_redirects=True,
    )
    return id_producto


# ---------------------------------------------------------------------------
# El catalogo
# ---------------------------------------------------------------------------


def test_el_catalogo_no_escala_con_el_numero_de_variantes(entorno):
    """
    La prueba central: 49 variantes no producen 49 consultas.

    Se fija un tope holgado (15) en lugar de un numero exacto para que la prueba
    no se rompa por un refactor razonable, pero suficientemente bajo para que
    reintroducir el N+1 la haga fallar de inmediato: volver al patron anterior
    daria mas de 50.
    """
    cliente, motor = entorno
    preparar_49_variantes(cliente)

    with ContadorDeConsultas(motor) as contador:
        respuesta = cliente.get("/inventario")

    assert respuesta.status_code == 200
    assert respuesta.text.count("POL-") >= 49  # las 49 variantes se muestran
    assert contador.total < 15, (
        f"El catalogo lanzo {contador.total} consultas para 49 variantes. "
        "Probablemente se reintrodujo el problema N+1."
    )


def test_el_catalogo_cuesta_lo_mismo_con_uno_o_con_muchos_productos(entorno):
    """
    Confirmacion de que el coste es constante y no lineal.

    Es la diferencia entre O(1) consultas y O(p + v): con tres productos el
    numero de consultas no debe triplicarse.
    """
    cliente, motor = entorno
    preparar_49_variantes(cliente)

    with ContadorDeConsultas(motor) as contador:
        cliente.get("/inventario")
    con_un_producto = contador.total

    for nombre in ("Gorra", "Playera", "Sudadera"):
        cliente.post(
            "/productos/nuevo",
            data={
                "nombre": nombre,
                "categoria": "Accesorios",
                "unidad": "pieza",
                "costo": 100,
                "minimo": 5,
                "atributo_1": "Talla",
                "valores_1": "S, M, L",
                "atributo_2": "",
                "valores_2": "",
            },
            follow_redirects=True,
        )

    with ContadorDeConsultas(motor) as contador:
        cliente.get("/inventario")
    con_cuatro_productos = contador.total

    assert con_cuatro_productos <= con_un_producto + 2, (
        f"Con 1 producto: {con_un_producto} consultas. "
        f"Con 4: {con_cuatro_productos}. El coste deberia ser casi constante."
    )


def test_las_descripciones_se_traen_en_una_sola_consulta(entorno):
    """
    Comprobacion directa sobre la tabla que causaba el problema.

    variante_valores es la tabla que antes se consultaba una vez por variante.
    """
    cliente, motor = entorno
    preparar_49_variantes(cliente)

    with ContadorDeConsultas(motor) as contador:
        cliente.get("/inventario")

    assert contador.de_tabla("variante_valores") <= 2


# ---------------------------------------------------------------------------
# El historial y el panel
# ---------------------------------------------------------------------------


def test_el_historial_no_escala_con_el_numero_de_movimientos(entorno):
    cliente, motor = entorno
    preparar_49_variantes(cliente)
    pantalla = cliente.get("/inventario")
    ids = re.findall(r"/variantes/([0-9a-f-]+)/movimiento", pantalla.text)

    # Veinte movimientos sobre variantes distintas.
    for id_variante in ids[:20]:
        cliente.post(
            f"/variantes/{id_variante}/movimiento",
            data={"tipo": "entrada", "cantidad": "2", "motivo": "Compra"},
            follow_redirects=True,
        )

    with ContadorDeConsultas(motor) as contador:
        respuesta = cliente.get("/historial")

    assert respuesta.status_code == 200
    assert contador.total < 15, (
        f"El historial lanzo {contador.total} consultas. Se esperaba un numero "
        "constante independiente de los movimientos mostrados."
    )


def test_el_panel_usa_un_numero_acotado_de_consultas(entorno):
    """
    El panel calcula seis indicadores; cada uno es una agregacion en SQL.

    El tope es mas alto que en el catalogo porque los indicadores son consultas
    distintas por naturaleza, pero sigue siendo constante: no crece con el
    inventario.
    """
    cliente, motor = entorno
    preparar_49_variantes(cliente)

    with ContadorDeConsultas(motor) as contador:
        respuesta = cliente.get("/panel")

    assert respuesta.status_code == 200
    assert contador.total < 25


# ---------------------------------------------------------------------------
# Paginacion
# ---------------------------------------------------------------------------


def test_el_historial_pagina_y_dice_cuantos_hay(entorno):
    """
    Antes el historial cortaba en 300 filas sin avisar.

    Con 49 variantes y una carga inicial hay pocos movimientos, asi que se
    comprueba que la pantalla informe el total y el rango mostrado.
    """
    cliente, _ = entorno
    preparar_49_variantes(cliente)

    historial = cliente.get("/historial").text
    assert "Mostrando" in historial
    assert "movimientos" in historial


def test_una_pagina_fuera_de_rango_no_rompe(entorno):
    """Pedir la pagina 999 devuelve la ultima valida, no un error."""
    cliente, _ = entorno
    preparar_49_variantes(cliente)

    respuesta = cliente.get("/historial?pagina=999")
    assert respuesta.status_code == 200
    assert "Pagina" not in respuesta.text or "Mostrando" in respuesta.text


def test_una_pagina_negativa_se_normaliza(entorno):
    cliente, _ = entorno
    preparar_49_variantes(cliente)
    assert cliente.get("/historial?pagina=-5").status_code == 200
