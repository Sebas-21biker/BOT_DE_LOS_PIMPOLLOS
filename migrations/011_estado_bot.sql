-- Estado conversacional del bot (propuestas por confirmar, respuestas que espera, ideas recientes...).
-- Antes vivía solo en memoria y se perdía con cada despliegue de Railway.
CREATE TABLE estado_bot (
    clave          TEXT PRIMARY KEY,        -- 'bot' o 'user:<telegram_id>'
    datos          BYTEA NOT NULL,          -- pickle de los dicts de python-telegram-bot
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
