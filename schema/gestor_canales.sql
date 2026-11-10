-- BD propia del gestor de canales (NO SIRT).
-- Motor activo: MySQL 8 (schema gestor_canales).
-- SQLite (local_data/gestor_canales.db) queda como backup/migración.
-- SIRT permanece solo consulta. Foto del día = SIRT + override_pieza (esta BD).

CREATE DATABASE IF NOT EXISTS gestor_canales
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE gestor_canales;

CREATE TABLE IF NOT EXISTS schema_meta (
    clave VARCHAR(191) PRIMARY KEY,
    valor TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS opl_propietario (
    id INT AUTO_INCREMENT PRIMARY KEY,
    propietario VARCHAR(191) NOT NULL UNIQUE,
    opl VARCHAR(191) NOT NULL,
    extra INT NOT NULL DEFAULT 0,
    updated_at VARCHAR(32) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS beneficio_opl_propietario (
    id INT AUTO_INCREMENT PRIMARY KEY,
    propietario VARCHAR(191) NOT NULL UNIQUE,
    opl VARCHAR(191) NOT NULL,
    extra INT NOT NULL DEFAULT 0,
    updated_at VARCHAR(32) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS opl_particular (
    opl VARCHAR(191) PRIMARY KEY,
    orden INT NOT NULL,
    created_at VARCHAR(32) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS asignado_congelado (
    fecha VARCHAR(16) NOT NULL,
    turno VARCHAR(32) NOT NULL,
    payload LONGTEXT NOT NULL,
    updated_at VARCHAR(32) NOT NULL,
    PRIMARY KEY (fecha, turno)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS etiqueta_impresa (
    fecha VARCHAR(16) NOT NULL,
    codigo VARCHAR(64) NOT NULL,
    impresa_en VARCHAR(64) NOT NULL,
    PRIMARY KEY (fecha, codigo),
    INDEX idx_etiqueta_impresa_fecha (fecha)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS app_setting (
    clave VARCHAR(191) PRIMARY KEY,
    valor TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS adicionales_dia (
    fecha VARCHAR(16) PRIMARY KEY,
    payload LONGTEXT NOT NULL,
    updated_at VARCHAR(32) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS opl_progreso_snapshot (
    id INT AUTO_INCREMENT PRIMARY KEY,
    guardado_en VARCHAR(32) NOT NULL,
    payload LONGTEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS historico_operacion (
    id INT AUTO_INCREMENT PRIMARY KEY,
    fecha VARCHAR(64),
    payload LONGTEXT NOT NULL,
    created_at VARCHAR(32) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS override_pieza (
    id INT AUTO_INCREMENT PRIMARY KEY,
    fecha VARCHAR(16) NOT NULL,
    codigo VARCHAR(64) NOT NULL,
    campo VARCHAR(64) NOT NULL,
    valor TEXT,
    usuario VARCHAR(128),
    motivo TEXT,
    created_at VARCHAR(32) NOT NULL,
    INDEX idx_override_pieza_fecha_codigo (fecha, codigo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
