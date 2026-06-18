# Deploying the dashboard as a shareable link

The app is a FastAPI server that serves the dashboards and a JSON API. Host it once
and share the URL — no install for the viewer.

**It works fully without any live tokens.** The Fleet Command Center runs entirely off
the bundled 90-day CSV, and the live screen serves the bundled Tive *snapshot*
(real cached data, clearly labelled "NOT LIVE / SNAPSHOT"). So you can deploy with no
secrets and it's still completely playable.

## Recommended: Render (free, ~5 min)

1. Push this repo to GitHub (**public or private — no secrets are in source**). `.env` is
   gitignored, so your credentials never ship; the app runs off the bundled snapshot + CSV.
2. In Render: **New + → Blueprint → connect the repo → Apply**. It reads `render.yaml`
   and builds the `Dockerfile`.
3. You get a URL like `https://coldchain-visibility.onrender.com`. Share it.
   - `/` is the live shipment dashboard, `/fleet` is the command center, `/healthz` is the probe.
   - Free tier sleeps after inactivity, so the first hit takes ~30s to wake — normal.

## Alternatives (same Dockerfile, no lock-in)

- **Railway** — New Project → Deploy from repo → it detects the Dockerfile. Set no env vars to run off the snapshot.
- **Fly.io** — `fly launch` (uses the Dockerfile), then `fly deploy`.
- **Google Cloud Run** — `gcloud run deploy --source .` (scales to zero, generous free tier).
- **Hugging Face Spaces** — create a Docker Space, push the repo.

Locally, the same image runs with:
```
docker build -t coldchain . && docker run -p 8000:8000 coldchain
# -> http://localhost:8000
```

## Secrets / making FedEx live

All credentials are read from the environment only — **no values live in source**, so the repo
is safe to make public. `.env` is gitignored.

- **Default (no env vars):** the Fleet view is fully live off the CSV; the live screen serves
  the bundled Tive **snapshot** (labelled "NOT LIVE"). Completely playable.
- **To make FedEx live** (status, route, scan history, pickup/delivery markers): set
  `FEDEX_CLIENT_ID` and `FEDEX_CLIENT_SECRET` in the host's environment settings.
- **To force live Tive briefly:** set `TIVE_BEARER_TOKEN` (expires ~1h, so the snapshot is the
  steady state).

See `.env.example` for the full list.
