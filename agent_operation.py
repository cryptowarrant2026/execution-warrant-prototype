from __future__ import annotations

import hashlib
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.warden_plane import warden_admit


# ----------------------------
# Small utilities
# ----------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def safe_str(x: Any) -> str:
    try:
        return str(x)
    except Exception:
        return "<unstringifiable>"


def _strip_sha256_prefix(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("sha256:"):
        return s.split("sha256:", 1)[1].strip()
    return s


# ----------------------------
# Token helpers (support new + old token shapes)
# ----------------------------

def _token_payload(token_doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Support BOTH token shapes:
      A) New: payload-at-top-level (current mint)
      B) Old: {"payload": {...}, "integrity": {...}}
    """
    if not isinstance(token_doc, dict):
        return {}
    p = token_doc.get("payload")
    return p if isinstance(p, dict) else token_doc


def _get_bindings(payload: Dict[str, Any]) -> Dict[str, Any]:
    b = payload.get("bindings")
    return b if isinstance(b, dict) else {}


def _operation_name_from_card_raw(raw: Dict[str, Any]) -> str:
    """
    Support both shapes:
      - legacy: raw["operation_name"]
      - current: raw["operation"]["name"]
    """
    if not isinstance(raw, dict):
        return ""
    op_name = (raw.get("operation_name") or "").strip()
    if op_name:
        return op_name
    op = raw.get("operation")
    if isinstance(op, dict):
        return (op.get("name") or "").strip()
    return ""


def _operation_label_from_card_raw(raw: Dict[str, Any]) -> str:
    op = raw.get("operation")
    if isinstance(op, dict):
        return (op.get("label") or "").strip()
    return ""


def _extract_operation_ref_from_bindings(bindings: Dict[str, Any]) -> str:
    """
    Support iterative naming:
      - operation_ref (preferred)
      - operation_said (warden/UI might expect)
      - operation_digest (older)
    """
    if not isinstance(bindings, dict):
        return ""
    return (
        (bindings.get("operation_ref") or "").strip()
        or (bindings.get("operation_said") or "").strip()
        or (bindings.get("operation_digest") or "").strip()
    )


# ----------------------------
# Normalization for agent/receipt convenience
# ----------------------------

def normalize_payload_for_agent(ward_path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Derived, unsigned view for execution + receipts.

    Ensures:
      - payload.role_id, payload.persona_id exist (from bindings)
      - payload.operation_ref exists (from bindings)
      - payload.operation (name) exists by reading Operation Card (operations/<SAID>.json)

    IMPORTANT:
      - This is for *agent convenience only*. It must not be written back into the warrant token.
    """
    if not isinstance(payload, dict):
        return {}

    out = dict(payload)
    bindings = _get_bindings(payload)

    # role_id
    if out.get("role_id") in (None, "", [], {}):
        role = bindings.get("role") if isinstance(bindings, dict) else None
        if isinstance(role, dict):
            rid = (role.get("role_id") or "").strip()
            if rid:
                out["role_id"] = rid

    # persona_id
    if out.get("persona_id") in (None, "", [], {}):
        persona = bindings.get("persona") if isinstance(bindings, dict) else None
        if isinstance(persona, dict):
            pid = (persona.get("persona_id") or "").strip()
            if pid:
                out["persona_id"] = pid

    # operation_ref
    op_ref = _extract_operation_ref_from_bindings(bindings)
    if op_ref:
        out.setdefault("operation_ref", op_ref)
        # legacy mirror
        out.setdefault("operation_digest", op_ref)

    # operation name resolution by reading operation card (SAID filename)
    op_name = (out.get("operation") or "").strip()
    if not op_name and op_ref:
        op_card, _ = load_operation_card_by_ref(ward_path, op_ref)
        op_name = _operation_name_from_card_raw(op_card)
        if op_name:
            out["operation"] = op_name

    return out


# ----------------------------
# Operation Card loading (SAID filename)
# ----------------------------

def load_operation_card_by_ref(ward_path: Path, operation_ref: str) -> Tuple[Dict[str, Any], Path]:
    """
    Loads:
      wards/<LOCAL_HANDLE>/operations/<SAID>.json

    operation_ref may be:
      - SAID (preferred)
      - sha256:<SAID> (tolerated)
    """
    ref_norm = _strip_sha256_prefix(operation_ref)
    if not ref_norm:
        raise RuntimeError("missing operation_ref (expected SAID or sha256:<...>)")

    op_path = ward_path / "operations" / f"{ref_norm}.json"
    if not op_path.exists():
        raise FileNotFoundError(f"operation card not found by ref: {op_path}")

    op = load_json(op_path)
    return op, op_path


def _required_receipt_fields(operation_card: Dict[str, Any]) -> List[str]:
    rs = operation_card.get("receipt_schema") or {}
    fields = rs.get("required_fields") or []
    if isinstance(fields, list):
        return [str(x) for x in fields]
    return []


# ----------------------------
# Receipt writing
# ----------------------------

