# BD propia del gestor

SIRT (PostgreSQL) es **solo consulta**: canales, cavas, salidas y programación.
Esta BD guarda la **capa operativa** del gestor (lo que antes estaba en JSON / SQLite).

Foto del día = consulta SIRT + overrides de esta BD (`override_pieza`).
Los overrides están preparados y **aún no se aplican** en las consultas.

## Dónde está (este servidor)

MySQL 8 local, base **`gestor_canales`** (no mezclar con SIRT ni con `colbeef_gestor` de Vísceras).

En MySQL Workbench: conexión `root` @ `127.0.0.1:3306` → schema `gestor_canales`.

Variable de entorno (en `.env`, no se sube a git):

```env
GESTOR_DB_URL=mysql://USER:PASSWORD@127.0.0.1:3306/gestor_canales
```

Si no hay MySQL, se puede usar SQLite:

```env
GESTOR_DB_URL=sqlite:///local_data/gestor_canales.db
```

## Arranque

Al importar `apps_script_local` se crea el schema. Si MySQL está vacío y existe `local_data/gestor_canales.db`, copia SQLite → MySQL. Si tampoco hay SQLite y existe el JSON, lo importa y lo renombra a `.bak`.

A mano (servidor detenido):

```bat
venv\Scripts\python.exe migrate_json_to_db.py
```

Luego `restart.bat`.

## Backup

- SQLite anterior: `local_data/gestor_canales.db`
- JSON: `local_data/canales_state.json.bak`
- MySQL: dump de `gestor_canales` (Workbench o `mysqldump gestor_canales`)
- `local_data/` y `.env` no se suben a git

## Qué persiste aquí (no en SIRT)

| Dato | Tabla |
|------|--------|
| OPL por propietario (Despacho) | `opl_propietario` |
| OPL por propietario (Beneficio) | `beneficio_opl_propietario` |
| OPLs particulares (Excel) | `opl_particular` |
| Meta congelada fecha+turno | `asignado_congelado` |
| Etiquetas impresas | `etiqueta_impresa` |
| Rollo 2/4 y operación finalizada | `app_setting` |
| Adicionales del día | `adicionales_dia` |
| Progreso OPL | `opl_progreso_snapshot` |
| Histórico de cierres | `historico_operacion` |
| Overrides futuros sobre SIRT | `override_pieza` |

No se escribe en tablas SIRT (`trazabilidad_proceso`, `organizaciones`, etc.).
