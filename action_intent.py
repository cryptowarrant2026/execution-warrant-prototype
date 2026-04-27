from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from src.digests import canonical_digest_sha256


# ============================================================
# Action Intent v1 (MVP) — Full replacement, mint-safe
# ============================================================
#
# Key points:
# - Stored Action Intent includes metadata (status, created_at, created_by).
# - context_binding_digest is a convenience field computed from context_binding.
# - action_intent_digest MUST be computed from the *canonical payload* that the
#   minting + enforcement plane recomputes.
#
# Canonical digest payload (v1.0, MVP):
#   REQUIRED:
#     schema, schema_version, action_intent_id, operation_digest,
#     targets, context_binding, parameters, scope
#   PLUS (when present):
#     context_binding_digest
#
# Excluded from digest:
#   status, created_at, created_by, action_intent_digest (self-referential), d
#
# Storage (MVP):
#   wards/<WARD_HANDLE>/action_intents/<action_intent_id>.json
#
# Transitional bridge (IMPORTANT):
# - We add a 'd' field (SAID-like handle) set equal to action_intent_digest.
# - This lets the rest of the workbench treat Action Intents like the other
#   SAID-addressed artifacts without changing filename conventions yet.
#
# IMPORTANT MVP CHANGE (2026-01):
# - operation_digest is treated as a SAID reference to an Operation Card (its "d"),
#   NOT as an algorithm-qualified digest string.
# - Therefore we DO NOT enforce a "sha256:" prefix for operation_digest.
#   (Operation Cards are stored as <SAID>.json and their SAID has no prefix.)
# ============================================================


# ----------------------------
# Types
# ----------------------------

@dataclass(frozen=True)
class ActionIntentV1:
    raw: Dict[str, Any]

    @property
    def schema(self) -> str:
        return str(self.raw.get("schema") or "")

    @property
    def schema_version(self) -> str:
        return str(self.raw.get("schema_version") or "")

    @property
    def status(self) -> str:
        return str(self.raw.get("status") or "")

    @property
    def action_intent_id(self) -> str:
        return str(self.raw.get("action_intent_id") or "")

    @property
    def action_intent_digest(self) -> str:
        return str(self.raw.get("action_intent_digest") or "")

    @property
    def d(self) -> str:
        # Transitional SAID-like handle (bridge): d == action_intent_digest
        return str(self.raw.get("d") or "")

    @property
    def operation_digest(self) -> str:
        # MVP: this is an Operation Card SAID ("d"), not "sha256:..."
        return str(self.raw.get("operation_digest") or "")

    @property
    def targets(self) -> List[Dict[str, Any]]:
        t = self.raw.get("targets") or []
        return t if isinstance(t, list) else []

    @property
    def context_binding(self) -> Dict[str, Any]:
        cb = self.raw.get("context_binding") or {}
        return cb if isinstance(cb, dict) else {}

    @property
    def context_binding_digest(self) -> str:
        return str(self.raw.get("context_binding_digest") or "")

    @property
    def parameters(self) -> Dict[str, Any]:
        p = self.raw.get("parameters") or {}
        return p if isinstance(p, dict) else {}

    @property
    def scope(self) -> Dict[str, Any]:
        s = self.raw.get("scope") or {}
        return s if isinstance(s, dict) else {}

    @property
    def created_at(self) -> str:
        return str(self.raw.get("created_at") or "")

    @property
    def created_by(self) -> str:
        return str(self.raw.get("created_by") or "")


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: List[str]


# ----------------------------
# IO
# ----------------------------