def write_receipt(
    ward_path: Path,
    warrant_path: Path,
    payload: Dict[str, Any],
    operation_card: Optional[Dict[str, Any]],
    operation_card_path: Optional[Path],
    result_status: str,
    output_path: Optional[Path] = None,
    error: Optional[str] = None,
    warden_decision: Optional[Dict[str, Any]] = None,
) -> Path:
    """
    Writes a receipt for success/failed/denied outcomes.

    If operation_card is not available (e.g., denied before op load),
    receipt schema enforcement is skipped.
    """
    receipts_dir = ward_path / "receipts"
    receipts_dir.mkdir(exist_ok=True)

    warrant_hash = sha256_file(warrant_path) if warrant_path.exists() else None
    op_hash = sha256_file(operation_card_path) if operation_card_path and operation_card_path.exists() else None

    manifest_path = ward_path / "manifest.json"
    manifest = load_json(manifest_path) if manifest_path.exists() else {}
    local_handle = manifest.get("local_handle")

    ward_ref = payload.get("ward_ref") or (warden_decision or {}).get("ward_ref")
    if not ward_ref:
        raise RuntimeError("missing ward_ref for receipt")

    op_ref = (payload.get("operation_ref") or payload.get("operation_digest") or "").strip()
    operation_name = (payload.get("operation") or "").strip()
    operation_label: Optional[str] = None

    if operation_card is not None:
        if not op_ref and operation_card_path is not None:
            op_ref = operation_card_path.stem
        if not operation_name:
            operation_name = _operation_name_from_card_raw(operation_card)
        operation_label = _operation_label_from_card_raw(operation_card) or None

    receipt_id = hashlib.sha256(
        f"{payload.get('warrant_id','') or warrant_path.stem}|{utc_now_iso()}".encode("utf-8")
    ).hexdigest()[:24]

    warden_receipt = (warden_decision or {}).get("execution_receipt")

    if isinstance(warden_receipt, dict):

        receipt = dict(warden_receipt)

        receipt["result"] = {
            "status": result_status,
            "output_path": str(output_path) if output_path else None,
            "error": error,
        }

    else:

        # fallback if warden didn't return receipt
        receipt = {
            "receipt_id": receipt_id,
            "created_at": utc_now_iso(),

            "warrant_sha256": warrant_hash,
            "operation_card_sha256": op_hash,

            "warden": {
                "verified_warrant": (warden_decision or {}).get("verified_warrant"),
                "verified_presence": (warden_decision or {}).get("verified_presence"),
                "warrant_consumed": (warden_decision or {}).get("warrant_consumed"),
                "burn_path": (warden_decision or {}).get("burn_path"),
            },

            "result": {
                "status": result_status,
                "output_path": str(output_path) if output_path else None,
                "error": error,
            },
        }

    if operation_card is not None:
        required = _required_receipt_fields(operation_card)
        missing = [f for f in required if receipt.get(f) in (None, "", [], {})]
        if missing:
            receipt["result"]["status"] = "receipt_schema_failed"
            receipt["result"]["error"] = (
                (receipt["result"].get("error") or "") +
                f" | missing required receipt fields: {', '.join(missing)}"
            ).strip(" |")
            receipt["receipt_schema_required_fields"] = required

    receipt_path = receipts_dir / f"{payload.get('warrant_id') or warrant_path.stem}.receipt.json"
    write_json(receipt_path, receipt)
    return receipt_path


# ----------------------------
# Dummy operation execution
# ----------------------------

def run_dummy_operation(ward_path: Path, payload: Dict[str, Any], operation_name: str) -> Path:
    """
    Minimal 'Agent operation': write a file inside Ward's data directory.
    Uses operation_name for human-friendly output file naming.
    """
    data_dir = ward_path / "data"
    data_dir.mkdir(exist_ok=True)

    op_name = (operation_name or "").strip()
    if not op_name:
        op_ref = (payload.get("operation_ref") or payload.get("operation_digest") or "").strip()
        raise RuntimeError(f"missing operation name (operation_ref={op_ref or 'EMPTY'})")

    out = data_dir / f"{op_name}.txt"
    out.write_text(
        "Operation executed under warrant:\n"
        f"- warrant_id:   {payload.get('warrant_id')}\n"
        f"- ward_ref:     {payload.get('ward_ref')}\n"
        f"- role_id:      {payload.get('role_id')}\n"
        f"- persona_id:   {payload.get('persona_id')}\n"
        f"- operation_ref:{payload.get('operation_ref') or payload.get('operation_digest')}\n"
        f"- operation:    {op_name}\n"
        f"- executed_at:  {utc_now_iso()}\n",
        encoding="utf-8",
    )
    return out


# ----------------------------
# CLI entrypoint
# ----------------------------

