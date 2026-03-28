import logging
from typing import Any, Dict

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def forward_to_layer3(payload: Dict[str, Any]) -> None:
    """Best-effort forward of Layer 2 output to Layer 3.

    Failures are logged and do not affect Layer 2 response to callers.
    """
    url = (settings.layer3_url or "").strip()
    if not url:
        return

    try:
        with httpx.Client(timeout=settings.layer3_timeout_seconds) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
    except Exception as exc:
        logger.warning("Layer3 forward failed (%s): %s", url, exc)
