"""
Modelos de datos de Stoqo (Etapa 1).

Estos modelos son el espejo en Python del esquema SQL de
migrations/001_esquema_inicial.sql. Se declaran una sola vez y sirven para dos
propositos: operar contra Postgres en produccion y contra SQLite en memoria
durante las pruebas.

Decision central de esta etapa: el aislamiento entre empresas se declara con
llaves foraneas COMPUESTAS que incluyen empresa_id. Una variante no apunta solo
a un producto, apunta al par (empresa, producto). Un movimiento no apunta solo a
una variante, apunta al par (empresa, variante). Si una consulta olvidara
filtrar por empresa, la base de datos igual impediria mezclar datos de dos
marcas. El aislamiento deja de depender de que el programador se acuerde.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base declarativa comun a todos los modelos."""


def nuevo_id() -> uuid.UUID:
    """Genera el identificador en Python, no en la base de datos.

    Asi el mismo modelo funciona en Postgres y en SQLite sin depender de
    gen_random_uuid(), que solo existe en Postgres.
    """
    return uuid.uuid4()


def ahora() -> datetime:
    """Marca de tiempo con zona horaria explicita."""
    return datetime.now(UTC)


# Tipos de movimiento permitidos y el signo que aplica cada uno al stock.
# Una sola fuente de verdad: el motor de movimientos de la Etapa 4 leera de aqui.
SIGNO_POR_TIPO: dict[str, int] = {
    "entrada": +1,
    "ajuste_positivo": +1,
    "salida": -1,
    "ajuste_negativo": -1,
}


class Empresa(Base):
    """La unidad de aislamiento. Toda la informacion cuelga de una empresa."""

    __tablename__ = "empresas"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=nuevo_id)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)

    propietario: Mapped[Propietario | None] = relationship(back_populates="empresa")
    productos: Mapped[list[Producto]] = relationship(back_populates="empresa")


class Propietario(Base):
    """Cuenta unica que administra una empresa.

    El unique en empresa_id ES la regla "un solo propietario por empresa" del
    MVP. No es un comentario en la documentacion: la base la hace cumplir.
    """

    __tablename__ = "propietarios"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=nuevo_id)
    empresa_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("empresas.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    correo: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    hash_password: Mapped[str] = mapped_column(Text, nullable=False)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)

    empresa: Mapped[Empresa] = relationship(back_populates="propietario")


class Categoria(Base):
    """Agrupacion de productos, unica por nombre dentro de cada empresa."""

    __tablename__ = "categorias"
    __table_args__ = (UniqueConstraint("empresa_id", "nombre"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=nuevo_id)
    empresa_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(Text, nullable=False)


class Producto(Base):
    """Articulo simple o base de un conjunto de variantes."""

    __tablename__ = "productos"
    __table_args__ = (
        CheckConstraint("costo >= 0", name="costo_no_negativo"),
        CheckConstraint("minimo >= 0", name="minimo_no_negativo"),
        # Permite que otras tablas apunten al par (empresa, producto).
        UniqueConstraint("empresa_id", "id", name="uq_producto_por_empresa"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=nuevo_id)
    empresa_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False
    )
    categoria_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("categorias.id", ondelete="SET NULL")
    )
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    unidad: Mapped[str] = mapped_column(String(32), nullable=False, default="pieza")
    costo: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    minimo: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    es_variable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)

    empresa: Mapped[Empresa] = relationship(back_populates="productos")
    atributos: Mapped[list[Atributo]] = relationship(back_populates="producto")
    variantes: Mapped[list[Variante]] = relationship(back_populates="producto")


