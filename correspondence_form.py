# src/correspondence_form.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from src.digests import canonical_digest_sha256


# ============================================================
# Correspondence Form v1 (MVP) — STRICT RELATIONSHIP MAPPING
# ============================================================
#
# Purpose:
# - Declare a durable correspondence between:
#     Role Card
#     Persona Card
#     Action Intent
#
# - Used as a mint-time selector and integrity anchor
# - NOT an execution artifact
# - NOT a permission grant
#
# Storage:
#   wards/<WARD_HANDLE>/correspondences/<correspondence_id>.json
#
# Strict mapping required by warrant_mint:
#   relationships.role.role_id
#   relationships.persona.persona_id
#   relationships.action_intent.action_intent_id
#
# Digest:
# - Computed from the Canonical Digest Payload (Final)
# - sha256 via canonical_digest_sha256(...)
#
# Transitional bridge (IMPORTANT):
# - We add a 'd' field (SAID-like handle) set equal to correspondence_digest.
# - This lets the rest of the workbench treat correspondences like the other
#   SAID-addressed artifacts without changing filename conventions yet.
#


# ----------------------------
# Types
# ----------------------------

@dataclass(frozen=True)
class CorrespondenceFormV1:
    raw: Dict[str, Any]

    @property
    def schema(self) -> str:
        return str(self.raw.get("schema") or "")

    @property
    def schema_version(self) -> str:
        return str(self.raw.get("schema_version") or "")

    @property
    def correspondence_id(self) -> str:
        return str(self.raw.get("correspondence_id") or "")

    @property
    def correspondence_digest(self) -> str:
        return str(self.raw.get("correspondence_digest") or "")

    @property
    def d(self) -> str:
        # Transitional SAID-like handle (bridge): d == correspondence_digest
        return str(self.raw.get("d") or "")

    @property
    def ward_ref(self) -> str:
        return str(self.raw.get("ward_ref") or "")

    # --- STRICT: relationships object
    @property
    def relationships(self) -> Dict[str, Any]:
        rel = self.raw.get("relationships") or {}
        return rel if isinstance(rel, dict) else {}

    @property
    def role_relationship(self) -> Dict[str, Any]:
        r = self.relationships.get("role") or {}
        return r if isinstance(r, dict) else {}

    @property
    def persona_relationship(self) -> Dict[str, Any]:
        p = self.relationships.get("persona") or {}
        return p if isinstance(p, dict) else {}

    @property
    def action_intent_relationship(self) -> Dict[str, Any]:
        ai = self.relationships.get("action_intent") or {}
        return ai if isinstance(ai, dict) else {}

    @property
    def role_id(self) -> str:
        return str(self.role_relationship.get("role_id") or "")

    @property
    def persona_id(self) -> str:
        return str(self.persona_relationship.get("persona_id") or "")

    @property
    def action_intent_id(self) -> str:
        return str(self.action_intent_relationship.get("action_intent_id") or "")

    @property
    def created_at(self) -> str:
        return str(self.raw.get("created_at") or "")

    @property
    def created_by(self) -> str:
        return str(self.raw.get("created_by") or "")

    @property
    def status(self) -> str:
        # optional but useful for UI
        return str(self.raw.get("status") or "")


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: List[str]


# ----------------------------
# IO helpers
# ----------------------------

