"""Runtime configuration. Everything is overridable through environment variables."""
from __future__ import annotations

import os
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


DATA_DIR = Path(_env("FINN_DATA_DIR", str(Path(__file__).resolve().parents[2] / ".finn-data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

STATIC_DIR = Path(_env("FINN_STATIC_DIR", str(Path(__file__).resolve().parents[2] / "frontend" / "dist")))

GEMINI_API_KEY = _env("GEMINI_API_KEY")
GROQ_API_KEY = _env("GROQ_API_KEY")

# Optional explicit model overrides (comma-separated preference lists). When unset, FINN discovers
# what the key can actually use at startup and picks the best match for each tier.
GEMINI_MODELS_FAST = _env("GEMINI_MODELS_FAST")
GEMINI_MODELS_SMART = _env("GEMINI_MODELS_SMART")
GROQ_MODELS_FAST = _env("GROQ_MODELS_FAST")
GROQ_MODELS_SMART = _env("GROQ_MODELS_SMART")

# Background refresh of the push feed (minutes). 0 disables the loop (used in tests).
REFRESH_MINUTES = int(_env("FINN_REFRESH_MINUTES", "15") or "15")
# Pre-compute research for the demo set on startup so the first visit is instant.
WARM_RESEARCH = _env("FINN_WARM_RESEARCH", "1") == "1"

# Web push (VAPID). Generated and persisted under DATA_DIR when not provided.
VAPID_PRIVATE_KEY = _env("VAPID_PRIVATE_KEY")
VAPID_PUBLIC_KEY = _env("VAPID_PUBLIC_KEY")
VAPID_SUBJECT = _env("VAPID_SUBJECT", "mailto:finn-demo@example.com")

HTTP_TIMEOUT = float(_env("FINN_HTTP_TIMEOUT", "25") or "25")
USER_AGENT = _env(
    "FINN_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36",
)
