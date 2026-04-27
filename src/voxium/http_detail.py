"""HTTP error string extraction (pure, for requests / FastAPI error bodies)."""

from __future__ import annotations

from typing import Any


def http_error_detail_text(exc: Any) -> str:
    """
    Prefer FastAPI JSON `detail` over a generic 500 string.
    ``exc`` is typically ``requests.HTTPError`` with a ``.response`` attribute.
    """
    r = getattr(exc, "response", None)
    if r is not None:
        try:
            data = r.json()
            d = data.get("detail")
            if isinstance(d, str) and d.strip():
                return d.strip()
        except Exception:
            pass
        text = (r.text or "").strip()
        if text:
            return text[:500]
    return str(exc)