def read_json(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(p: Path, obj: Dict[str, Any]) -> None:
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def correspondence_path(ward_path: Path, correspondence_id: str) -> Path:
    return ward_path / "correspondences" / f"{correspondence_id}.json"


def load_correspondence(ward_path: Path, correspondence_id: str) -> CorrespondenceFormV1:
    p = correspondence_path(ward_path, correspondence_id)
    if not p.exists():
        raise FileNotFoundError(f"Correspondence Form not found: {p}")
    return CorrespondenceFormV1(read_json(p))


# ----------------------------
# Validation
# ----------------------------

def _is_str(x: Any) -> bool:
    return isinstance(x, str) and bool(x.strip())


def validate_correspondence_form_v1(cf: CorrespondenceFormV1) -> ValidationResult:
    errs: List[str] = []
    raw = cf.raw

    # schema
    if cf.schema != "rbc.correspondence_form":
        errs.append("schema must be 'rbc.correspondence_form'")
    if cf.schema_version != "1.0":
        errs.append("schema_version must be '1.0'")

    # identity
    if not _is_str(cf.correspondence_id):
        errs.append("correspondence_id must be a non-empty string")
    if not _is_str(cf.ward_ref):
        errs.append("ward_ref must be a non-empty string")

    # relationships (STRICT)
    rel = raw.get("relationships")
    if not isinstance(rel, dict):
        errs.append("relationships must be an object")
        rel = {}

    role_rel = rel.get("role")
    if not isinstance(role_rel, dict):
        errs.append("relationships.role must be an object")
        role_rel = {}
    if not _is_str(role_rel.get("role_id")):
        errs.append("relationships.role.role_id must be a non-empty string")

    persona_rel = rel.get("persona")
    if not isinstance(persona_rel, dict):
        errs.append("relationships.persona must be an object")
        persona_rel = {}
    if not _is_str(persona_rel.get("persona_id")):
        errs.append("relationships.persona.persona_id must be a non-empty string")

    ai_rel = rel.get("action_intent")
    if not isinstance(ai_rel, dict):
        errs.append("relationships.action_intent must be an object")
        ai_rel = {}
    if not _is_str(ai_rel.get("action_intent_id")):
        errs.append("relationships.action_intent.action_intent_id must be a non-empty string")

    # created fields
    if not _is_str(raw.get("created_at")):
        errs.append("created_at must be a non-empty string")
    if not _is_str(raw.get("created_by")):
        errs.append("created_by must be a non-empty string")

    # optional status (do not fail closed; keep permissive for MVP)
    st = (raw.get("status") or "").strip()
    if st and st not in {"active", "revoked", "superseded"}:
        errs.append("status must be one of: active, revoked, superseded (or omitted)")

    # digest check (if present)
    embedded = (cf.correspondence_digest or "").strip()
    if embedded:
        computed = canonical_correspondence_digest_v1(cf)
        if embedded != computed:
            errs.append("correspondence_digest mismatch")

    # Transitional bridge: if d is present it must match the embedded digest
    dval = (raw.get("d") or "").strip()
    if dval and embedded and dval != embedded:
        errs.append("d must match correspondence_digest when present")

    return ValidationResult(ok=(len(errs) == 0), errors=errs)


# ----------------------------
# Canonical digest (LOCKED)
# ----------------------------

def canonical_correspondence_digest_payload_v1(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Canonical Digest Payload (Final) for Correspondence Form v1.0.

    We lock the digest over the strict relationship mapping + identity anchors:
      {schema, schema_version, correspondence_id, ward_ref, relationships}

    Note:
    - Exclude storage-only fields like correspondence_digest itself.
    - Include status only if you want it to be digest-bound; for MVP we exclude it.
    - Exclude 'd' (bridge field), which must mirror correspondence_digest.
    """
    schema = str(raw.get("schema") or "")
    schema_version = str(raw.get("schema_version") or "")
    correspondence_id = str(raw.get("correspondence_id") or "")
    ward_ref = str(raw.get("ward_ref") or "")

    relationships = raw.get("relationships") or {}
    if not isinstance(relationships, dict):
        relationships = {}

    # Ensure only the strict subtrees are represented (defensive canonicalization)
    role_rel = relationships.get("role") or {}
    persona_rel = relationships.get("persona") or {}
    ai_rel = relationships.get("action_intent") or {}

    if not isinstance(role_rel, dict):
        role_rel = {}
    if not isinstance(persona_rel, dict):
        persona_rel = {}
    if not isinstance(ai_rel, dict):
        ai_rel = {}

    canonical_relationships = {
        "role": {
            "role_id": str(role_rel.get("role_id") or ""),
        },
        "persona": {
            "persona_id": str(persona_rel.get("persona_id") or ""),
        },
        "action_intent": {
            "action_intent_id": str(ai_rel.get("action_intent_id") or ""),
        },
    }

    return {
        "schema": schema,
        "schema_version": schema_version,
        "correspondence_id": correspondence_id,
        "ward_ref": ward_ref,
        "relationships": canonical_relationships,
    }


def canonical_correspondence_digest_v1(cf: CorrespondenceFormV1) -> str:
    payload = canonical_correspondence_digest_payload_v1(cf.raw)
    return canonical_digest_sha256(payload, exclude_keys=[])


# ----------------------------
# Builder
# ----------------------------

def build_correspondence_form_v1(
    *,
    correspondence_id: str,
    ward_ref: str,
    role_id: str,
    persona_id: str,
    action_intent_id: str,
    created_at: str,
    created_by: str,
    status: str = "active",
) -> Dict[str, Any]:
    """
    Build a v1.0 Correspondence Form with the STRICT relationship mapping expected by warrant_mint.

    NOTE:
    - role/persona/action_intent digests are intentionally not required here.
      The Warden/mint layer should validate and digest-bind the canonical artifacts themselves.
    - action_intent_id may be a UUID (legacy) OR a sha256:... handle (bridge),
      as long as warrant_mint/correspondence_create can resolve it.
    """
    base: Dict[str, Any] = {
        "schema": "rbc.correspondence_form",
        "schema_version": "1.0",
        "status": (status or "active"),

        "correspondence_id": (correspondence_id or "").strip(),
        "correspondence_digest": "",

        "ward_ref": (ward_ref or "").strip(),

        # STRICT relationship mapping (authoritative)
        "relationships": {
            "role": {"role_id": (role_id or "").strip()},
            "persona": {"persona_id": (persona_id or "").strip()},
            "action_intent": {"action_intent_id": (action_intent_id or "").strip()},
        },

        "created_at": (created_at or "").strip(),
        "created_by": (created_by or "").strip(),
    }

    tmp = CorrespondenceFormV1(base)
    base["correspondence_digest"] = canonical_correspondence_digest_v1(tmp)

    # Transitional bridge: populate SAID-like handle
    base["d"] = base["correspondence_digest"]

    return base

