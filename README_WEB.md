# Crypto Radar Web Platform

Esta fase agrega una plataforma web separada del scanner existente.

## Arquitectura

```text
Scanner Python existente -> crypto_radar.db -> FastAPI (`api/`) -> Next.js + TypeScript (`web/`)
```

- `engine`: obtiene, analiza y persiste datos.
- `api`: expone únicamente snapshots ya guardados; no consulta providers ni recalcula scores.
- `web`: presenta los datos de la API; no contiene lógica de scoring.

## API

Desde `C:\Defi_Aura`:

```powershell
python -m venv .venv-api
.\.venv-api\Scripts\Activate.ps1
pip install -r api\requirements.txt
python api\run.py
```

API: `http://127.0.0.1:8000`

- `GET /api/health`
- `GET /api/pools`
- `GET /api/pools/{address}`
- `GET /api/protocols/health`

La API abre `crypto_radar.db` con SQLite `mode=ro`. Se puede cambiar la ruta con `CRYPTO_RADAR_DB`.

## Frontend

Requiere Node.js y npm/pnpm:

```powershell
cd web
npm install
npm run dev
```

Frontend: `http://127.0.0.1:3000`

Si la API corre en otra URL, definir `NEXT_PUBLIC_API_URL` antes de iniciar Next.js.

## Dependencias y carpetas

- API: FastAPI, Uvicorn, Python standard library y SQLite existente.
- Web: Next.js 14, React 18, TypeScript.
- `api/main.py`: endpoints de lectura.
- `api/run.py`: servidor local.
- `web/app/page.tsx`: primera tabla de pools.
- `web/app/globals.css`: estilos de la primera pantalla.

No se incluyen todavía wallet, login, alertas, ejecución, portfolio ni multi-chain.
