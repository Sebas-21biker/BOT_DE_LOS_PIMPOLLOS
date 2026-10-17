-- Liquidaciones: modo prueba y mensaje original, igual que movimientos.
ALTER TABLE liquidaciones
    ADD COLUMN es_prueba BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN mensaje_original TEXT;

-- Metas: lo ahorrado NO se guarda; se calcula sumando aportes_meta.
ALTER TABLE metas_ahorro
    DROP COLUMN monto_actual,
    ADD COLUMN emoji TEXT,
    ADD COLUMN activa BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN creado_por INTEGER REFERENCES usuarios (id),
    ADD COLUMN creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE TABLE aportes_meta (
    id         SERIAL PRIMARY KEY,
    meta_id    INTEGER NOT NULL REFERENCES metas_ahorro (id) ON DELETE CASCADE,
    usuario_id INTEGER NOT NULL REFERENCES usuarios (id),
    monto      NUMERIC(14, 2) NOT NULL CHECK (monto > 0),
    fecha      DATE NOT NULL DEFAULT CURRENT_DATE,
    creado_en  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Lista de compras compartida.
CREATE TABLE items_compra (
    id           SERIAL PRIMARY KEY,
    nombre       TEXT NOT NULL,
    cantidad     TEXT,
    agregado_por INTEGER REFERENCES usuarios (id),
    comprado     BOOLEAN NOT NULL DEFAULT FALSE,
    creado_en    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Recordatorios. para_usuario_id NULL = para ambos.
CREATE TABLE recordatorios (
    id              SERIAL PRIMARY KEY,
    texto           TEXT NOT NULL,
    fecha_hora      TIMESTAMPTZ NOT NULL,
    repetir         TEXT NOT NULL DEFAULT 'nunca'
                    CHECK (repetir IN ('nunca', 'diario', 'semanal', 'mensual', 'anual')),
    para_usuario_id INTEGER REFERENCES usuarios (id),
    creado_por      INTEGER REFERENCES usuarios (id),
    enviado         BOOLEAN NOT NULL DEFAULT FALSE,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_recordatorios_pendientes ON recordatorios (fecha_hora) WHERE NOT enviado;

-- Panoramas pendientes (restaurantes, lugares, películas...).
CREATE TABLE panoramas (
    id           SERIAL PRIMARY KEY,
    titulo       TEXT NOT NULL,
    tipo         TEXT NOT NULL DEFAULT 'otro'
                 CHECK (tipo IN ('restaurante', 'lugar', 'pelicula', 'serie', 'actividad', 'viaje', 'otro')),
    notas        TEXT,
    agregado_por INTEGER REFERENCES usuarios (id),
    hecho        BOOLEAN NOT NULL DEFAULT FALSE,
    hecho_en     TIMESTAMPTZ,
    creado_en    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Datos de la casa (clave del wifi, tallas, contacto del arriendo...).
CREATE TABLE notas (
    id             SERIAL PRIMARY KEY,
    clave          TEXT NOT NULL UNIQUE,
    valor          TEXT NOT NULL,
    creado_por     INTEGER REFERENCES usuarios (id),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
