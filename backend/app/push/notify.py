"""Web Push: real phone notifications for triggered alerts (works on Android, and on iOS once the
app is added to the home screen). Each subscription carries its own thresholds and categories."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from pathlib import Path

from ..config import DATA_DIR, VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY, VAPID_SUBJECT

log = logging.getLogger("finn.notify")

_KEY_FILE = DATA_DIR / "vapid.json"
_SUBS_FILE = DATA_DIR / "push_subscriptions.json"
_lock = asyncio.Lock()


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def vapid_keys() -> dict:
    """VAPID key pair (from env, else generated once and persisted)."""
    if VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY:
        return {"private": VAPID_PRIVATE_KEY, "public": VAPID_PUBLIC_KEY}
    if _KEY_FILE.exists():
        try:
            return json.loads(_KEY_FILE.read_text())
        except ValueError:
            pass
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    priv = key.private_numbers().private_value.to_bytes(32, "big")
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    keys = {"private": _b64url(priv), "public": _b64url(pub)}
    _KEY_FILE.write_text(json.dumps(keys))
    return keys


def _load() -> dict:
    try:
        return json.loads(Path(_SUBS_FILE).read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _save(subs: dict) -> None:
    tmp = _SUBS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(subs))
    tmp.replace(_SUBS_FILE)


async def subscribe(subscription: dict, prefs: dict) -> None:
    endpoint = subscription.get("endpoint")
    if not endpoint or not (subscription.get("keys") or {}).get("p256dh"):
        raise ValueError("invalid push subscription")
    async with _lock:
        subs = _load()
        prev = subs.get(endpoint, {})
        subs[endpoint] = {"subscription": subscription, "prefs": prefs, "sent": prev.get("sent", []), "created": prev.get("created", time.time())}
        _save(subs)


async def unsubscribe(endpoint: str) -> None:
    async with _lock:
        subs = _load()
        subs.pop(endpoint, None)
        _save(subs)


def subscriptions() -> dict:
    return _load()


def _send_sync(subscription: dict, payload: dict) -> int:
    from pywebpush import WebPushException, webpush

    keys = vapid_keys()
    try:
        resp = webpush(
            subscription_info=subscription,
            data=json.dumps(payload),
            vapid_private_key=keys["private"],
            vapid_claims={"sub": VAPID_SUBJECT},
            ttl=6 * 3600,
        )
        return getattr(resp, "status_code", 201)
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None) or 500
        log.info("push failed (%s): %s", status, exc)
        return status


async def send(endpoint: str, payload: dict) -> int:
    subs = _load()
    entry = subs.get(endpoint)
    if not entry:
        return 404
    status = await asyncio.to_thread(_send_sync, entry["subscription"], payload)
    if status in (404, 410):  # subscription expired or revoked
        await unsubscribe(endpoint)
    return status


async def mark_sent(endpoint: str, ids: list[str]) -> None:
    async with _lock:
        subs = _load()
        if endpoint in subs:
            subs[endpoint]["sent"] = (subs[endpoint].get("sent", []) + ids)[-500:]
            _save(subs)
