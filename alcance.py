"""
Alcance por empresa (Etapa 1).

El aislamiento tiene dos capas en Stoqo:

1. La base de datos, con llaves foraneas compuestas (ver modelos.py). Impide que
   los datos de dos marcas se mezclen estructuralmente.
2. Esta clase, que impide LEER datos de otra empresa.

La segunda capa existe porque la primera no cubre las consultas: nada evita que
un `select(Producto)` sin filtro devuelva productos de todas las marcas. En
lugar de confiar en recordar el filtro en cada consulta, todas las lecturas
pasan por aqui y el filtro se aplica una sola vez, en un solo lugar auditable.
"""

from __future__ import annotations

import uuid
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from modelos import Movimiento, Producto, Variante

# Tablas que llevan empresa_id y por lo tanto pueden acotarse directamente.
ModeloConEmpresa = TypeVar("ModeloConEmpresa", Producto, Variante, Movimiento)


class EmpresaNoAutorizada(Exception):
    """Se intento acceder a un registro que pertenece a otra empresa."""


class AlcanceEmpresa:
    """
    Punto unico de acceso a los datos de una empresa autenticada.

    Uso:
        alcance = AlcanceEmpresa(sesion, empresa_id)
        productos = alcance.todos(Producto)
        variante = alcance.obtener(Variante, variante_id)
    """

    def __init__(self, sesion: Session, empresa_id: uuid.UUID) -> None:
        if empresa_id is None:
            raise ValueError("El alcance requiere una empresa autenticada.")
        self.sesion = sesion
        self.empresa_id = empresa_id

    def consulta(self, modelo: type[ModeloConEmpresa]) -> Select:
        """Devuelve un SELECT ya acotado a la empresa. Toda lectura nace aqui."""
        return select(modelo).where(modelo.empresa_id == self.empresa_id)

    def todos(self, modelo: type[ModeloConEmpresa]) -> list[ModeloConEmpresa]:
        """Lista todos los registros del modelo dentro de la empresa."""
        return list(self.sesion.scalars(self.consulta(modelo)).all())

    def obtener(
        self, modelo: type[ModeloConEmpresa], registro_id: uuid.UUID
    ) -> ModeloConEmpresa | None:
        """
        Busca un registro por id, pero solo dentro de la empresa.

        Si el id existe pero pertenece a otra marca, devuelve None: para esta
        sesion ese registro simplemente no existe. No se distingue entre
        "no existe" y "no es tuyo", porque esa diferencia ya seria una fuga de
        informacion sobre el inventario de otra empresa.
        """
        consulta = self.consulta(modelo).where(modelo.id == registro_id)
        return self.sesion.scalars(consulta).first()

    def exigir(
        self, modelo: type[ModeloConEmpresa], registro_id: uuid.UUID
    ) -> ModeloConEmpresa:
        """
        Igual que obtener(), pero falla en lugar de devolver None.

        Se usa antes de escribir: si un movimiento va a modificar una variante,
        esta operacion debe interrumpirse de forma ruidosa cuando la variante no
        pertenece a la empresa autenticada.
        """
        registro = self.obtener(modelo, registro_id)
        if registro is None:
            raise EmpresaNoAutorizada(
                f"{modelo.__name__} {registro_id} no pertenece a la empresa "
                f"{self.empresa_id}."
            )
        return registro
