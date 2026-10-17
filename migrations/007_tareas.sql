-- Tareas de la casa con frecuencia y turnos.
CREATE TABLE tareas (
    id               SERIAL PRIMARY KEY,
    nombre           TEXT NOT NULL,
    cada_dias        INTEGER NOT NULL CHECK (cada_dias > 0),
    responsable_id   INTEGER NOT NULL REFERENCES usuarios (id),
    rotar            BOOLEAN NOT NULL DEFAULT TRUE,   -- si se turnan entre los dos
    proxima          DATE NOT NULL,
    ultimo_hecho_por INTEGER REFERENCES usuarios (id),
    ultimo_hecho_en  TIMESTAMPTZ,
    activa           BOOLEAN NOT NULL DEFAULT TRUE,
    creado_por       INTEGER REFERENCES usuarios (id),
    creado_en        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
