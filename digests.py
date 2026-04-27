# src/digests.py
from __future__ import annotations

import base64
import json
from hashlib import sha256
from typing import Any, Iterable, Optional


def canonical_json_bytes(obj: Any) -> bytes:
    """
    Deterministic JSON bytes for hashing:
      - sorted keys
      - no whitespace
      - UTF-8 bytes
      - ensure_ascii=False (preserve unicode)
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256_b64url_nopad(data: bytes) -> str:
    """
    Returns base64url encoding of SHA-256 digest bytes (no '=' padding),
    prefixed with 'sha256:'.
    """
    digest_bytes = sha256(data).digest()
    b64url = base64.urlsafe_b64encode(digest_bytes).rstrip(b"=").decode("ascii")
    return f"sha256:{b64url}"


def canonical_digest_sha256(
    obj: Any,
    *,
    exclude_keys: Optional[Iterable[str]] = None,
) -> str:
    """
    Canonical digest for JSON-serializable objects.
    Optionally exclude top-level keys (e.g., 'operation_digest') to avoid self-reference.
    """
    if exclude_keys:
        if not isinstance(obj, dict):
            raise TypeError("exclude_keys requires obj to be a dict (top-level only)")
        material = dict(obj)
        for k in exclude_keys:
            material.pop(k, None)
    else:
        material = obj

    return sha256_b64url_nopad(canonical_json_bytes(material))

