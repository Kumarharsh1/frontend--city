# Deployment

## Architecture

- Deploy `frontend/` to Netlify.
- Deploy the FastAPI application (`main.py`) to a Python host such as Render, Railway, Fly.io, or a VPS.
- Netlify cannot reliably run the OSMnx graph download and NetworkX routing workload as a static site.

## Backend

Start locally with:

```powershell
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

For a small deployment, run one worker so the in-memory analysis cache is not duplicated:

```powershell
pip install -r requirements-backend.txt
python -m uvicorn main:app --host 0.0.0.0 --port $env:PORT
```

On Linux hosts such as Render or Railway, use `$PORT` instead of PowerShell's `$env:PORT`:

```bash
python -m uvicorn main:app --host 0.0.0.0 --port $PORT
```

The first request for a city downloads its road graph. Later requests reuse the graph from
`cache/graphs/`, so keep that directory on persistent storage when your hosting provider
supports it. Pre-warm the common city after deployment by requesting `/connectivity?city=...`.

Your deployed backend exposes:

- `GET /route?city=...&origin=...&destination=...`
- `GET /connectivity?city=...`
- `GET /bottlenecks?city=...&top_n=5`
- `GET /accessibility?city=...&target=...&sample=10`
- `GET /map?city=...&origin=...&destination=...`

## Netlify frontend

The frontend is configured to use the Render backend by default:

```js
const API_BASE = localStorage.getItem('geoaiApiUrl') || 'https://city-1-6jst.onrender.com';
```

In Netlify, set the publish directory to `frontend` (or drag the `frontend` folder into Netlify Drop). The included `netlify.toml` supports direct-link refreshes.

For a custom API domain, update `allow_origins` in `main.py` from `['*']` to your Netlify domain.
