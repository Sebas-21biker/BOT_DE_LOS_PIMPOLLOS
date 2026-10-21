-- Registro de cada llamada a Claude (tokens) para saber cuánto cuesta el bot (/costo).
CREATE TABLE uso_ia (
    id               SERIAL PRIMARY KEY,
    creado_en        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    modelo           TEXT NOT NULL,
    motivo           TEXT NOT NULL,          -- mensaje, correo, atajo, foto, menu, ideas
    entrada          INTEGER NOT NULL,
    cache_escritura  INTEGER NOT NULL DEFAULT 0,
    cache_lectura    INTEGER NOT NULL DEFAULT 0,
    salida           INTEGER NOT NULL
);
CREATE INDEX idx_uso_ia_fecha ON uso_ia (creado_en);
