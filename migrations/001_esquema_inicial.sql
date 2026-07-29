-- =============================================================================
-- Stoqo - Esquema inicial (Etapa 1)
--
-- Se ejecuta una sola vez en Supabase: SQL Editor > New query > Run.
--
-- Principio de esta migracion: el aislamiento entre empresas no se confia al
-- codigo de Python. Se declara en la base de datos mediante llaves foraneas
-- compuestas que incluyen empresa_id. Si un dia una consulta olvida filtrar,
-- la base de datos sigue impidiendo que un producto de una marca reciba un
-- movimiento de otra.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. Empresa: la unidad de aislamiento. Todo cuelga de aqui.
-- -----------------------------------------------------------------------------
create table empresas (
    id          uuid primary key default gen_random_uuid(),
    nombre      text not null,
    creada_en   timestamptz not null default now()
);


-- -----------------------------------------------------------------------------
-- 2. Propietario: una sola cuenta administradora por empresa.
--    El unique en empresa_id es la regla "un solo propietario" del MVP,
--    declarada en la base y no en un comentario.
-- -----------------------------------------------------------------------------
create table propietarios (
    id              uuid primary key default gen_random_uuid(),
    empresa_id      uuid not null unique references empresas(id) on delete cascade,
    correo          text not null unique,
    hash_password   text not null,
    creado_en       timestamptz not null default now()
);


-- -----------------------------------------------------------------------------
-- 3. Categoria: unica por nombre dentro de cada empresa, no globalmente.
--    Dos marcas distintas pueden tener ambas la categoria "Playeras".
-- -----------------------------------------------------------------------------
create table categorias (
    id          uuid primary key default gen_random_uuid(),
    empresa_id  uuid not null references empresas(id) on delete cascade,
    nombre      text not null,
    unique (empresa_id, nombre)
);


-- -----------------------------------------------------------------------------
-- 4. Producto: articulo simple o base de variantes.
-- -----------------------------------------------------------------------------
create table productos (
    id           uuid primary key default gen_random_uuid(),
    empresa_id   uuid not null references empresas(id) on delete cascade,
    categoria_id uuid references categorias(id) on delete set null,
    nombre       text not null,
    unidad       text not null default 'pieza',
    costo        numeric(12,2) not null default 0 check (costo >= 0),
    minimo       integer not null default 0 check (minimo >= 0),
    es_variable  boolean not null default false,
    activo       boolean not null default true,
    creado_en    timestamptz not null default now(),

    -- Necesario para que otras tablas puedan apuntar al par (empresa, producto)
    -- y no solo al producto. Es la base del aislamiento estructural.
    unique (empresa_id, id)
);


-- -----------------------------------------------------------------------------
-- 5. Atributo y sus valores: las dimensiones personalizables de un producto
--    (ej. Talla, Color) y los valores de cada una (S, M, L / negro, blanco).
--    De aqui sale el producto cartesiano que genera las variantes.
-- -----------------------------------------------------------------------------
create table atributos (
    id           uuid primary key default gen_random_uuid(),
    producto_id  uuid not null references productos(id) on delete cascade,
    nombre       text not null,
    unique (producto_id, nombre)
);

create table valores_atributo (
    id           uuid primary key default gen_random_uuid(),
    atributo_id  uuid not null references atributos(id) on delete cascade,
    valor        text not null,
    unique (atributo_id, valor)
);


-- -----------------------------------------------------------------------------
-- 6. Variante: la unidad que realmente tiene existencias.
--    Un producto simple tiene exactamente una variante base.
-- -----------------------------------------------------------------------------
create table variantes (
    id          uuid primary key default gen_random_uuid(),
    empresa_id  uuid not null references empresas(id) on delete cascade,
    producto_id uuid not null,
    sku         text not null,
    stock       integer not null default 0,
    activa      boolean not null default true,
    creada_en   timestamptz not null default now(),

    -- SKU unico dentro de la empresa, no en todo el sistema.
    unique (empresa_id, sku),

    -- Llave foranea compuesta: la variante y su producto DEBEN pertenecer a la
    -- misma empresa. Postgres rechaza cualquier intento de mezclarlas.
    foreign key (empresa_id, producto_id)
        references productos (empresa_id, id) on delete cascade,

    unique (empresa_id, id)
);

-- Que combinacion de valores representa cada variante (talla M + color negro).
create table variante_valores (
    variante_id       uuid not null references variantes(id) on delete cascade,
    valor_atributo_id uuid not null references valores_atributo(id) on delete cascade,
    primary key (variante_id, valor_atributo_id)
);


-- -----------------------------------------------------------------------------
-- 7. Movimiento: registro inmutable de cada cambio de inventario.
--
--    Reglas declaradas aqui, no solo en Python:
--    - cantidad siempre positiva; el signo lo lleva el delta
--    - stock_posterior = stock_anterior + delta (integridad aritmetica)
--    - un movimiento original puede tener como maximo una compensacion
-- -----------------------------------------------------------------------------
create table movimientos (
    id             uuid primary key default gen_random_uuid(),
    empresa_id     uuid not null references empresas(id) on delete cascade,
    variante_id    uuid not null,

    tipo           text not null check (tipo in (
                       'entrada', 'salida', 'ajuste_positivo', 'ajuste_negativo'
                   )),
    cantidad       integer not null check (cantidad > 0),
    delta          integer not null,
    stock_anterior integer not null,
    stock_posterior integer not null,

    es_incidencia  boolean not null default false,
    motivo         text,
    cancelado      boolean not null default false,

    -- Si este movimiento compensa a otro, aqui vive el enlace.
    compensa_a     uuid unique references movimientos(id) on delete restrict,

    registrado_en  timestamptz not null default now(),

    -- El delta debe coincidir con el tipo y la cantidad.
    constraint delta_coherente_con_tipo check (
        (tipo in ('entrada', 'ajuste_positivo') and delta = cantidad)
        or
        (tipo in ('salida', 'ajuste_negativo') and delta = -cantidad)
    ),

    -- Integridad aritmetica: el historial nunca puede contradecirse.
    constraint stock_aritmetica_valida check (
        stock_posterior = stock_anterior + delta
    ),

    -- Una incidencia siempre trae motivo.
    constraint incidencia_requiere_motivo check (
        es_incidencia = false or motivo is not null
    ),

    -- Mismo aislamiento estructural: el movimiento y su variante comparten empresa.
    foreign key (empresa_id, variante_id)
        references variantes (empresa_id, id) on delete cascade
);


-- -----------------------------------------------------------------------------
-- Indices: las consultas del dashboard y los reportes siempre filtran por
-- empresa y ordenan por fecha. Sin estos indices, cada metrica recorre la
-- tabla completa.
-- -----------------------------------------------------------------------------
create index idx_movimientos_empresa_fecha
    on movimientos (empresa_id, registrado_en desc);

create index idx_movimientos_variante
    on movimientos (variante_id, registrado_en desc);

create index idx_variantes_empresa
    on variantes (empresa_id, activa);

create index idx_productos_empresa
    on productos (empresa_id, activo);


-- -----------------------------------------------------------------------------
-- 8. Incidencia: evidencia de una excepcion de stock negativo confirmada.
-- -----------------------------------------------------------------------------
create table incidencias (
    id            uuid primary key default gen_random_uuid(),
    empresa_id    uuid not null references empresas(id) on delete cascade,
    movimiento_id uuid not null unique references movimientos(id) on delete cascade,
    motivo        text not null,
    confirmada    boolean not null default true,
    creada_en     timestamptz not null default now()
);
