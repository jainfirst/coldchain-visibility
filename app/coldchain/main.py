"""
FastAPI app: serves the visibility dashboard and a JSON API that merges
FedEx tracking with Tive sensor data and excursion analysis.

Run:  uvicorn app.coldchain.main:app --reload  (from the repo root)
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .clients import tive_client
from .fleet import build_fleet_view, csv_available
from .service import build_shipment_view
from .util import jsonable

_STATIC = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Cold-Chain Visibility", version="1.0")


@app.get("/api/shipment")
def shipment(
    tracking_number: str = Query(default=config.DEFAULT_TRACKING_NUMBER),
    temp_class: str = Query(default=config.DEFAULT_TEMP_CLASS),
    transit_only: bool = Query(
        default=False,
        description="Clip the temperature analysis to the FedEx pickup->delivery window.",
    ),
) -> JSONResponse:
    """Merged FedEx + Tive view with excursion analysis."""
    return JSONResponse(jsonable(
        build_shipment_view(tracking_number, temp_class, transit_only)))


@app.get("/api/temp-classes")
def temp_classes() -> JSONResponse:
    """Expose the temperature policy so the UI can show assumptions inline."""
    return JSONResponse({
        k: {"lo": b.lo, "hi": b.hi, "rationale": b.rationale}
        for k, b in config.TEMP_BANDS.items()
    })


@app.get("/api/fleet")
def fleet() -> JSONResponse:
    """Portfolio-level KPIs, triaged alert queue, and the patterns behind them."""
    return JSONResponse(jsonable(build_fleet_view()))


@app.get("/healthz")
def healthz() -> JSONResponse:
    """Readiness probe: reports data-source health without hitting the live APIs."""
    snap = tive_client.snapshot()
    return JSONResponse(jsonable({
        "status": "ok",
        "tive_bearer_token_set": bool(config.TIVE_BEARER_TOKEN),
        "tive_snapshot_available": snap is not None,
        "tive_snapshot_captured_at": snap[1] if snap else None,
        "shipment_csv_present": csv_available(),
        "temp_classes": list(config.TEMP_BANDS),
    }))


# The dashboards are single HTML files with inline JS. Browsers (Safari especially)
# cache these aggressively and can serve stale JS against fresh API data, which looks
# like "the page broke." Force a no-store revalidate so a refresh always gets the latest.
_NO_CACHE = {"Cache-Control": "no-store, max-age=0"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html", headers=_NO_CACHE)


@app.get("/fleet")
def fleet_page() -> FileResponse:
    return FileResponse(_STATIC / "fleet.html", headers=_NO_CACHE)


app.mount("/static", StaticFiles(directory=_STATIC), name="static")
