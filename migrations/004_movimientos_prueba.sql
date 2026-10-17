-- Movimientos de prueba: se guardan pero no cuentan en saldos ni resúmenes.
ALTER TABLE movimientos ADD COLUMN es_prueba BOOLEAN NOT NULL DEFAULT FALSE;
