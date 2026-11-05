-- BD propia del gestor de canales (NO SIRT).
-- Motor: SQLite (local_data/gestor_canales.db) o futuro PostgreSQL schema gestor_canales.
-- SIRT permanece solo consulta. Foto del día = SIRT + override_pieza (esta BD).

CREATE TABLE IF NOT EXISTS schema_meta (
    clave TEXT PRIMARY KEY,
    valor TEXT
);

CREATE TABLE IF NOT EXISTS opl_propietario (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    propietario TEXT NOT NULL UNIQUE,
    opl TEXT NOT NULL,
    extra INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS opl_particular (
    opl TEXT PRIMARY KEY,
    orden INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS asignado_congelado (
    fecha TEXT NOT NULL,
    turno TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (fecha, turno)
);

CREATE TABLE IF NOT EXISTS etiqueta_impresa (
    fecha TEXT NOT NULL,
    codigo TEXT NOT NULL,
    impresa_en TEXT NOT NULL,
    PRIMARY KEY (fecha, codigo)
);
CREATE INDEX IF NOT EXISTS idx_etiqueta_impresa_fecha ON etiqueta_impresa(fecha);

CREATE TABLE IF NOT EXISTS app_setting (
    clave TEXT PRIMARY KEY,
    valor TEXT
);

CREATE TABLE IF NOT EXISTS adicionales_dia (
    fecha TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS opl_progreso_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guardado_en TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS historico_operacion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Overrides futuros sobre la consulta SIRT (no se escribe SIRT).
-- Campos típicos: excluir_del_dia, forzar_estado, nota.
CREATE TABLE IF NOT EXISTS override_pieza (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    codigo TEXT NOT NULL,
    campo TEXT NOT NULL,
    valor TEXT,
    usuario TEXT,
    motivo TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_override_pieza_fecha_codigo
    ON override_pieza(fecha, codigo);
