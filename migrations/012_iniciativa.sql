-- Fase A: Pimpollo con iniciativa en el grupo.

-- Memoria corta de la charla del grupo (se borra a las 48 h). Solo mensajes de texto de la pareja.
CREATE TABLE mensajes_grupo (
    id         BIGSERIAL PRIMARY KEY,
    chat_id    BIGINT NOT NULL,
    usuario_id INT REFERENCES usuarios(id) ON DELETE CASCADE,
    texto      TEXT NOT NULL,
    al_bot     BOOLEAN NOT NULL DEFAULT FALSE,   -- le hablaban al bot o el bot ya lo procesó
    creado_en  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX mensajes_grupo_chat ON mensajes_grupo (chat_id, id);
CREATE INDEX mensajes_grupo_fecha ON mensajes_grupo (creado_en);

-- Preferencias de iniciativa por chat: el grupo, o el chat privado de cada uno (avisos de sorpresas).
CREATE TABLE iniciativa (
    chat_id        BIGINT PRIMARY KEY,
    nivel          TEXT NOT NULL DEFAULT 'baja' CHECK (nivel IN ('off', 'baja', 'media', 'alta')),
    ajuste         INT NOT NULL DEFAULT 0,          -- lo mueven los botones 👍 (-1) y 🤫 (+1)
    silencio_hasta TIMESTAMPTZ,                      -- "pimpollo, silencio por hoy"
    evaluado_hasta BIGINT NOT NULL DEFAULT 0          -- último mensajes_grupo.id ya evaluado
);

-- Cada vez que Pimpollo habló por iniciativa propia (para cooldown, límites y no repetir).
CREATE TABLE intervenciones (
    id         BIGSERIAL PRIMARY KEY,
    chat_id    BIGINT NOT NULL,
    tipo       TEXT NOT NULL CHECK (tipo IN ('grupo', 'sorpresa')),
    tema       TEXT NOT NULL DEFAULT '',
    utilidad   INT NOT NULL DEFAULT 0,
    texto      TEXT NOT NULL,
    reaccion   TEXT CHECK (reaccion IN ('util', 'silencio')),
    creado_en  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX intervenciones_chat ON intervenciones (chat_id, creado_en);
