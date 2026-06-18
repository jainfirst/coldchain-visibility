"""
API clients for FedEx tracking and Tive sensor data.

Both wrap OAuth2 client-credentials auth with in-process token caching.

The Tive client also accepts a pre-issued bearer token (TIVE_BEARER_TOKEN),
because the exercise notes the OAuth credentials may not work and a 1-hour
token can be supplied instead.  If no valid token is available the client
raises TiveAuthError — it NEVER fabricates sensor readings.  For a cold-chain
visibility tool, showing invented temperatures would be worse than showing
nothing, so "no data" is an explicit, surfaced state.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import requests

from . import config
from .util import utc_now_iso

# Where we cache the last successful Tive pull. Serving this (clearly labelled,
# with its capture time) when the live token is unavailable is honest — it's
# real data, dated — and means a demo or an ops screen never goes blank just
# because a 1-hour token lapsed. It is NEVER presented as live.
_SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "tive_snapshot.json"


class TiveAuthError(RuntimeError):
    """Raised when no valid Tive bearer token can be obtained."""


class _TokenCache:
    def __init__(self) -> None:
        self.token: str | None = None
        self.expires_at: float = 0.0

    def valid(self) -> bool:
        # 60s safety margin before real expiry.
        return self.token is not None and time.time() < self.expires_at - 60

    def set(self, token: str, expires_in: int) -> None:
        self.token = token
        self.expires_at = time.time() + expires_in


class FedExClient:
    def __init__(self) -> None:
        self._cache = _TokenCache()
        self._lock = threading.Lock()

    def _token(self) -> str:
        if self._cache.valid():
            return self._cache.token  # type: ignore[return-value]
        if not (config.FEDEX_CLIENT_ID and config.FEDEX_CLIENT_SECRET):
            raise RuntimeError("FedEx credentials not set "
                               "(FEDEX_CLIENT_ID / FEDEX_CLIENT_SECRET).")
        with self._lock:                      # one refresh at a time (double-checked)
            if self._cache.valid():
                return self._cache.token  # type: ignore[return-value]
            resp = requests.post(
                config.FEDEX_TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": config.FEDEX_CLIENT_ID,
                    "client_secret": config.FEDEX_CLIENT_SECRET,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            self._cache.set(data["access_token"], int(data.get("expires_in", 3600)))
            return self._cache.token  # type: ignore[return-value]

    def track(self, tracking_number: str) -> dict[str, Any]:
        resp = requests.post(
            config.FEDEX_TRACK_URL,
            headers={
                "Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json",
                "X-locale": "en_US",
            },
            json={
                "includeDetailedScans": True,
                "trackingInfo": [
                    {"trackingNumberInfo": {"trackingNumber": tracking_number}}
                ],
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()


class TiveClient:
    def __init__(self) -> None:
        self._cache = _TokenCache()
        self._lock = threading.Lock()

    def _oauth_token(self) -> str | None:
        """Attempt the OAuth client-credentials flow. Returns None on failure."""
        if not (config.TIVE_CLIENT_ID and config.TIVE_CLIENT_SECRET):
            return None
        try:
            resp = requests.post(
                config.TIVE_TOKEN_URL,
                json={
                    "clientId": config.TIVE_CLIENT_ID,
                    "clientSecret": config.TIVE_CLIENT_SECRET,
                    "grant_type": "client_credentials",
                },
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            token = data.get("access_token") or data.get("token")
            if token:
                self._cache.set(token, int(data.get("expires_in", 3600)))
            return token
        except requests.RequestException:
            return None

    def _token(self) -> str:
        """Return a usable bearer token or raise TiveAuthError.

        Preference order: explicit TIVE_BEARER_TOKEN (the vendor-issued 1-hour
        token) -> cached OAuth token -> fresh OAuth attempt.
        """
        if config.TIVE_BEARER_TOKEN:
            return config.TIVE_BEARER_TOKEN
        if self._cache.valid():
            return self._cache.token  # type: ignore[return-value]
        with self._lock:                      # one OAuth attempt at a time
            if self._cache.valid():
                return self._cache.token  # type: ignore[return-value]
            token = self._oauth_token()
            if not token:
                raise TiveAuthError(
                    "No valid Tive token. The OAuth credentials return 401 "
                    "'Credentials not found'; set TIVE_BEARER_TOKEN to a "
                    "vendor-issued bearer token to go live."
                )
            return token

    def tracker_data(self) -> dict[str, Any]:
        """Fetch raw Tive trackerData live, and cache it. Raises on failure."""
        resp = requests.get(
            config.TIVE_DATA_URL,
            headers={
                "Authorization": f"Bearer {self._token()}",
                "x-tive-account-id": config.TIVE_ACCOUNT_ID,
            },
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        self._write_snapshot(payload)
        return payload

    @staticmethod
    def _write_snapshot(payload: dict[str, Any]) -> None:
        try:
            _SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
            _SNAPSHOT.write_text(json.dumps({
                "captured_at": utc_now_iso(),
                "shipment": config.TIVE_SHIPMENT,
                "payload": payload,
            }))
        except OSError:
            pass  # caching is best-effort; never block a live response on it

    @staticmethod
    def snapshot() -> tuple[dict[str, Any], str] | None:
        """Return (payload, captured_at) from the last good pull, or None."""
        if not _SNAPSHOT.exists():
            return None
        try:
            data = json.loads(_SNAPSHOT.read_text())
            return data["payload"], data.get("captured_at", "unknown")
        except (OSError, ValueError, KeyError):
            return None


# Module-level singletons. Clients hold a token cache with expiry, so reusing one
# instance across requests means we authenticate once per token lifetime — not on
# every call (which a per-request client would do, adding an OAuth round-trip each
# time). Safe to share: token refresh is guarded by a lock.
fedex_client = FedExClient()
tive_client = TiveClient()