def _usage() -> str:
    return "Usage: python3 -m src.agent_operation <LOCAL_HANDLE> <WARRANT_ID>"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(_usage())

    LOCAL_HANDLE = sys.argv[1]
    WARRANT_ID = sys.argv[2]

    ward_path = Path("wards") / LOCAL_HANDLE
    warrant_path = ward_path / "warrants" / f"{WARRANT_ID}.json"

    if not warrant_path.exists():
        raise SystemExit(f"Warrant not found: {warrant_path}")

    # Preload payload for receipt context (even if warden denies)
    raw_token = load_json(warrant_path)
    pre_payload = _token_payload(raw_token)

    # Best-effort: load op card pre-admission so denial receipts can include op metadata
    pre_payload_norm = normalize_payload_for_agent(ward_path, pre_payload)

    operation_card: Optional[Dict[str, Any]] = None
    operation_card_path: Optional[Path] = None
    operation_name: str = ""

    try:
        op_ref_pre = (pre_payload_norm.get("operation_ref") or pre_payload_norm.get("operation_digest") or "").strip()
        if op_ref_pre:
            operation_card, operation_card_path = load_operation_card_by_ref(ward_path, op_ref_pre)
            operation_name = (pre_payload_norm.get("operation") or "").strip() or _operation_name_from_card_raw(operation_card)
    except Exception:
        operation_card, operation_card_path, operation_name = None, None, ""

    # 1) Warden boundary (STRICT single-use happens here)
    ok, res = warden_admit(
        ward_path=ward_path,
        warrant_path=warrant_path,
        proposed_operation=None,
        presence_max_age_seconds=300,
    )

    # Warden returns signed_view(payload) for UI compatibility; we normalize for agent convenience
    payload = res.get("payload") if isinstance(res, dict) else None
    if not isinstance(payload, dict):
        payload = pre_payload
    payload = normalize_payload_for_agent(ward_path, payload)

    if not ok:
        reason = res.get("reason") if isinstance(res, dict) else safe_str(res)

        # Write a denial receipt (warrant is consumed at the warden boundary)
        try:
            rp = write_receipt(
                ward_path=ward_path,
                warrant_path=warrant_path,
                payload=payload,
                operation_card=operation_card,
                operation_card_path=operation_card_path,
                result_status="denied",
                output_path=None,
                error=reason,
                warden_decision=res if isinstance(res, dict) else None,
            )
            print("DENIED at warden boundary (receipt written)")
            print(f"Reason:  {reason}")
            print(f"Receipt: {rp.resolve()}")
        except Exception:
            pass

        raise SystemExit(f"DENIED: {reason}")

    # 2) Load Operation Card (required for execution)
    op_ref = (payload.get("operation_ref") or payload.get("operation_digest") or "").strip()
    if not op_ref:
        reason = "EXECUTION FAILED: missing operation_ref (expected in bindings.operation_ref/operation_said/operation_digest)"
        try:
            rp = write_receipt(
                ward_path=ward_path,
                warrant_path=warrant_path,
                payload=payload,
                operation_card=operation_card,
                operation_card_path=operation_card_path,
                result_status="failed",
                output_path=None,
                error=reason,
                warden_decision=res if isinstance(res, dict) else None,
            )
            print("FAILED under warrant (receipt written)")
            print(f"Receipt: {rp.resolve()}")
        except Exception:
            pass
        raise SystemExit(reason)

    if operation_card is None or operation_card_path is None:
        operation_card, operation_card_path = load_operation_card_by_ref(ward_path, op_ref)

    operation_name = (payload.get("operation") or "").strip() or _operation_name_from_card_raw(operation_card)
    if not operation_name:
        reason = f"EXECUTION FAILED: could not derive operation name from Operation Card (operation_ref={op_ref})"
        try:
            rp = write_receipt(
                ward_path=ward_path,
                warrant_path=warrant_path,
                payload=payload,
                operation_card=operation_card,
                operation_card_path=operation_card_path,
                result_status="failed",
                output_path=None,
                error=reason,
                warden_decision=res if isinstance(res, dict) else None,
            )
            print("FAILED under warrant (receipt written)")
            print(f"Receipt: {rp.resolve()}")
        except Exception:
            pass
        raise SystemExit(reason)

    # 3) Execute + receipt
    # NOTE: do NOT burn the warrant here; it was consumed atomically at warden_admit().
    output_path: Optional[Path] = None
    receipt_path: Optional[Path] = None

    try:
        output_path = run_dummy_operation(ward_path, payload, operation_name)

        receipt_path = write_receipt(
            ward_path=ward_path,
            warrant_path=warrant_path,
            payload=payload,
            operation_card=operation_card,
            operation_card_path=operation_card_path,
            result_status="success",
            output_path=output_path,
            error=None,
            warden_decision=res if isinstance(res, dict) else None,
        )

        print("EXECUTED under warrant")
        print(f"Operation: {operation_name}")
        print(f"Output:    {output_path.resolve()}")
        print(f"Receipt:   {receipt_path.resolve()}")
        print("NOTE: Warrant consumption occurred at the warden boundary (strict single-use)")

    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        tb = traceback.format_exc()

        try:
            receipt_path = write_receipt(
                ward_path=ward_path,
                warrant_path=warrant_path,
                payload=payload,
                operation_card=operation_card,
                operation_card_path=operation_card_path,
                result_status="failed",
                output_path=output_path,
                error=f"{err}\n{tb}",
                warden_decision=res if isinstance(res, dict) else None,
            )
            print("FAILED under warrant (receipt written)")
            print(f"Receipt: {receipt_path.resolve()}")
        except Exception:
            pass

        raise SystemExit(f"EXECUTION FAILED: {err}")

