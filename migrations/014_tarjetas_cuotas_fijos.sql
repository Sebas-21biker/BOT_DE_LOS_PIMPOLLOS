-- Compromisos mensuales: tarjetas de crédito (día de facturación y de pago), compras en cuotas y fijos (sueldo, arriendo).

CREATE TABLE tarjetas (
    id              SERIAL PRIMARY KEY,
    usuario_id      INTEGER NOT NULL REFERENCES usuarios (id) ON DELETE CASCADE,
    nombre          TEXT NOT NULL,
    dia_facturacion SMALLINT NOT NULL CHECK (dia_facturacion BETWEEN 1 AND 31),
    dia_vencimiento SMALLINT CHECK (dia_vencimiento BETWEEN 1 AND 31),
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX tarjetas_usuario_nombre ON tarjetas (usuario_id, lower(nombre));

-- Una compra en cuotas: cada cuota se anota como movimiento propio el día que toca (no se guardan cuotas futuras).
CREATE TABLE planes_cuotas (
    id                 SERIAL PRIMARY KEY,
    usuario_id         INTEGER NOT NULL REFERENCES usuarios (id),
    tarjeta_id         INTEGER REFERENCES tarjetas (id) ON DELETE SET NULL,
    descripcion        TEXT,
    categoria_id       INTEGER REFERENCES categorias (id),
    monto_total        NUMERIC(14, 2) NOT NULL CHECK (monto_total > 0),
    moneda             moneda NOT NULL DEFAULT 'CLP',
    n_cuotas           SMALLINT NOT NULL CHECK (n_cuotas BETWEEN 2 AND 60),
    cuotas_registradas SMALLINT NOT NULL DEFAULT 0,
    fecha_compra       DATE NOT NULL,
    primera_fecha      DATE NOT NULL,
    dia_cobro          SMALLINT NOT NULL CHECK (dia_cobro BETWEEN 1 AND 31),
    porcentaje_pagador NUMERIC(5, 2),            -- NULL = individual
    mensaje_original   TEXT,
    es_prueba          BOOLEAN NOT NULL DEFAULT FALSE,
    activo             BOOLEAN NOT NULL DEFAULT TRUE,   -- FALSE = se dejó de anotar (ej: la prepagó)
    creado_en          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Borrar un plan (Deshacer) borra las cuotas que ya se anotaron; cancelarlo (activo = FALSE) las conserva.
ALTER TABLE movimientos ADD COLUMN plan_cuotas_id INTEGER REFERENCES planes_cuotas (id) ON DELETE CASCADE;

-- La tabla recurrentes existía desde la 001 sin uso: ahora son los "fijos" de cada persona.
ALTER TABLE recurrentes
    ADD COLUMN tipo               TEXT NOT NULL DEFAULT 'gasto' CHECK (tipo IN ('gasto', 'ingreso')),
    ADD COLUMN usuario_id         INTEGER REFERENCES usuarios (id) ON DELETE CASCADE,
    ADD COLUMN porcentaje_pagador NUMERIC(5, 2),
    ADD COLUMN mensaje_original   TEXT,
    ADD COLUMN creado_en          TIMESTAMPTZ NOT NULL DEFAULT NOW();