class Atributo(Base):
    """Dimension personalizable de un producto (Talla, Color, Sabor...)."""

    __tablename__ = "atributos"
    __table_args__ = (UniqueConstraint("producto_id", "nombre"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=nuevo_id)
    producto_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("productos.id", ondelete="CASCADE"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(Text, nullable=False)

    producto: Mapped[Producto] = relationship(back_populates="atributos")
    valores: Mapped[list[ValorAtributo]] = relationship(back_populates="atributo")


class ValorAtributo(Base):
    """Un valor concreto de un atributo (S, M, L / negro, blanco).

    El producto cartesiano de estos valores genera las variantes en la Etapa 3.
    """

    __tablename__ = "valores_atributo"
    __table_args__ = (UniqueConstraint("atributo_id", "valor"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=nuevo_id)
    atributo_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("atributos.id", ondelete="CASCADE"), nullable=False
    )
    valor: Mapped[str] = mapped_column(Text, nullable=False)

    atributo: Mapped[Atributo] = relationship(back_populates="valores")


class Variante(Base):
    """La unidad que realmente tiene existencias.

    Un producto simple tiene exactamente una variante base; un producto variable
    tiene una por combinacion de valores.
    """

    __tablename__ = "variantes"
    __table_args__ = (
        # SKU unico dentro de la empresa, no en todo el sistema: dos marcas
        # pueden usar el mismo SKU sin estorbarse.
        UniqueConstraint("empresa_id", "sku", name="uq_sku_por_empresa"),
        UniqueConstraint("empresa_id", "id", name="uq_variante_por_empresa"),
        # Aislamiento estructural: variante y producto comparten empresa.
        ForeignKeyConstraint(
            ["empresa_id", "producto_id"],
            ["productos.empresa_id", "productos.id"],
            ondelete="CASCADE",
            name="fk_variante_producto_misma_empresa",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=nuevo_id)
    empresa_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    producto_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    activa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)

    producto: Mapped[Producto] = relationship(back_populates="variantes")
    movimientos: Mapped[list[Movimiento]] = relationship(back_populates="variante")


class VarianteValor(Base):
    """Enlace que dice de que valores esta compuesta una variante."""

    __tablename__ = "variante_valores"

    variante_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("variantes.id", ondelete="CASCADE"), primary_key=True
    )
    valor_atributo_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("valores_atributo.id", ondelete="CASCADE"), primary_key=True
    )


class Movimiento(Base):
    """Registro inmutable de un cambio de inventario.

    Las tres restricciones de abajo son el corazon de la trazabilidad de Stoqo:
    la cantidad siempre es positiva, el delta debe coincidir con el tipo, y el
    stock posterior debe ser exactamente el anterior mas el delta. Con eso, un
    historial jamas puede contradecirse a si mismo.
    """

    __tablename__ = "movimientos"
    __table_args__ = (
        CheckConstraint("cantidad > 0", name="cantidad_positiva"),
        CheckConstraint(
            "(tipo IN ('entrada', 'ajuste_positivo') AND delta = cantidad) "
            "OR (tipo IN ('salida', 'ajuste_negativo') AND delta = -cantidad)",
            name="delta_coherente_con_tipo",
        ),
        CheckConstraint(
            "stock_posterior = stock_anterior + delta",
            name="stock_aritmetica_valida",
        ),
        CheckConstraint(
            "es_incidencia = 0 OR motivo IS NOT NULL",
            name="incidencia_requiere_motivo",
        ),
        ForeignKeyConstraint(
            ["empresa_id", "variante_id"],
            ["variantes.empresa_id", "variantes.id"],
            ondelete="CASCADE",
            name="fk_movimiento_variante_misma_empresa",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=nuevo_id)
    empresa_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    variante_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    tipo: Mapped[str] = mapped_column(String(24), nullable=False)
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_anterior: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_posterior: Mapped[int] = mapped_column(Integer, nullable=False)

    es_incidencia: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    motivo: Mapped[str | None] = mapped_column(Text)
    cancelado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Cada original admite como maximo una compensacion: el unique lo garantiza.
    compensa_a: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("movimientos.id", ondelete="RESTRICT"), unique=True
    )

    registrado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora
    )

    variante: Mapped[Variante] = relationship(back_populates="movimientos")


class Incidencia(Base):
    """Evidencia de una excepcion de stock negativo confirmada por el usuario."""

    __tablename__ = "incidencias"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=nuevo_id)
    empresa_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False
    )
    movimiento_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("movimientos.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    motivo: Mapped[str] = mapped_column(Text, nullable=False)
    confirmada: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)