def read_json(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(p: Path, obj: Dict[str, Any]) -> None:
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def action_intent_path(ward_path: Path, action_intent_id: str) -> Path:
    return ward_path / "action_intents" / f"{action_intent_id}.json"


def load_action_intent(ward_path: Path, action_intent_id: str) -> ActionIntentV1:
    p = action_intent_path(ward_path, action_intent_id)
    if not p.exists():
        raise FileNotFoundError(f"action_intent not found: {p}")
    raw = read_json(p)
    return ActionIntentV1(raw)


def resolve_action_intent_id_or_d_to_intent(
    ward_path: Path, action_intent_id_or_d: str
) -> ActionIntentV1:
    """
    MVP bridge helper: accepts either:
      - action_intent_id (filename stem), OR
      - d / action_intent_digest (SAID-like handle for action intent)
    and returns the resolved ActionIntentV1.

    This allows downstream flows to carry around 'd' like other SAID-addressed
    artifacts without forcing a file layout migration today.
    """
    s = (action_intent_id_or_d or "").strip()
    if not s:
        raise ValueError("resolve_action_intent_id_or_d_to_intent: empty identifier")

    # Fast path: treat as action_intent_id (filename)
    p = action_intent_path(ward_path, s)
    if p.exists():
        return ActionIntentV1(read_json(p))

    # Slow path: scan action_intents for matching d / action_intent_digest
    intents_dir = ward_path / "action_intents"
    if not intents_dir.exists():
        raise FileNotFoundError(f"action_intents dir not found: {intents_dir}")

    for fp in intents_dir.glob("*.json"):
        try:
            raw = read_json(fp)
        except Exception:
            continue
        dval = str(raw.get("d") or "").strip()
        dig = str(raw.get("action_intent_digest") or "").strip()
        if s == dval or s == dig:
            return ActionIntentV1(raw)

    raise FileNotFoundError(
        f"action_intent not found by id-or-d: {s} (searched {intents_dir})"
    )


# ----------------------------
# Validation (strict enough for MVP demo)
# ----------------------------

def _is_str(x: Any) -> bool:
    return isinstance(x, str) and bool(x.strip())


def _is_list(x: Any) -> bool:
    return isinstance(x, list)


def _is_dict(x: Any) -> bool:
    return isinstance(x, dict)


def _is_int_or_none(x: Any) -> bool:
    return (x is None) or isinstance(x, int)


def _is_list_of_str(x: Any) -> bool:
    return isinstance(x, list) and all(isinstance(i, str) and i.strip() for i in x)


def validate_action_intent_v1(intent: ActionIntentV1) -> ValidationResult:
    errs: List[str] = []
    raw = intent.raw

    # schema + version
    if intent.schema != "rbc.action_intent":
        errs.append("schema must be 'rbc.action_intent'")
    if intent.schema_version != "1.0":
        errs.append("schema_version must be '1.0'")

    # status (stored artifact field)
    if intent.status not in {"proposed", "accepted", "rejected", "expired"}:
        errs.append("status must be one of: proposed, accepted, rejected, expired")

    # identity
    if not _is_str(intent.action_intent_id):
        errs.append("action_intent_id must be a non-empty string")

    # operation_digest (MVP: Operation Card SAID, no prefix enforcement)
    if not _is_str(intent.operation_digest):
        errs.append("operation_digest must be a non-empty string")
    # NOTE: intentionally no "sha256:" prefix requirement.

    # targets
    targets = raw.get("targets")
    if targets is None or not _is_list(targets) or len(targets) == 0:
        errs.append("targets must be a non-empty list")
    else:
        for i, t in enumerate(targets):
            if not _is_dict(t):
                errs.append(f"targets[{i}] must be an object")
                continue
            if not _is_str(t.get("target_kind")):
                errs.append(f"targets[{i}].target_kind must be a non-empty string")
            if not _is_str(t.get("target_ref")):
                errs.append(f"targets[{i}].target_ref must be a non-empty string")
            sel = t.get("selector")
            if sel is not None and not isinstance(sel, str):
                errs.append(f"targets[{i}].selector must be a string (or omitted)")

    # context_binding
    cb = raw.get("context_binding")
    if cb is None or not _is_dict(cb):
        errs.append("context_binding must be an object")
    else:
        if not _is_str(cb.get("zone_ref")):
            errs.append("context_binding.zone_ref must be a non-empty string")
        if "overlay_refs" in cb and cb["overlay_refs"] is not None and not _is_list_of_str(cb["overlay_refs"]):
            errs.append("context_binding.overlay_refs must be a list of strings")
        if "jurisdiction_ref" in cb and cb["jurisdiction_ref"] is not None and not isinstance(cb["jurisdiction_ref"], str):
            errs.append("context_binding.jurisdiction_ref must be a string (or omitted)")
        if not _is_str(cb.get("effective_time")):
            errs.append("context_binding.effective_time must be a non-empty string (ISO8601)")

    # context_binding_digest (optional stored convenience)
    # Keep current behavior: when present, require sha256: prefix (it's a digest string, not a SAID ref).
    cbd = (raw.get("context_binding_digest") or "").strip()
    if cbd and not cbd.startswith("sha256:"):
        errs.append("context_binding_digest must use 'sha256:' prefix when present")

    # parameters
    params = raw.get("parameters")
    if params is None or not _is_dict(params):
        errs.append("parameters must be an object (dict)")

    # scope
    scope = raw.get("scope")
    if scope is None or not _is_dict(scope):
        errs.append("scope must be an object")
    else:
        if not _is_int_or_none(scope.get("max_records")):
            errs.append("scope.max_records must be an int or null")
        if not _is_int_or_none(scope.get("time_window_seconds")):
            errs.append("scope.time_window_seconds must be an int or null")
        if "field_allowlist" in scope and scope["field_allowlist"] is not None and not _is_list_of_str(scope["field_allowlist"]):
            errs.append("scope.field_allowlist must be a list of strings")
        if "field_blocklist" in scope and scope["field_blocklist"] is not None and not _is_list_of_str(scope["field_blocklist"]):
            errs.append("scope.field_blocklist must be a list of strings")
        if "data_categories" in scope and scope["data_categories"] is not None and not _is_list_of_str(scope["data_categories"]):
            errs.append("scope.data_categories must be a list of strings")

    # created_at / created_by (stored artifact fields)
    if not _is_str(raw.get("created_at")):
        errs.append("created_at must be a non-empty string (ISO8601)")
    if not _is_str(raw.get("created_by")):
        errs.append("created_by must be a non-empty string")

    # digest check (if present, must match)
    embedded = (intent.action_intent_digest or "").strip()
    if embedded:
        computed = canonical_action_intent_digest_v1(intent)
        if embedded != computed:
            errs.append("action_intent_digest mismatch (embedded does not match canonical digest payload)")

    # Transitional bridge: if d is present it must match the embedded digest
    dval = (raw.get("d") or "").strip()
    if dval and embedded and dval != embedded:
        errs.append("d must match action_intent_digest when present")

    return ValidationResult(ok=(len(errs) == 0), errors=errs)


# ----------------------------
# Canonical digest helpers (LOCKED)
# ----------------------------

def canonical_action_intent_digest_payload_v1(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Canonical digest payload for Action Intent v1.0.

    This is what minting/enforcement should recompute.

    Includes context_binding_digest when present (binds the digest-of-context into the
    action intent identity), but excludes storage-only fields (status, created_*),
    plus excludes action_intent_digest itself and d.

    NOTE (MVP):
    - operation_digest is included as the Operation Card SAID reference string (no prefix).
    """
    schema = str(raw.get("schema") or "")
    schema_version = str(raw.get("schema_version") or "")
    action_intent_id = str(raw.get("action_intent_id") or "")
    operation_digest = str(raw.get("operation_digest") or "")

    targets = raw.get("targets") or []
    if not isinstance(targets, list):
        targets = []

    context_binding = raw.get("context_binding") or {}
    if not isinstance(context_binding, dict):
        context_binding = {}

    parameters = raw.get("parameters") or {}
    if not isinstance(parameters, dict):
        parameters = {}

    scope = raw.get("scope") or {}
    if not isinstance(scope, dict):
        scope = {}

    payload: Dict[str, Any] = {
        "schema": schema,
        "schema_version": schema_version,
        "action_intent_id": action_intent_id,
        "operation_digest": operation_digest,
        "targets": targets,
        "context_binding": context_binding,
        "parameters": parameters,
        "scope": scope,
    }

    # Bind context_binding_digest if present (recommended for mint/eval consistency)
    cbd = (raw.get("context_binding_digest") or "").strip()
    if cbd:
        payload["context_binding_digest"] = cbd

    return payload


def canonical_action_intent_digest_v1(intent: ActionIntentV1) -> str:
    """
    Compute canonical digest for Action Intent v1.0 using the locked payload.
    """
    if not isinstance(intent.raw, dict):
        raise TypeError("ActionIntentV1.raw must be a dict")
    payload = canonical_action_intent_digest_payload_v1(intent.raw)
    return canonical_digest_sha256(payload, exclude_keys=[])


def action_intent_digest_matches(intent: ActionIntentV1) -> bool:
    v = (intent.action_intent_digest or "").strip()
    return bool(v) and (v == canonical_action_intent_digest_v1(intent))


def canonical_context_binding_digest_from_dict(context_binding: Dict[str, Any]) -> str:
    """
    Compute canonical digest for context_binding only (stable).
    """
    if not isinstance(context_binding, dict):
        raise TypeError("context_binding must be a dict")
    return canonical_digest_sha256(context_binding, exclude_keys=[])


def canonical_context_binding_digest_v1(intent: ActionIntentV1) -> str:
    return canonical_context_binding_digest_from_dict(intent.context_binding)


# ----------------------------
# Builder (convenience)
# ----------------------------

def build_action_intent_v1(
    *,
    action_intent_id: str,
    operation_digest: str,
    targets: List[Dict[str, Any]],
    context_binding: Dict[str, Any],
    parameters: Dict[str, Any],
    scope: Dict[str, Any],
    created_at: str,
    created_by: str,
    status: str = "proposed",
) -> Dict[str, Any]:
    """
    Returns a dict shaped like the stored template, with:
      - context_binding_digest computed first
      - action_intent_digest computed from canonical digest payload (which includes
        context_binding_digest when present)
      - d set equal to action_intent_digest (transitional SAID-like handle)

    NOTE (MVP):
    - operation_digest is stored as the Operation Card SAID (no prefix).
    """
    base: Dict[str, Any] = {
        "schema": "rbc.action_intent",
        "schema_version": "1.0",
        "status": status,

        "action_intent_id": (action_intent_id or "").strip(),
        "operation_digest": (operation_digest or "").strip(),

        "targets": targets,
        "context_binding": context_binding,
        "parameters": parameters,
        "scope": scope,

        "created_at": created_at,
        "created_by": created_by,
    }

    # Compute context_binding_digest first (stored convenience, bound into intent digest)
    base["context_binding_digest"] = canonical_context_binding_digest_from_dict(context_binding)

    # Compute action_intent_digest over canonical payload (includes context_binding_digest)
    tmp = ActionIntentV1({**base, "action_intent_digest": "", "d": ""})
    base["action_intent_digest"] = canonical_action_intent_digest_v1(tmp)

    # Transitional bridge: populate SAID-like handle
    base["d"] = base["action_intent_digest"]

    return base

