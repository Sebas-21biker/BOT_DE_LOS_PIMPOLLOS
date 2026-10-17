-- Esquema inicial: finanzas en pareja.
-- Moneda base: CLP. El saldo entre usuarios NO se guarda, se calcula.

CREATE DOMAIN moneda AS CHAR(3)
    CHECK (VALUE IN ('CLP', 'USD', 'MXN'));

CREATE TABLE usuarios (
    id               SERIAL PRIMARY KEY,
    nombre           TEXT NOT NULL,
    telegram_id      BIGINT NOT NULL UNIQUE,
    moneda_preferida moneda NOT NULL DEFAULT 'CLP'
);

CREATE TABLE categorias (
    id                 SERIAL PRIMARY KEY,
    nombre             TEXT NOT NULL,
    tipo               TEXT NOT NULL CHECK (tipo IN ('gasto', 'ingreso')),
    emoji              TEXT,
    categoria_padre_id INTEGER REFERENCES categorias (id),
    UNIQUE (nombre, tipo)
);

CREATE TABLE movimientos (
    id                SERIAL PRIMARY KEY,
    tipo              TEXT NOT NULL CHECK (tipo IN ('gasto', 'ingreso')),
    monto             NUMERIC(14, 2) NOT NULL CHECK (monto > 0),
    moneda            moneda NOT NULL,
    monto_base        NUMERIC(14, 2) NOT NULL,          -- monto convertido a CLP
    tipo_cambio_usado NUMERIC(18, 6) NOT NULL DEFAULT 1, -- 1 unidad de `moneda` en CLP
    categoria_id      INTEGER REFERENCES categorias (id),
    pagado_por        INTEGER NOT NULL REFERENCES usuarios (id),
    fecha             DATE NOT NULL DEFAULT CURRENT_DATE,
    descripcion       TEXT,
    es_compartido     BOOLEAN NOT NULL DEFAULT FALSE,
    mensaje_original  TEXT,
    creado_en         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_movimientos_fecha ON movimientos (fecha);
CREATE INDEX idx_movimientos_categoria ON movimientos (categoria_id);

-- Cómo se reparte un movimiento compartido entre los usuarios.
CREATE TABLE repartos (
    movimiento_id INTEGER NOT NULL REFERENCES movimientos (id) ON DELETE CASCADE,
    usuario_id    INTEGER NOT NULL REFERENCES usuarios (id),
    porcentaje    NUMERIC(5, 2) NOT NULL CHECK (porcentaje >= 0 AND porcentaje <= 100),
    PRIMARY KEY (movimiento_id, usuario_id)
);

-- Pagos entre ellos para saldar deudas.
CREATE TABLE liquidaciones (
    id                SERIAL PRIMARY KEY,
    de_usuario        INTEGER NOT NULL REFERENCES usuarios (id),
    a_usuario         INTEGER NOT NULL REFERENCES usuarios (id),
    monto             NUMERIC(14, 2) NOT NULL CHECK (monto > 0),
    moneda            moneda NOT NULL,
    monto_base        NUMERIC(14, 2) NOT NULL,
    tipo_cambio_usado NUMERIC(18, 6) NOT NULL DEFAULT 1,
    fecha             DATE NOT NULL DEFAULT CURRENT_DATE,
    creado_en         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (de_usuario <> a_usuario)
);

CREATE TABLE tipos_cambio (
    fecha          DATE NOT NULL,
    moneda_origen  moneda NOT NULL,
    moneda_destino moneda NOT NULL,
    valor          NUMERIC(18, 6) NOT NULL CHECK (valor > 0),
    PRIMARY KEY (fecha, moneda_origen, moneda_destino)
);

CREATE TABLE presupuestos (
    id                   SERIAL PRIMARY KEY,
    categoria_id         INTEGER NOT NULL REFERENCES categorias (id),
    monto                NUMERIC(14, 2) NOT NULL CHECK (monto > 0),
    moneda               moneda NOT NULL DEFAULT 'CLP',
    mes                  DATE NOT NULL CHECK (EXTRACT(DAY FROM mes) = 1), -- primer día del mes
    alerta_al_porcentaje SMALLINT NOT NULL DEFAULT 80 CHECK (alerta_al_porcentaje BETWEEN 1 AND 100),
    UNIQUE (categoria_id, mes)
);

CREATE TABLE recurrentes (
    id           SERIAL PRIMARY KEY,
    descripcion  TEXT NOT NULL,
    monto        NUMERIC(14, 2) NOT NULL CHECK (monto > 0),
    moneda       moneda NOT NULL DEFAULT 'CLP',
    categoria_id INTEGER REFERENCES categorias (id),
    frecuencia   TEXT NOT NULL DEFAULT 'mensual' CHECK (frecuencia IN ('semanal', 'mensual', 'anual')),
    dia_del_mes  SMALLINT CHECK (dia_del_mes BETWEEN 1 AND 31),
    activo       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE metas_ahorro (
    id             SERIAL PRIMARY KEY,
    nombre         TEXT NOT NULL,
    monto_objetivo NUMERIC(14, 2) NOT NULL CHECK (monto_objetivo > 0),
    moneda         moneda NOT NULL DEFAULT 'CLP',
    fecha_objetivo DATE,
    monto_actual   NUMERIC(14, 2) NOT NULL DEFAULT 0
);
