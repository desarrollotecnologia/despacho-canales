# Despacho de Canales · Colbeef v1.0

Control de medias canales: despachos y operadores logísticos (OPL) en tiempo real.

## Arranque rápido (Windows)

| Archivo | Acción |
|---------|--------|
| `setup.bat` | Primera vez: crea venv e instala dependencias |
| `start.bat` | Inicia el servidor en segundo plano |
| `stop.bat` | Detiene el servidor |
| `restart.bat` | Reinicia el backend |
| `status.bat` | Ver si está activo + últimas líneas del log |
| `start-console.bat` | Inicia con ventana visible (depuración) |

### URLs

- En este PC: http://localhost:8000
- Servidor de planta (205): http://192.168.20.205:8012/
- Desde la red (otro puerto): http://IP-DE-ESTE-PC:PUERTO

Log del servidor: `logs/server.log`

## Módulos

### 📋 Despachos
- Lista agrupada por propietario/cliente
- Columnas: Código, Propietario/Cliente, MC1, MC2, Total partes (cada media = 0.5)
- Filtro por turno (DxL, LxM, MxM, etc.)
- Indicadores: MC1, MC2, Total partes, Clientes
- Impresión de la lista principal

### 👷 Planilla OPL
- Tablero de operadores logísticos en tiempo real
- Barras de progreso: salidas pistoleadas vs pendientes
- Cada media canal = 0.5 → par completo = 1.0
- Clic en tarjeta OPL → lista completa con código+sufijo, cava, riel
- Descarga CSV individual por OPL

### 📌 Planilla de Puntos
- Lista por operador logístico (CAVA AJR, CAVA MIREYA, etc.)
- Columnas: Código, Propietario/Cliente, Cava, Riel
- Excel por OPL con encabezado `OPL CAVA AJR` para ubicar y despachar

### 🏭 Canales en Cava
- Inventario completo con código (sufijo MC1/MC2), propietario, cava, riel
- Buscador en tiempo real

## Configuración `.env`

```env
POSTGRES_HOST=10.64.1.47
POSTGRES_DB=sirt
POSTGRES_USER=acceso
POSTGRES_PASSWORD=...
POSTGRES_PORT=5432
APP_HOST=0.0.0.0
APP_PORT=8000
```

## Importante: IDs de tipo_parte_producto

El backend asume que las medias canales tienen los IDs:
- `ID_MC1 = 4` → Media Canal 1
- `ID_MC2 = 5` → Media Canal 2 Cola

**Si los IDs son diferentes en la BD**, ajústalos en `main.py` líneas 20-21.
Para verificar qué IDs corresponden, accede a: http://localhost:8000/api/tipos_canal

## Endpoints API

- `GET /api/ping` — verificar conexión BD
- `GET /api/tipos_canal` — detectar IDs de canales en la BD
- `GET /api/dashboard?fecha=YYYY-MM-DD` — resumen ejecutivo
- `GET /api/cavas?fecha=YYYY-MM-DD` — inventario en cava
- `GET /api/despachos?fecha=YYYY-MM-DD&turno=DxL` — agrupado por destino
- `GET /api/despachos/detalle?destino=X&fecha=YYYY-MM-DD` — lista individual
- `GET /api/opl?fecha=YYYY-MM-DD` — agrupado por propietario
- `GET /api/opl/detalle?propietario=X&fecha=YYYY-MM-DD` — lista individual OPL
- `GET /api/salidas?fecha=YYYY-MM-DD&dias=1` — canales despachadas
- `GET /api/planilla_opl?fecha=YYYY-MM-DD&turno=DxL` — progreso OPL combinado
