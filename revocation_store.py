# src/revocation_store.py
"""
Revocation Store — closes Gap G-1.

Provides a filesystem-backed, fail-closed revocation mechanism that mirrors
the Burn Store pattern (os.open O_CREAT|O_EXCL for atomicity).

Layout:
    wards/<handle>/warrants_revoked/<warrant_id>.revoked

Each file contains a JSON revocation record:
    {
        "warrant_id":  "<uuid>",
        "revoked_at":  "<ISO8601>",
        "revoked_by":  "<actor>",
        "reason":      "<human-readable string>"
    }

API:
    revoke_warrant(ward_path, warrant_id, reason, revoked_by) -> str  (revocation_id path)
    is_revoked(ward_path, warrant_id) -> bool
    load_revocation(ward_path, warrant_id) -> dict | None

Production note (G-4):
    This implementation provides single-node filesystem atomicity via O_CREAT|O_EXCL.
    It does NOT satisfy distributed linearisability (A2). Replace with a linearisable
    distributed store (Redis SETNX, CAS key-value, or serialised append-only log)
    before any multi-node deployment.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


class RevocationError(Exception):
    """Raised when the revocation check itself fails (distinct from 'is revoked')."""


def _revoked_dir(ward_path: Path) -> Path:
    d = ward_path / "warrants_revoked"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _revocation_path(ward_path: Path, warrant_id: str) -> Path:
    return _revoked_dir(ward_path) / f"{warrant_id}.revoked"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def revoke_warrant(
    ward_path: Path,
    warrant_id: str,
    reason: str = "revoked",
    revoked_by: str = "ward:operator",
) -> Path:
    """
    Record a revocation for warrant_id.  Fail-closed: raises if the warrant
    is already revoked (idempotency not guaranteed — treat as single-write).

    Returns the path to the revocation record file.
    """
    warrant_id = (warrant_id or "").strip()
    if not warrant_id:
        raise ValueError("warrant_id must be non-empty")

    revocation_path = _revocation_path(ward_path, warrant_id)

    record = {
        "warrant_id": warrant_id,
        "revoked_at": _utc_now_iso(),
        "revoked_by": (revoked_by or "ward:operator").strip(),
        "reason":     (reason or "revoked").strip(),
    }

    try:
        fd = os.open(str(revocation_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as f:
            json.dump(record, f, indent=2)
    except FileExistsError:
        raise RevocationError(
            f"Warrant already revoked: {warrant_id}  (record: {revocation_path})"
        )
    except OSError as exc:
        raise RevocationError(
            f"Failed to write revocation record for {warrant_id}: {exc}"
        ) from exc

    return revocation_path


def is_revoked(ward_path: Path, warrant_id: str) -> bool:
    """
    Return True if warrant_id has been revoked, False otherwise.

    Fail-closed: any unexpected OS error raises RevocationError rather than
    silently returning False (which would allow a potentially revoked token
    to pass).
    """
    warrant_id = (warrant_id or "").strip()
    if not warrant_id:
        raise RevocationError("is_revoked: warrant_id must be non-empty")

    try:
        return _revocation_path(ward_path, warrant_id).exists()
    except OSError as exc:
        raise RevocationError(
            f"Revocation check failed for {warrant_id}: {exc}"
        ) from exc


def load_revocation(ward_path: Path, warrant_id: str) -> Optional[Dict[str, Any]]:
    """
    Return the revocation record dict if the warrant is revoked, else None.
    """
    p = _revocation_path(ward_path, warrant_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"warrant_id": warrant_id, "reason": "revocation file unreadable"}

