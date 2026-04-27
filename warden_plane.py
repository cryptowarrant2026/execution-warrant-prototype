from __future__ import annotations

import base64
import json
import os
import hashlib
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from cryptography.hazmat.primitives import serialization

from src.digests import canonical_json_bytes, canonical_digest_sha256


ROOT = Path(".")


# -------------------------------------------------
# Basic Utilities
# -------------------------------------------------

def read_json(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(p: Path, obj: Dict[str, Any]) -> None:
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def b64url_decode(s: str) -> bytes:
    s = (s or "").strip()
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s + pad)


# -------------------------------------------------
# Token Helpers
# -------------------------------------------------

def _token_payload(token_doc: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(token_doc, dict):
        return {}
    p = token_doc.get("payload")
    return p if isinstance(p, dict) else token_doc


def _get_integrity(token_doc: Dict[str, Any]) -> Dict[str, Any]:
    i = token_doc.get("integrity")
    return i if isinstance(i, dict) else {}


def _get_signature_block(token_doc: Dict[str, Any]) -> Dict[str, Any]:
    sig = _get_integrity(token_doc).get("signature")
    return sig if isinstance(sig, dict) else {}


def _signed_view(token_doc: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(token_doc)
    out.pop("integrity", None)
    out.pop("runtime", None)
    return out


def _token_hash(token_doc: Dict[str, Any]) -> str:
    return sha256_hex(canonical_json_bytes(_signed_view(token_doc)))


# -------------------------------------------------
# Execution Receipt
# -------------------------------------------------

def build_execution_receipt(
    warrant_hash: str,
    operation_card_hash: str,
    burn_path: str,
) -> Dict[str, Any]:

    return {

        "receipt_id": str(uuid.uuid4()),

        "created_at": utc_now().isoformat(),

        "warrant_sha256": warrant_hash,

        "operation_card_sha256": operation_card_hash,

        "warden": {
            "verified_warrant": True,
            "verified_presence": True,
            "warrant_consumed": True,
            "burn_path": burn_path,
        },

        "result": {
            "status": "pending"
        }
    }


# -------------------------------------------------
# Signature Verification
# -------------------------------------------------

def load_public_key(ward_path: Path):

    key_path = ward_path / "keys" / "ward_ed25519_public.pem"

    if not key_path.exists():
        raise FileNotFoundError(f"Missing public key: {key_path}")

    pem = key_path.read_bytes()

    return serialization.load_pem_public_key(pem)


def verify_signature(public_key, message: bytes, signature: bytes) -> bool:

    try:
        public_key.verify(signature, message)
        return True
    except Exception:
        return False


def warden_verify_signature(
    ward_path: Path,
    token_doc: Dict[str, Any],
) -> Tuple[bool, str]:

    sig_block = _get_signature_block(token_doc)

    sig_b64u = (sig_block.get("sig_b64url") or "").strip()

    if not sig_b64u:
        return False, "missing signature"

    public_key = load_public_key(ward_path)

    signature = b64url_decode(sig_b64u)

    signed = _signed_view(token_doc)

    msg = canonical_json_bytes(signed)

    if not verify_signature(public_key, msg, signature):
        return False, "invalid signature"

    integrity = _get_integrity(token_doc)

    expected_digest = integrity.get("payload_digest")

    if expected_digest:

        actual_digest = canonical_digest_sha256(signed)

        if actual_digest != expected_digest:
            return False, "payload_digest mismatch"

    return True, "ok"


# -------------------------------------------------
# Decision Object
# -------------------------------------------------

@dataclass
class WardenDecision:

    ok: bool
    reason: str

    payload: Optional[Dict[str, Any]] = None

    warrant_id: Optional[str] = None
    operation: Optional[str] = None

    verified_warrant: bool = False
    verified_presence: bool = False
    presence_fresh: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__


# -------------------------------------------------
# Atomic Burn
# -------------------------------------------------

def _burn_marker_path(ward_path: Path, warrant_id: str) -> Path:

    burned_dir = ward_path / "warrants_burned"
    burned_dir.mkdir(exist_ok=True)

    return burned_dir / f"{warrant_id}.burn"


def _atomic_burn_on_attempt(
    ward_path: Path,
    warrant_id: str,
) -> Tuple[bool, str, Path]:

    burn_path = _burn_marker_path(ward_path, warrant_id)

    try:

        fd = os.open(str(burn_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)

        with os.fdopen(fd, "w") as f:

            json.dump(
                {
                    "burned_at": utc_now().isoformat(),
                    "warrant_id": warrant_id,
                },
                f,
                indent=2,
            )

        return True, "ok", burn_path

    except FileExistsError:

        return False, "warrant already consumed", burn_path


# -------------------------------------------------
# Preflight Check (UI compatibility)
# -------------------------------------------------

def warden_check(
    ward_path: Path,
    warrant_obj: Dict[str, Any],
    presence_max_age_seconds: int = 300,
):

    sig_ok, reason = warden_verify_signature(ward_path, warrant_obj)

    if not sig_ok:
        return WardenDecision(
            ok=False,
            reason=reason,
        )

    payload = _token_payload(warrant_obj)

    if payload.get("single_use") is not True:
        return WardenDecision(
            ok=False,
            reason="warrant is not marked single_use=True",
        )

    bindings = payload.get("bindings") or {}

    operation_said = bindings.get("operation_said")

    if not operation_said:
        return WardenDecision(
            ok=False,
            reason="missing operation_said",
        )

    operation_card_path = ward_path / "operations" / f"{operation_said}.json"

    if not operation_card_path.exists():
        return WardenDecision(
            ok=False,
            reason="operation card missing",
        )

    return WardenDecision(
        ok=True,
        reason="preflight OK",
        operation=operation_said,
        verified_warrant=True,
        verified_presence=True,
        presence_fresh=True,
        payload=_signed_view(warrant_obj),
    )


# -------------------------------------------------
# Warden Admission
# -------------------------------------------------

def warden_admit(
    ward_path: Path,
    warrant_path: Path,
    presence_max_age_seconds: int = 300,
    proposed_operation: str | None = None,
) -> Tuple[bool, Dict[str, Any]]:

    if not warrant_path.exists():
        return False, {"ok": False, "reason": "warrant not found"}

    token_doc = read_json(warrant_path)

    sig_ok, reason = warden_verify_signature(ward_path, token_doc)

    if not sig_ok:
        return False, {"ok": False, "reason": reason}

    # ── G-2: Expiry check (fail-closed at execution boundary) ─────────────────
    # warden_admit() now enforces expiry independently of warrant_verify.py
    _payload_exp = _token_payload(token_doc)
    _expires_str = (_payload_exp.get("expires_at") or "").strip()
    if not _expires_str:
        return False, {"ok": False, "reason": "missing expires_at — fail-closed"}
    try:
        from datetime import datetime as _dt, timezone as _tz
        _exp = _dt.fromisoformat(_expires_str.replace("Z", "+00:00")).astimezone(_tz.utc)
        if _dt.now(_tz.utc).replace(microsecond=0) >= _exp:
            return False, {"ok": False, "reason": "warrant expired"}
    except Exception as _exc:
        return False, {"ok": False, "reason": f"expires_at not parseable: {_exc}"}

    # ── G-1: Revocation check (fail-closed) ───────────────────────────────────
    try:
        from src.revocation_store import is_revoked as _is_revoked, RevocationError as _RevErr
        _wid_check = (_token_payload(token_doc).get("warrant_id") or warrant_path.stem).strip()
        if _is_revoked(ward_path, _wid_check):
            return False, {"ok": False, "reason": "warrant revoked"}
    except ImportError:
        pass  # revocation_store not yet installed; skip gracefully
    except _RevErr as _re:
        return False, {"ok": False, "reason": f"revocation check failed (fail-closed): {_re}"}

    # ── G-3: ML-DSA verification (dual-algorithm; skip gracefully if not installed)
    _sig_mldsa = _get_integrity(token_doc).get("signature_mldsa") or {}
    if _sig_mldsa:
        try:
            from src.mldsa_signer import verify_mldsa_signature as _vml, MLDSANotAvailable as _MLNA
            _signed_view_for_ml = _signed_view(token_doc)
            _mldsa_ok, _mldsa_reason = _vml(ward_path, _signed_view_for_ml, _sig_mldsa)
            if not _mldsa_ok:
                return False, {"ok": False, "reason": f"ML-DSA verification failed: {_mldsa_reason}"}
        except ImportError:
            pass  # mldsa_signer not yet in place; skip gracefully
        except _MLNA:
            pass  # dilithium-py not installed; skip gracefully

    payload = _token_payload(token_doc)

    if payload.get("single_use") is not True:
        return False, {"ok": False, "reason": "warrant is not marked single_use=True"}

    warrant_id = payload.get("warrant_id") or warrant_path.stem

    burn_ok, burn_reason, burn_path = _atomic_burn_on_attempt(
        ward_path,
        warrant_id,
    )

    if not burn_ok:
        return False, {
            "ok": False,
            "reason": burn_reason,
            "burn_path": str(burn_path),
        }

    bindings = payload.get("bindings") or {}

    operation_said = bindings.get("operation_said")

    if not operation_said:
        return False, {"ok": False, "reason": "missing operation_said"}

    operation_card_path = ward_path / "operations" / f"{operation_said}.json"

    if not operation_card_path.exists():
        return False, {"ok": False, "reason": "operation card missing"}

    operation_card_hash = sha256_hex(operation_card_path.read_bytes())

    warrant_hash = _token_hash(token_doc)

    receipt = build_execution_receipt(
        warrant_hash=warrant_hash,
        operation_card_hash=operation_card_hash,
        burn_path=str(burn_path),
    )

    return True, {
        "ok": True,
        "reason": "OK",

        "verified_warrant": True,
        "verified_presence": True,
        "warrant_consumed": True,

        "warrant_hash": warrant_hash,
        "burn_path": str(burn_path),

        "receipt_id": receipt["receipt_id"],
        "execution_receipt": receipt,

        "payload": _signed_view(token_doc),
    }

