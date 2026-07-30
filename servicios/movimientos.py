"""
Motor de movimientos (Etapa 4, adelantado para la carga de inventario inicial).

Este modulo es el corazon de Stoqo. Implementa el Pseudocodigo B de la
planeacion y es el UNICO lugar del sistema autorizado a cambiar el stock de una
variante. Ninguna otra parte del codigo escribe `variante.stock` directamente.

Por que importa: si el stock se pudiera editar desde varios lugares, dos de esos
lugares acabarian discrepando y el historial dejaria de explicar las cifras. Con
un solo punto de entrada, cada unidad que entra o sale tiene su movimiento
correspondiente, y el stock actual siempre es reconstruible.

Tres garantias:

1. Atomicidad: el movimiento y la actualizacion del stock se confirman juntos o
   se revierten juntos. Nunca queda un movimiento sin efecto ni un stock sin
   movimiento que lo explique.
2. Lectura con bloqueo: el stock anterior se lee con SELECT ... FOR UPDATE, de
   modo que dos operaciones simultaneas sobre la misma variante no pueden leer
   el mismo stock anterior y perder una de las dos.
3. Delta firmado: el signo lo determina el tipo de movimiento mediante una regla
   unica (SIGNO_POR_TIPO), nunca el dato que llega del formulario.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from modelos import SIGNO_POR_TIPO, Incidencia, Movimiento, Variante


class TipoInvalido(Exception):
    """El tipo de movimiento no es uno de los cuatro permitidos."""


class CantidadInvalida(Exception):
    """La cantidad no es un entero mayor que cero."""


class VarianteNoDisponible(Exception):
    """La variante no existe, no esta activa o no pertenece a la empresa."""


class StockNegativoRequiereConfirmacion(Exception):
    """
    La salida deja el stock por debajo de cero.

    No es un error del sistema: es una decision que le corresponde a la persona
    propietaria. La operacion se detiene sin cambiar nada y se le pregunta.
    """

    def __init__(self, stock_anterior: int, stock_posterior: int) -> None:
        self.stock_anterior = stock_anterior
        self.stock_posterior = stock_posterior
        super().__init__(
            f"La operacion dejaria el stock en {stock_posterior} "
            f"(actualmente {stock_anterior})."
        )


class MotivoRequerido(Exception):
    """Se confirmo un stock negativo pero no se dio el motivo obligatorio."""


def registrar_movimiento(
    sesion: Session,
    empresa_id: uuid.UUID,
    variante_id: uuid.UUID,
    tipo: str,
    cantidad: int,
    *,
    motivo: str | None = None,
    confirmar_negativo: bool = False,
    compensa_a: uuid.UUID | None = None,
    confirmar: bool = True,
) -> Movimiento:
    """
    Registra un movimiento y actualiza el stock en una sola operacion.

    Parametros que merecen explicacion:
    - confirmar_negativo: la persona ya vio la advertencia y acepta seguir.
    - compensa_a: enlaza este movimiento con el original que corrige.
    - confirmar: hacer commit al terminar. Se pasa False cuando este movimiento
      forma parte de una operacion mas grande (por ejemplo cargar 49 variantes
      de golpe) y quien llama decide cuando cerrar la transaccion.

    Devuelve el movimiento creado.
    """
    if tipo not in SIGNO_POR_TIPO:
        raise TipoInvalido(f"Tipo de movimiento desconocido: {tipo}")

    # La cantidad debe ser un entero estricto: 2.5 unidades no existe en el MVP.
    if isinstance(cantidad, bool) or not isinstance(cantidad, int) or cantidad <= 0:
        raise CantidadInvalida("La cantidad debe ser un entero mayor que cero.")

    # Lectura con bloqueo: mientras esta transaccion no termine, ninguna otra
    # puede leer ni modificar esta fila. Es lo que impide que dos salidas
    # simultaneas calculen el mismo stock anterior.
    variante = sesion.scalars(
        select(Variante)
        .where(Variante.id == variante_id, Variante.empresa_id == empresa_id)
        .with_for_update()
    ).first()

    if variante is None or not variante.activa:
        raise VarianteNoDisponible(str(variante_id))

    # El signo NUNCA viene del formulario: se deriva del tipo.
    delta = SIGNO_POR_TIPO[tipo] * cantidad
    stock_anterior = variante.stock
    stock_posterior = stock_anterior + delta

    es_incidencia = False
    if stock_posterior < 0:
        if not confirmar_negativo:
            # Se aborta sin escribir nada. El caso limite "salida mayor al stock"
            # de la planeacion: advertir y continuar solo con confirmacion.
            sesion.rollback()
            raise StockNegativoRequiereConfirmacion(stock_anterior, stock_posterior)
        if not motivo or not motivo.strip():
            sesion.rollback()
            raise MotivoRequerido(
                "Un stock negativo necesita un motivo para quedar justificado."
            )
        es_incidencia = True

    movimiento = Movimiento(
        empresa_id=empresa_id,
        variante_id=variante.id,
        tipo=tipo,
        cantidad=cantidad,
        delta=delta,
        stock_anterior=stock_anterior,
        stock_posterior=stock_posterior,
        es_incidencia=es_incidencia,
        motivo=motivo.strip() if motivo else None,
        compensa_a=compensa_a,
    )
    sesion.add(movimiento)

    # El stock nuevo no se calcula aparte: es exactamente el stock_posterior que
    # quedo registrado en el movimiento. Una sola fuente de verdad.
    variante.stock = stock_posterior

    if es_incidencia:
        sesion.flush()
        sesion.add(
            Incidencia(
                empresa_id=empresa_id,
                movimiento_id=movimiento.id,
                motivo=movimiento.motivo,
                confirmada=True,
            )
        )

    if confirmar:
        sesion.commit()
    else:
        sesion.flush()

    return movimiento


def cargar_existencias(
    sesion: Session,
    empresa_id: uuid.UUID,
    cantidades: dict[uuid.UUID, int],
    *,
    motivo: str = "Inventario inicial",
) -> list[Movimiento]:
    """
    Carga existencias de varias variantes en una sola transaccion.

    Es lo que necesita el caso KOVA: 5 azules medianas, 4 azules grandes, 0 de
    otras. Cada cantidad se convierte en su propio movimiento de entrada, asi que
    el historial explica variante por variante de donde salio cada unidad.

    Las cantidades en cero se omiten: no existe un movimiento de cero unidades.
    Si alguna variante falla, no se guarda ninguna.
    """
    movimientos = []
    for variante_id, cantidad in cantidades.items():
        if cantidad is None or cantidad == 0:
            continue
        if cantidad < 0:
            sesion.rollback()
            raise CantidadInvalida("Las existencias no pueden ser negativas.")
        movimientos.append(
            registrar_movimiento(
                sesion,
                empresa_id,
                variante_id,
                "entrada",
                int(cantidad),
                motivo=motivo,
                confirmar=False,
            )
        )
    sesion.commit()
    return movimientos


# ---------------------------------------------------------------------------
# Cancelacion y compensacion (Pseudocodigo C)
# ---------------------------------------------------------------------------

TIPO_OPUESTO = {
    "entrada": "salida",
    "salida": "entrada",
    "ajuste_positivo": "ajuste_negativo",
    "ajuste_negativo": "ajuste_positivo",
}


class MovimientoNoCancelable(Exception):
    """El movimiento no existe, ya fue cancelado, o es una compensacion."""


def cancelar_movimiento(
    sesion: Session,
    empresa_id: uuid.UUID,
    movimiento_id: uuid.UUID,
    motivo: str,
    *,
    confirmar_negativo: bool = False,
) -> Movimiento:
    """
    Corrige un movimiento sin borrarlo (Pseudocodigo C de la planeacion).

    Por que no se edita ni se borra el original: un historial que se puede
    reescribir no explica nada. Si un error se corrige borrando la fila, el stock
    cuadra pero nadie puede saber que paso ni cuando. Aqui el original se marca
    como cancelado y se crea un movimiento NUEVO con el delta contrario, enlazado
    al primero. El historial conserva las tres cosas: el error, la cancelacion y
    la operacion que restauro el inventario.

    Reglas que se hacen cumplir:
    - Solo movimientos de la propia empresa.
    - Un movimiento ya cancelado no se puede cancelar otra vez.
    - Una compensacion no se puede cancelar (seria una cadena infinita).
    """
    if not motivo or not motivo.strip():
        raise MotivoRequerido("Cancelar un movimiento exige un motivo.")

    original = sesion.scalars(
        select(Movimiento).where(
            Movimiento.id == movimiento_id, Movimiento.empresa_id == empresa_id
        )
    ).first()

    if original is None:
        raise MovimientoNoCancelable("El movimiento no existe en esta empresa.")
    if original.cancelado:
        # Caso limite de la planeacion: bloquear una segunda cancelacion.
        raise MovimientoNoCancelable("Este movimiento ya fue cancelado.")
    if original.compensa_a is not None:
        raise MovimientoNoCancelable(
            "Una compensacion no se cancela. Cancela el movimiento original."
        )

    # El delta contrario se logra registrando el tipo opuesto con la misma
    # cantidad, en lugar de inventar un delta a mano. Asi la compensacion pasa
    # por las mismas validaciones que cualquier otro movimiento.
    compensacion = registrar_movimiento(
        sesion,
        empresa_id,
        original.variante_id,
        TIPO_OPUESTO[original.tipo],
        original.cantidad,
        motivo=f"Cancelacion: {motivo.strip()}",
        confirmar_negativo=confirmar_negativo,
        compensa_a=original.id,
        confirmar=False,
    )

    original.cancelado = True
    sesion.commit()
    return compensacion
