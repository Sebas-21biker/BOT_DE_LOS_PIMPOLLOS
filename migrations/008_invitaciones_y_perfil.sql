-- Invitaciones de un solo uso para registrar a la pareja con un link.
CREATE TABLE invitaciones (
    codigo     TEXT PRIMARY KEY,
    creado_por INTEGER NOT NULL REFERENCES usuarios (id),
    creado_en  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expira_en  TIMESTAMPTZ NOT NULL,
    usado_por  INTEGER REFERENCES usuarios (id),
    usado_en   TIMESTAMPTZ
);

-- Perfil de pareja: gustos, sueños, horarios, amigos... usuario_id NULL = de los dos.
CREATE TABLE perfil (
    id         SERIAL PRIMARY KEY,
    usuario_id INTEGER REFERENCES usuarios (id),
    categoria  TEXT NOT NULL
               CHECK (categoria IN ('gustos', 'comida', 'suenos', 'trabajo', 'amigos', 'deportes',
                                    'regalos', 'musica', 'otro')),
    dato       TEXT NOT NULL,
    creado_por INTEGER REFERENCES usuarios (id),
    creado_en  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
