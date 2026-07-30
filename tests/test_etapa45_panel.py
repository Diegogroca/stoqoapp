"""
Pruebas de las Etapas 4 y 5: cancelacion compensatoria, historial y dashboard.

Cubre CE-06 a CE-13 de la planeacion.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import dependencias
import main
from db import crear_tablas_para_pruebas
from modelos import Movimiento, Variante
from servicios.cuentas import registrar
from servicios.indicadores import (
    entradas_y_salidas,
    estado_por_producto,
    productos_con_alerta,
    productos_mas_movidos,
    resumen_por_categoria,
    unidades_disponibles,
    valor_inventario,
)
from servicios.movimientos import (
    MotivoRequerido,
    MovimientoNoCancelable,
    cancelar_movimiento,
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
def inventario(sesion: Session):
    """KOVA con una gorra simple (stock 20) y un polo de 2 variantes."""
    empresa, _ = registrar(sesion, "KOVA", "dueno@kova.com", "clave_seguraKOVA")
    gorra = crear_producto(
        sesion,
        empresa.id,
        "Gorra",
        categoria="Accesorios",
        costo=100,
        minimo=5,
        existencia_inicial=20,
    )
    polo = crear_producto(
        sesion,
        empresa.id,
        "Polo Premium",
        categoria="Playeras",
        costo=250,
        minimo=10,
        atributos=[{"nombre": "Talla", "valores": ["M", "L"]}],
    )
    return empresa, gorra, polo


def variante_de(sesion: Session, producto) -> Variante:
    return sesion.scalars(
        select(Variante).where(Variante.producto_id == producto.id).order_by(Variante.sku)
    ).first()


# ---------------------------------------------------------------------------
# CE-10: cancelacion y compensacion
# ---------------------------------------------------------------------------


def test_cancelar_crea_compensacion_y_restaura_el_stock(sesion: Session, inventario):
    empresa, gorra, _ = inventario
    variante = variante_de(sesion, gorra)

    salida = registrar_movimiento(sesion, empresa.id, variante.id, "salida", 6)
    assert variante.stock == 14

    compensacion = cancelar_movimiento(
        sesion, empresa.id, salida.id, "Se registro dos veces"
    )

    assert variante.stock == 20  # el stock volvio a su valor previo
    assert compensacion.tipo == "entrada"  # el opuesto de una salida
    assert compensacion.delta == 6
    assert compensacion.compensa_a == salida.id
    assert salida.cancelado is True


def test_el_original_no_se_borra(sesion: Session, inventario):
    """La trazabilidad exige que el error siga visible."""
    empresa, gorra, _ = inventario
    variante = variante_de(sesion, gorra)

    salida = registrar_movimiento(sesion, empresa.id, variante.id, "salida", 6)
    cancelar_movimiento(sesion, empresa.id, salida.id, "Error de captura")

    movimientos = sesion.scalars(select(Movimiento)).all()
    # Entrada inicial + salida original + compensacion = 3 filas.
    assert len(movimientos) == 3
    assert sesion.get(Movimiento, salida.id) is not None


def test_no_se_puede_cancelar_dos_veces(sesion: Session, inventario):
    """Caso limite: bloquear la segunda cancelacion."""
    empresa, gorra, _ = inventario
    variante = variante_de(sesion, gorra)

    salida = registrar_movimiento(sesion, empresa.id, variante.id, "salida", 6)
    cancelar_movimiento(sesion, empresa.id, salida.id, "Primera")

    with pytest.raises(MovimientoNoCancelable):
        cancelar_movimiento(sesion, empresa.id, salida.id, "Segunda")


def test_una_compensacion_no_se_puede_cancelar(sesion: Session, inventario):
    """Cancelar una compensacion abriria una cadena infinita de correcciones."""
    empresa, gorra, _ = inventario
    variante = variante_de(sesion, gorra)

    salida = registrar_movimiento(sesion, empresa.id, variante.id, "salida", 6)
    compensacion = cancelar_movimiento(sesion, empresa.id, salida.id, "Error")

    with pytest.raises(MovimientoNoCancelable):
        cancelar_movimiento(sesion, empresa.id, compensacion.id, "Otra vez")


def test_cancelar_exige_motivo(sesion: Session, inventario):
    empresa, gorra, _ = inventario
    variante = variante_de(sesion, gorra)
    salida = registrar_movimiento(sesion, empresa.id, variante.id, "salida", 6)

    with pytest.raises(MotivoRequerido):
        cancelar_movimiento(sesion, empresa.id, salida.id, "   ")


def test_no_se_puede_cancelar_un_movimiento_de_otra_empresa(sesion: Session, inventario):
    empresa, gorra, _ = inventario
    otra, _ = registrar(sesion, "Panaderia", "dueno@pan.com", "clave_seguraPAN")
    variante = variante_de(sesion, gorra)
    salida = registrar_movimiento(sesion, empresa.id, variante.id, "salida", 3)

    with pytest.raises(MovimientoNoCancelable):
        cancelar_movimiento(sesion, otra.id, salida.id, "Intruso")


# ---------------------------------------------------------------------------
# CE-12: indicadores del dashboard
# ---------------------------------------------------------------------------


def test_unidades_y_valor_coinciden_con_el_calculo_manual(sesion: Session, inventario):
    """
    CE-12: las metricas se comparan contra aritmetica hecha a mano.

    Gorra: 20 unidades x $100 = $2,000.
    Polo M: 4 x $250 = $1,000. Polo L: 6 x $250 = $1,500.
    Total: 30 unidades, $4,500.
    """
    empresa, gorra, polo = inventario
    variantes = sesion.scalars(
        select(Variante).where(Variante.producto_id == polo.id).order_by(Variante.sku)
    ).all()
    registrar_movimiento(sesion, empresa.id, variantes[0].id, "entrada", 4)
    registrar_movimiento(sesion, empresa.id, variantes[1].id, "entrada", 6)

    assert unidades_disponibles(sesion, empresa.id) == 30
    assert valor_inventario(sesion, empresa.id) == 4500.0


def test_las_entradas_y_salidas_se_separan(sesion: Session, inventario):
    empresa, gorra, _ = inventario
    variante = variante_de(sesion, gorra)
    registrar_movimiento(sesion, empresa.id, variante.id, "salida", 5)
    registrar_movimiento(sesion, empresa.id, variante.id, "entrada", 3)

    flujo = entradas_y_salidas(sesion, empresa.id)
    assert flujo["entradas"] == 23  # 20 iniciales + 3
    assert flujo["salidas"] == 5


def test_las_cancelaciones_no_cuentan_como_actividad(sesion: Session, inventario):
    """
    Regla de la planeacion: los rankings excluyen cancelados y compensaciones.

    Una correccion contable no es mercancia que se movio.
    """
    empresa, gorra, _ = inventario
    variante = variante_de(sesion, gorra)

    salida = registrar_movimiento(sesion, empresa.id, variante.id, "salida", 5)
    antes = entradas_y_salidas(sesion, empresa.id)
    assert antes["salidas"] == 5

    cancelar_movimiento(sesion, empresa.id, salida.id, "Error de captura")

    despues = entradas_y_salidas(sesion, empresa.id)
    # La salida desaparece del conteo y la compensacion no se suma como entrada.
    assert despues["salidas"] == 0
    assert despues["entradas"] == 20


def test_el_ranking_usa_valores_absolutos(sesion: Session, inventario):
    """Entrar 100 y salir 100 es mucha actividad, aunque el neto sea cero."""
    empresa, gorra, polo = inventario
    variante_polo = variante_de(sesion, polo)

    registrar_movimiento(sesion, empresa.id, variante_polo.id, "entrada", 50)
    registrar_movimiento(sesion, empresa.id, variante_polo.id, "salida", 50)

    ranking = productos_mas_movidos(sesion, empresa.id)
    primero = ranking[0]
    assert primero["nombre"] == "Polo Premium"
    assert primero["volumen"] == 100  # no cero


# ---------------------------------------------------------------------------
# CE-13: alertas de reposicion
# ---------------------------------------------------------------------------


def test_el_minimo_se_compara_contra_la_suma_de_variantes(sesion: Session, inventario):
    """
    El minimo pertenece al producto, no a la variante.

    El polo tiene minimo 10. Con 6 en M y 6 en L suma 12: no esta bajo, aunque
    cada variante suelta este por debajo de 10.
    """
    empresa, _, polo = inventario
    variantes = sesion.scalars(
        select(Variante).where(Variante.producto_id == polo.id)
    ).all()
    for variante in variantes:
        registrar_movimiento(sesion, empresa.id, variante.id, "entrada", 6)

    estados = {fila["nombre"]: fila for fila in estado_por_producto(sesion, empresa.id)}
    assert estados["Polo Premium"]["stock_total"] == 12
    assert estados["Polo Premium"]["estado"] == "disponible"


def test_los_tres_estados_se_distinguen(sesion: Session, inventario):
    empresa, gorra, polo = inventario
    estados = {fila["nombre"]: fila["estado"] for fila in estado_por_producto(sesion, empresa.id)}

    assert estados["Gorra"] == "disponible"  # 20 sobre minimo 5
    assert estados["Polo Premium"] == "agotado"  # sin existencias

    variante = variante_de(sesion, gorra)
    registrar_movimiento(sesion, empresa.id, variante.id, "salida", 16)
    estados = {fila["nombre"]: fila["estado"] for fila in estado_por_producto(sesion, empresa.id)}
    assert estados["Gorra"] == "bajo"  # 4 bajo minimo 5


def test_solo_los_productos_con_alerta_aparecen_en_alertas(sesion: Session, inventario):
    empresa, _, _ = inventario
    nombres = {fila["nombre"] for fila in productos_con_alerta(sesion, empresa.id)}
    assert nombres == {"Polo Premium"}  # la gorra esta disponible


def test_el_resumen_por_categoria_agrupa(sesion: Session, inventario):
    empresa, _, _ = inventario
    resumen = {fila["categoria"]: fila for fila in resumen_por_categoria(sesion, empresa.id)}
    assert resumen["Accesorios"]["unidades"] == 20
    assert resumen["Accesorios"]["valor"] == 2000.0
    assert resumen["Playeras"]["unidades"] == 0


def test_los_indicadores_estan_aislados_por_empresa(sesion: Session, inventario):
    """CE-01 en los indicadores: una marca no suma el inventario de otra."""
    empresa, _, _ = inventario
    otra, _ = registrar(sesion, "Panaderia", "dueno@pan.com", "clave_seguraPAN")
    crear_producto(sesion, otra.id, "Concha", costo=15, existencia_inicial=200)

    assert unidades_disponibles(sesion, empresa.id) == 20
    assert unidades_disponibles(sesion, otra.id) == 200


# ---------------------------------------------------------------------------
# Flujo por HTTP: stock negativo y cancelacion
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


def preparar_gorra(cliente: TestClient) -> str:
    """Registra KOVA, crea una gorra simple y devuelve el id de su variante."""
    import re

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
            "nombre": "Gorra",
            "categoria": "Accesorios",
            "unidad": "pieza",
            "costo": 100,
            "minimo": 5,
            "atributo_1": "",
            "valores_1": "",
            "atributo_2": "",
            "valores_2": "",
        },
        follow_redirects=True,
    )
    id_variante = re.search(r'name="cantidad_([0-9a-f-]+)"', pantalla.text).group(1)
    cliente.post(
        f"/productos/{re.search(r'action=./productos/([0-9a-f-]+)/existencias', pantalla.text).group(1)}/existencias",
        data={f"cantidad_{id_variante}": "10"},
        follow_redirects=True,
    )
    return id_variante


def test_el_panel_carga_con_las_seis_metricas(cliente: TestClient):
    preparar_gorra(cliente)
    panel = cliente.get("/panel")
    assert panel.status_code == 200
    assert "Unidades disponibles" in panel.text
    assert "Valor a costo" in panel.text
    assert "Productos con alerta" in panel.text
    assert "Requieren reposicion" in panel.text
    assert "Mayor movimiento" in panel.text
    assert "Inventario por categoria" in panel.text


def test_una_salida_normal_se_registra(cliente: TestClient):
    id_variante = preparar_gorra(cliente)
    respuesta = cliente.post(
        f"/variantes/{id_variante}/movimiento",
        data={"tipo": "salida", "cantidad": "3", "motivo": "Venta"},
        follow_redirects=True,
    )
    assert respuesta.status_code == 200
    assert "Salida" in respuesta.text
    assert "Venta" in respuesta.text


def test_ce09_una_salida_mayor_al_stock_pide_confirmacion(cliente: TestClient):
    id_variante = preparar_gorra(cliente)
    respuesta = cliente.post(
        f"/variantes/{id_variante}/movimiento",
        data={"tipo": "salida", "cantidad": "30", "motivo": ""},
    )
    assert respuesta.status_code == 200
    assert "deja el stock en -20" in respuesta.text
    assert "confirmar_negativo" in respuesta.text
    # Sin confirmar, el stock no cambio.
    assert ">10<" in cliente.get("/inventario").text


def test_ce09_confirmado_con_motivo_queda_como_incidencia(cliente: TestClient):
    id_variante = preparar_gorra(cliente)
    cliente.post(
        f"/variantes/{id_variante}/movimiento",
        data={
            "tipo": "salida",
            "cantidad": "30",
            "motivo": "Venta registrada tarde",
            "confirmar_negativo": "1",
        },
        follow_redirects=True,
    )
    historial = cliente.get("/historial?incidencias=1").text
    assert "Incidencia" in historial
    assert "Venta registrada tarde" in historial


def test_una_cantidad_con_letras_se_explica(cliente: TestClient):
    id_variante = preparar_gorra(cliente)
    respuesta = cliente.post(
        f"/variantes/{id_variante}/movimiento",
        data={"tipo": "salida", "cantidad": "tres", "motivo": ""},
    )
    assert respuesta.status_code == 400
    assert "numero entero" in respuesta.text


def test_ce10_cancelar_desde_el_historial(cliente: TestClient):
    import re

    id_variante = preparar_gorra(cliente)
    cliente.post(
        f"/variantes/{id_variante}/movimiento",
        data={"tipo": "salida", "cantidad": "4", "motivo": "Venta"},
        follow_redirects=True,
    )
    historial = cliente.get("/historial").text
    ids = re.findall(r'/movimientos/([0-9a-f-]+)/cancelar', historial)
    assert ids

    # La pantalla dedicada explica el efecto antes de confirmar.
    confirmacion = cliente.get(f"/movimientos/{ids[0]}/cancelar")
    assert confirmacion.status_code == 200
    assert "Revertir este movimiento" in confirmacion.text

    cliente.post(
        f"/movimientos/{ids[0]}/cancelar",
        data={"motivo": "Se registro dos veces"},
        follow_redirects=True,
    )
    assert ">10<" in cliente.get("/inventario").text  # stock restaurado


def test_la_vista_operativa_oculta_las_correcciones(cliente: TestClient):
    """
    Dos audiencias, dos vistas.

    El dueño quiere ver mercancia que se movio; el contador quiere ver todo,
    incluidos los errores. Ningun dato se pierde: cambia lo que se muestra por
    omision.
    """
    import re

    id_variante = preparar_gorra(cliente)
    cliente.post(
        f"/variantes/{id_variante}/movimiento",
        data={"tipo": "salida", "cantidad": "4", "motivo": "Venta"},
        follow_redirects=True,
    )
    ids = re.findall(r'/movimientos/([0-9a-f-]+)/cancelar', cliente.get("/historial").text)
    cliente.post(
        f"/movimientos/{ids[0]}/cancelar",
        data={"motivo": "Error de captura"},
        follow_redirects=True,
    )

    operativo = cliente.get("/historial").text
    auditoria = cliente.get("/historial?vista=auditoria").text

    # La salida revertida y su correccion no ensucian la vista operativa.
    assert "revertido" not in operativo.lower()
    assert "Error de captura" not in operativo
    # Pero siguen ahi, intactas, en auditoria.
    assert "Error de captura" in auditoria
    assert "cancelado" in auditoria
    assert "compensacion" in auditoria


def test_no_se_puede_revertir_dos_veces_desde_la_pantalla(cliente: TestClient):
    import re

    id_variante = preparar_gorra(cliente)
    cliente.post(
        f"/variantes/{id_variante}/movimiento",
        data={"tipo": "salida", "cantidad": "4", "motivo": "Venta"},
        follow_redirects=True,
    )
    ids = re.findall(r'/movimientos/([0-9a-f-]+)/cancelar', cliente.get("/historial").text)
    cliente.post(
        f"/movimientos/{ids[0]}/cancelar", data={"motivo": "Primera"}, follow_redirects=True
    )

    # La pantalla ya no ofrece el formulario y explica por que.
    pantalla = cliente.get(f"/movimientos/{ids[0]}/cancelar")
    assert "ya fue revertido" in pantalla.text
    assert 'name="motivo"' not in pantalla.text  # no hay formulario que enviar


def test_el_historial_exige_sesion(cliente: TestClient):
    assert cliente.get("/historial", follow_redirects=False).status_code == 303


def test_el_panel_exige_sesion(cliente: TestClient):
    assert cliente.get("/panel", follow_redirects=False).status_code == 303


def test_no_se_puede_mover_una_variante_ajena(cliente: TestClient):
    id_variante = preparar_gorra(cliente)
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
    ajena = cliente.get(f"/variantes/{id_variante}/movimiento", follow_redirects=False)
    assert ajena.status_code == 303


# ---------------------------------------------------------------------------
# Graficas del panel
# ---------------------------------------------------------------------------


def test_el_panel_agrupa_el_inventario_por_atributo(sesion: Session, inventario):
    """
    Con muchas variantes el catalogo es ilegible; agregado por atributo responde
    preguntas concretas: de que talla hay mas inventario.
    """
    from servicios.indicadores import stock_por_atributo

    empresa, _, polo = inventario
    variantes = sesion.scalars(
        select(Variante).where(Variante.producto_id == polo.id).order_by(Variante.sku)
    ).all()
    registrar_movimiento(sesion, empresa.id, variantes[0].id, "entrada", 7)
    registrar_movimiento(sesion, empresa.id, variantes[1].id, "entrada", 3)

    agrupado = stock_por_atributo(sesion, empresa.id)

    assert "Talla" in agrupado
    unidades = {fila["valor"]: fila["unidades"] for fila in agrupado["Talla"]}
    assert unidades == {"M": 7, "L": 3}
    # Ordenado de mayor a menor: la primera fila es la talla con mas stock.
    assert agrupado["Talla"][0]["valor"] == "M"


def test_el_flujo_diario_rellena_los_dias_sin_movimiento(sesion: Session, inventario):
    """
    Una grafica con huecos miente sobre el ritmo del negocio: un dia sin ventas
    debe aparecer como valle, no desaparecer del eje.
    """
    from servicios.indicadores import flujo_diario

    empresa, gorra, _ = inventario
    variante = variante_de(sesion, gorra)
    registrar_movimiento(sesion, empresa.id, variante.id, "salida", 4)

    serie = flujo_diario(sesion, empresa.id, dias=14)

    assert len(serie) == 14  # catorce dias, aunque solo uno tenga datos
    assert serie[-1]["salidas"] == 4  # hoy es el ultimo
    assert all("etiqueta" in dia for dia in serie)
    assert sum(dia["salidas"] for dia in serie) == 4


def test_la_concentracion_del_valor_ordena_y_acumula(sesion: Session, inventario):
    """Analisis ABC: pocos productos concentran la mayor parte del capital."""
    from servicios.indicadores import concentracion_del_valor

    empresa, _, polo = inventario
    variante = variante_de(sesion, polo)
    registrar_movimiento(sesion, empresa.id, variante.id, "entrada", 40)

    serie = concentracion_del_valor(sesion, empresa.id)

    # Polo: 40 x $250 = $10,000. Gorra: 20 x $100 = $2,000. Total $12,000.
    assert serie[0]["nombre"] == "Polo Premium"
    assert serie[0]["valor"] == 10000.0
    assert serie[0]["porcentaje"] == pytest.approx(83.3, abs=0.1)
    assert serie[-1]["acumulado"] == pytest.approx(100.0, abs=0.1)


def test_sin_inventario_las_series_no_truenan(sesion: Session):
    """Empresa recien creada: las graficas deben quedar vacias, no fallar."""
    from servicios.indicadores import (
        concentracion_del_valor,
        flujo_diario,
        stock_por_atributo,
    )

    empresa, _ = registrar(sesion, "Vacia", "dueno@vacia.com", "clave_seguraVAC")

    assert stock_por_atributo(sesion, empresa.id) == {}
    assert concentracion_del_valor(sesion, empresa.id) == []
    assert len(flujo_diario(sesion, empresa.id, dias=7)) == 7


def test_el_panel_renderiza_las_tres_graficas(cliente: TestClient):
    import re

    id_variante = preparar_gorra(cliente)
    cliente.post(
        f"/variantes/{id_variante}/movimiento",
        data={"tipo": "salida", "cantidad": "3", "motivo": "Venta"},
        follow_redirects=True,
    )
    panel = cliente.get("/panel").text

    assert "Inventario por atributo" not in panel  # la gorra es simple, sin atributos
    assert "Movimiento de los ultimos 14 dias" in panel
    assert "Donde esta detenido el dinero" in panel
    assert "<svg" in panel  # la grafica de flujo se dibujo
    assert re.search(r'<rect[^>]+fill="#B4541E"', panel)  # hay barra de salida


def test_el_panel_muestra_el_selector_de_atributos_cuando_hay(cliente: TestClient):
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
            "nombre": "Polo",
            "categoria": "Playeras",
            "unidad": "pieza",
            "costo": 250,
            "minimo": 5,
            "atributo_1": "Talla",
            "valores_1": "S, M, L",
            "atributo_2": "Color",
            "valores_2": "negro, azul",
        },
        follow_redirects=True,
    )
    import re

    ids = re.findall(r'name="cantidad_([0-9a-f-]+)"', pantalla.text)
    id_producto = re.search(r"/productos/([0-9a-f-]+)/existencias", pantalla.text).group(1)
    cliente.post(
        f"/productos/{id_producto}/existencias",
        data={f"cantidad_{ids[0]}": "5", f"cantidad_{ids[1]}": "9"},
        follow_redirects=True,
    )

    panel = cliente.get("/panel").text
    assert "Inventario por atributo" in panel
    assert 'data-atributo="0"' in panel
    assert "Talla" in panel and "Color" in panel
