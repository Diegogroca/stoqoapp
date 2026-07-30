"""
Pruebas de flujo por HTTP (Etapas 2 y 3).

Las otras pruebas verifican los servicios en aislamiento. Estas recorren la
aplicacion como lo haria una persona: registrarse, ver el onboarding, crear un
producto con variantes y volver al catalogo. Sirven para detectar errores que no
aparecen probando funciones sueltas, como una plantilla mal escrita o una cookie
que no se envia.

La base de datos se sustituye por SQLite en memoria mediante
dependency_overrides, el mecanismo de FastAPI para reemplazar dependencias en
pruebas.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import dependencias
import main
from db import crear_tablas_para_pruebas


@pytest.fixture(autouse=True)
def secreto_de_prueba(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "secreto_solo_para_pruebas_1234567890")


@pytest.fixture()
def cliente() -> TestClient:
    """Cliente HTTP con una base de datos limpia por prueba."""
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


def registrar_kova(cliente: TestClient):
    return cliente.post(
        "/registro",
        data={
            "empresa": "KOVA",
            "correo": "dueno@kova.com",
            "password": "clave_seguraKOVA",
        },
        follow_redirects=True,
    )


# ---------------------------------------------------------------------------
# Acceso
# ---------------------------------------------------------------------------


def test_el_inventario_exige_sesion(cliente: TestClient):
    respuesta = cliente.get("/inventario", follow_redirects=False)
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/entrar"


def test_el_alta_de_productos_exige_sesion(cliente: TestClient):
    respuesta = cliente.get("/productos/nuevo", follow_redirects=False)
    assert respuesta.status_code == 303


def test_las_pantallas_publicas_cargan(cliente: TestClient):
    assert cliente.get("/").status_code == 200
    assert cliente.get("/registro").status_code == 200
    assert cliente.get("/entrar").status_code == 200


# ---------------------------------------------------------------------------
# CE-02: registro y onboarding
# ---------------------------------------------------------------------------


def test_al_registrarse_se_llega_al_onboarding(cliente: TestClient):
    """Una empresa nueva no ve una tabla vacia, ve una invitacion a actuar."""
    respuesta = registrar_kova(cliente)
    assert respuesta.status_code == 200
    assert "Agregar primer producto" in respuesta.text


def test_el_registro_duplicado_muestra_el_error_en_la_pantalla(cliente: TestClient):
    registrar_kova(cliente)
    cliente.post("/salir")
    respuesta = cliente.post(
        "/registro",
        data={
            "empresa": "Otra",
            "correo": "dueno@kova.com",
            "password": "otra_clave_1234",
        },
    )
    assert respuesta.status_code == 400
    assert "ya tiene una cuenta" in respuesta.text


def test_entrar_con_credenciales_malas_no_da_acceso(cliente: TestClient):
    registrar_kova(cliente)
    cliente.post("/salir")
    respuesta = cliente.post(
        "/entrar", data={"correo": "dueno@kova.com", "password": "equivocada"}
    )
    assert respuesta.status_code == 401
    assert "incorrectos" in respuesta.text


def test_salir_cierra_la_sesion(cliente: TestClient):
    registrar_kova(cliente)
    cliente.post("/salir")
    assert cliente.get("/inventario", follow_redirects=False).status_code == 303


def test_entrar_de_nuevo_recupera_el_inventario(cliente: TestClient):
    registrar_kova(cliente)
    crear_polo(cliente)
    cliente.post("/salir")
    respuesta = cliente.post(
        "/entrar",
        data={"correo": "dueno@kova.com", "password": "clave_seguraKOVA"},
        follow_redirects=True,
    )
    assert "Catalogo" in respuesta.text
    assert "Polo Premium" in respuesta.text


# ---------------------------------------------------------------------------
# CE-04: alta de producto con variantes
# ---------------------------------------------------------------------------


def crear_polo(cliente: TestClient, **cambios):
    datos = {
        "nombre": "Polo Premium",
        "categoria": "Playeras",
        "unidad": "pieza",
        "costo": 250,
        "minimo": 10,
        "atributo_1": "Talla",
        "valores_1": "S, M, L",
        "atributo_2": "Color",
        "valores_2": "negro, blanco",
    }
    datos.update(cambios)
    return cliente.post("/productos/nuevo", data=datos, follow_redirects=True)


def test_crear_un_producto_lleva_a_capturar_existencias(cliente: TestClient):
    """El alta define la estructura; las cantidades se capturan despues."""
    registrar_kova(cliente)
    respuesta = crear_polo(cliente)

    assert respuesta.status_code == 200
    assert "Captura las existencias" in respuesta.text
    # 3 tallas x 2 colores = 6 filas, una por variante.
    assert respuesta.text.count('name="cantidad_') == 6


def test_un_producto_simple_tambien_aparece(cliente: TestClient):
    registrar_kova(cliente)
    crear_polo(
        cliente, nombre="Gorra", atributo_1="", valores_1="", atributo_2="", valores_2=""
    )
    assert "Gorra" in cliente.get("/inventario").text


def test_demasiadas_variantes_devuelve_el_formulario_con_el_error(cliente: TestClient):
    registrar_kova(cliente)
    muchos = ", ".join(str(n) for n in range(30))
    respuesta = crear_polo(cliente, valores_1=muchos, valores_2=muchos)
    assert respuesta.status_code == 400
    assert "el maximo es" in respuesta.text
    # Los datos capturados se conservan para no obligar a escribir todo de nuevo.
    assert "Polo Premium" in respuesta.text


def test_un_atributo_sin_valores_se_explica(cliente: TestClient):
    registrar_kova(cliente)
    respuesta = crear_polo(cliente, valores_2="")
    assert respuesta.status_code == 400
    assert "no tiene valores" in respuesta.text


def test_la_vista_previa_cuenta_sin_crear(cliente: TestClient):
    registrar_kova(cliente)
    respuesta = cliente.post(
        "/productos/vista-previa",
        data={
            "atributo_1": "Talla",
            "valores_1": "XXS, XS, S, M, L, XL, XXL",
            "atributo_2": "Color",
            "valores_2": "negro, blanco, azul, verde, rojo, gris, beige",
        },
    )
    assert respuesta.text == "49"
    # No se creo nada: el catalogo sigue vacio.
    assert "Agregar primer producto" in cliente.get("/inventario").text


# ---------------------------------------------------------------------------
# CE-01: aislamiento visto desde la interfaz
# ---------------------------------------------------------------------------


def test_una_empresa_no_ve_el_catalogo_de_la_otra(cliente: TestClient):
    registrar_kova(cliente)
    crear_polo(cliente)
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
    inventario = cliente.get("/inventario").text

    assert "Polo Premium" not in inventario
    assert "Agregar primer producto" in inventario
