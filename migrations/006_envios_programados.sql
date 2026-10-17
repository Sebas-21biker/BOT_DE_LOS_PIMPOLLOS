-- Registro de mensajes automáticos ya enviados (resumen semanal, etc.) para no repetirlos
-- si el bot se reinicia.
CREATE TABLE envios_programados (
    clave      TEXT PRIMARY KEY,
    enviado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
