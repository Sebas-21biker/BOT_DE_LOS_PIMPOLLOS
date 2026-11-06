-- Fase B: memoria. El perfil pasa a ser "recuerdos" (no se pierde nada: se renombra y se le agregan columnas).
ALTER TABLE perfil RENAME TO recuerdos;

ALTER TABLE recuerdos
    ADD COLUMN confianza     REAL NOT NULL DEFAULT 1 CHECK (confianza BETWEEN 0 AND 1),
    -- dicho: lo contaron como dato; pregunta: respuesta a la pregunta pimpolla; grupo: se dijo en la charla
    -- del grupo y la persona confirmó; inferido: el bot lo dedujo y la persona confirmó.
    ADD COLUMN origen        TEXT NOT NULL DEFAULT 'dicho' CHECK (origen IN ('dicho', 'pregunta', 'grupo', 'inferido')),
    -- todos: lo ven los dos; autor: solo quien lo anotó (ideas de regalo sorpresa, "no le digas").
    ADD COLUMN visibilidad   TEXT NOT NULL DEFAULT 'todos' CHECK (visibilidad IN ('todos', 'autor')),
    ADD COLUMN vigente_hasta DATE,                       -- datos temporales ("este mes estoy a dieta")
    ADD COLUMN busqueda      TSVECTOR GENERATED ALWAYS AS (to_tsvector('spanish', categoria || ' ' || dato)) STORED;

-- Las ideas de regalo que una persona anotó sobre la otra ya eran secretas: ahora queda explícito.
UPDATE recuerdos SET visibilidad = 'autor'
WHERE categoria = 'regalos' AND usuario_id IS NOT NULL AND creado_por IS NOT NULL AND usuario_id <> creado_por;

CREATE INDEX recuerdos_busqueda ON recuerdos USING GIN (busqueda);

-- Compatibilidad mientras Railway cambia de versión (el bot viejo sigue usando "perfil" unos segundos).
CREATE VIEW perfil AS SELECT id, usuario_id, categoria, dato, creado_por, creado_en FROM recuerdos;

-- Datos que Pimpollo escuchó en el grupo y le pregunta a la persona antes de guardar.
ALTER TABLE intervenciones DROP CONSTRAINT intervenciones_tipo_check;
ALTER TABLE intervenciones ADD CONSTRAINT intervenciones_tipo_check CHECK (tipo IN ('grupo', 'sorpresa', 'dato'));

-- Planes concretos con fecha ("el sábado vamos al asado de Juan"). Los panoramas son ideas sin fecha.
CREATE TABLE planes (
    id               SERIAL PRIMARY KEY,
    titulo           TEXT NOT NULL,
    fecha            DATE NOT NULL,
    hora             TIME,
    lugar            TEXT,
    con_quien        TEXT,
    estado           TEXT NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'hecho', 'cancelado')),
    creado_por       INTEGER REFERENCES usuarios (id),
    mensaje_original TEXT,
    creado_en        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX planes_fecha ON planes (fecha) WHERE estado = 'pendiente';
