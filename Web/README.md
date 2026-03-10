# e-CALLISTO FITS Analyzer Web

This workspace contains a standalone web implementation of the FITS viewer, background reduction controls, and export tools. Everything lives under `Web/` and does not modify the desktop application.

## Layout

- `backend/`: FastAPI service with FITS parsing, background reduction, exports, session storage, and rate limiting.
- `frontend/`: React + Vite + TypeScript workbench using Plotly for the spectrum viewer.
- `runtime/`: ephemeral server-side session storage for uploaded FITS files and temporary artifacts. This folder is created at runtime and ignored by git.

## Features

- Anonymous ephemeral sessions with one active FITS dataset per session.
- FITS upload with lightweight session status in the UI.
- FITS upload for `.fit`, `.fit.gz`, `.fits`, and `.fits.gz`.
- Dynamic spectrum viewer for raw and background-reduced data.
- Automatic row-mean background subtraction with low/high clipping controls.
- Exports for spectrum figures and raw or processed FITS files.
- In-memory rate limiting plus periodic cleanup of expired runtime sessions.

## Local Development

Use Docker Compose if you want the `.env` file loaded automatically. For host-based local development, the app works with defaults and you only need to export variables manually if you want to override them.

Optional Docker/env setup:

```bash
cp Web/.env.example Web/.env
```

Start the backend:

```bash
cd Web/backend
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Start the frontend in a second terminal:

```bash
cd Web/frontend
npm install
npm run dev
```

The Vite dev server proxies `/api` requests to the backend. By default the app opens at [http://localhost:5173](http://localhost:5173).

## Docker Development

Build and run both services from `Web/`:

```bash
docker compose up --build
```

Services:

- Frontend: [http://localhost:5173](http://localhost:5173)
- Backend API: [http://localhost:8000](http://localhost:8000)

## Test Commands

Backend:

```bash
cd Web/backend
. .venv/bin/activate
python -m pytest tests -q
```

Frontend:

```bash
cd Web/frontend
npm test
npm run build
```

## API Surface

- `POST /api/v1/sessions`
- `POST /api/v1/sessions/{sessionId}/dataset`
- `POST /api/v1/sessions/{sessionId}/processing/background`
- `POST /api/v1/sessions/{sessionId}/exports/figure`
- `POST /api/v1/sessions/{sessionId}/exports/fits`
