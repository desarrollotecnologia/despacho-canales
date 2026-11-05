# BD propia del gestor

SIRT (PostgreSQL) es **solo consulta**: canales, cavas, salidas y programación.
Esta BD guarda la **capa operativa** del gestor (lo que antes estaba en `canales_state.json`).

Foto del día = consulta SIRT + overrides de esta BD (`override_pieza`).
Los overrides están preparados y **aún no se aplican** en las consultas.

## Dónde está

Por defecto: `local_data/gestor_canales.db` (SQLite, un archivo, sin servicio extra).

Variable de entorno:

```env
GESTOR_DB_URL=sqlite:///local_data/gestor_canales.db
```

PostgreSQL (`postgresql://...`) queda documentado para una migración futura; hoy el motor activo es SQLite.

## Arranque

Al importar `apps_script_local` se crea el schema y, si existe `local_data/canales_state.json` y la BD está vacía, se importa todo en una transacción y el JSON se renombra a `canales_state.json.bak`.

También se puede forzar a mano (con el servidor detenido):

```bat
venv\Scripts\python.exe migrate_json_to_db.py
```

Luego `restart.bat`.

## Backup

- Tras migrar: `local_data/canales_state.json.bak`
- La BD SQLite: copiar `local_data/gestor_canales.db` (y si existen, `-wal` / `-shm`)
- `local_data/` no se sube a git

## Qué persiste aquí (no en SIRT)

| Dato | Tabla |
|------|--------|
| OPL por propietario | `opl_propietario` |
| OPLs particulares (Excel) | `opl_particular` |
| Meta congelada fecha+turno | `asignado_congelado` |
| Etiquetas impresas | `etiqueta_impresa` |
| Rollo 2/4 y operación finalizada | `app_setting` |
| Adicionales del día | `adicionales_dia` |
| Progreso OPL | `opl_progreso_snapshot` |
| Histórico de cierres | `historico_operacion` |
| Overrides futuros sobre SIRT | `override_pieza` |

## Concurrencia

SQLite con WAL, `busy_timeout=8000` y lock de proceso. Escrituras en transacción.

No se escribe en tablas SIRT (`trazabilidad_proceso`, `organizaciones`, etc.).
