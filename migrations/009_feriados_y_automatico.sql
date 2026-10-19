-- Feriados legales de Chile (se descargan una vez por año desde date.nager.at).
CREATE TABLE feriados (
    fecha  DATE PRIMARY KEY,
    nombre TEXT NOT NULL
);

-- Token secreto por usuario para registrar pagos automáticos (atajo del celular, notificaciones).
ALTER TABLE usuarios ADD COLUMN token_auto TEXT UNIQUE;
