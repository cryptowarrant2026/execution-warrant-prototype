# src/authority_policy.py
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.said_python import python_derivation_json, python_said_generate

# ============================================================
# Models
# ============================================================

@dataclass(frozen=True)
class AuthorityPolicyTemplateV1:
    """
    Authority Policy TEMPLATE (cca/governance/concept/1.0) with SAID in field 'd'.

    IMPORTANT:
      - Template MAY contain invariants (template-only).
      - Template MUST be non-authorizing (declarative admission envelope only).
      - DPV/OCV semantic validation is NOT done here (shape/type only).
    """
    raw: Dict[str, Any]

    @property
    def d(self) -> str: return str(self.raw.get("d") or "")
    @property
    def type(self) -> str: return str(self.raw.get("type") or "")
    @property
    def card_type(self) -> str: return str(self.raw.get("card_type") or "")
    @property
    def status(self) -> str: return str(self.raw.get("status") or "")

    @property
    def policy(self) -> Dict[str, Any]:
        p = self.raw.get("policy")
        return p if isinstance(p, dict) else {}

    @property
    def constraint_grammars(self) -> Dict[str, Any]:
        cg = self.raw.get("constraint_grammars")
        return cg if isinstance(cg, dict) else {}

    @property
    def privacy_grammar_ref(self) -> str:
        return str(self.constraint_grammars.get("privacy_grammar_ref") or "")

    @property
    def execution_grammar_ref(self) -> str:
        return str(self.constraint_grammars.get("execution_grammar_ref") or "")

    @property
    def invariants(self) -> Dict[str, Any]:
        inv = self.raw.get("invariants")
        return inv if isinstance(inv, dict) else {}


@dataclass(frozen=True)
class AuthorityPolicyV1:
    """
    Authority Policy INSTANCE (cca/governance/concept/1.0) with SAID in field 'd'.

    IMPORTANT:
      - invariants MUST NOT be present in the instance (template-only rule).
      - No implied bindings to external principals. Authority only.
    """
    raw: Dict[str, Any]

    @property
    def d(self) -> str: return str(self.raw.get("d") or "")
    @property
    def type(self) -> str: return str(self.raw.get("type") or "")
    @property
    def card_type(self) -> str: return str(self.raw.get("card_type") or "")
    @property
    def status(self) -> str: return str(self.raw.get("status") or "")
    @property
    def card_template_ref(self) -> str: return str(self.raw.get("card_template_ref") or "")

    @property
    def policy(self) -> Dict[str, Any]:
        p = self.raw.get("policy")
        return p if isinstance(p, dict) else {}

    @property
    def policy_name(self) -> str: return str(self.policy.get("name") or "")
    @property
    def policy_label(self) -> str: return str(self.policy.get("label") or "")
    @property
    def policy_description(self) -> str: return str(self.policy.get("description") or "")

    @property
    def constraint_grammars(self) -> Dict[str, Any]:
        cg = self.raw.get("constraint_grammars")
        return cg if isinstance(cg, dict) else {}

    @property
    def privacy_grammar_ref(self) -> str:
        return str(self.constraint_grammars.get("privacy_grammar_ref") or "")

    @property
    def execution_grammar_ref(self) -> str:
        return str(self.constraint_grammars.get("execution_grammar_ref") or "")


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: List[str]


# ============================================================
# IO helpers
# ============================================================

def read_json(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(p: Path, obj: Dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def project_root() -> Path:
    """
    Assumes repo layout like:
      rbc-mvp/
        src/
          authority_policy.py
        templates/
          authority_policy_v1_template.json
    """
    return Path(__file__).resolve().parents[1]


def default_authority_policy_template_path() -> Path:
    return project_root() / "templates" / "sources" / "authority_policy_v1_template.json"


# ============================================================
# Ward-scoped storage
# ============================================================

def authority_policies_dir(ward_path: Path) -> Path:
    return ward_path / "authority_policies"


def authority_policy_path(ward_path: Path, policy_said: str) -> Path:
    return authority_policies_dir(ward_path) / f"{policy_said}.json"


def authority_policy_path_legacy_by_name(ward_path: Path, policy_name: str) -> Path:
    # Legacy convenience; prefer SAID-addressed files
    return authority_policies_dir(ward_path) / f"{policy_name}.json"


def load_authority_policy(ward_path: Path, policy_name_or_said: str) -> AuthorityPolicyV1:
    """
    Preferred:
      wards/<WARD>/authority_policies/<SAID>.json
    Legacy fallback:
      wards/<WARD>/authority_policies/<policy_name>.json
    """
    p = authority_policy_path(ward_path, policy_name_or_said)
    if not p.exists():
        p = authority_policy_path_legacy_by_name(ward_path, policy_name_or_said)
    if not p.exists():
        raise FileNotFoundError(f"authority policy not found: {p}")
    return AuthorityPolicyV1(read_json(p))


def load_authority_policy_template(template_path: Optional[Path] = None) -> AuthorityPolicyTemplateV1:
    """
    Load the authority policy template from a *global* templates location (not ward storage).
    """
    p = template_path or default_authority_policy_template_path()
    if not p.exists():
        raise FileNotFoundError(f"authority policy template not found: {p}")
    return AuthorityPolicyTemplateV1(read_json(p))


# ============================================================
# SAID helpers (d-field)
# ============================================================

def compute_authority_policy_said_v1(raw: Dict[str, Any]) -> str:
    """
    Compute SAID for template or instance by canonical derivation (via said_python).
    """
    saided = python_said_generate(raw)
    return str(saided.get("d") or "")


def embed_said(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Return a new object with 'd' correctly embedded."""
    return python_said_generate(raw)


def authority_policy_said_matches(raw: Dict[str, Any]) -> bool:
    """Verify binding by recomputing SAID and comparing to raw['d']."""
    d = str(raw.get("d") or "").strip()
    if not d:
        return False
    return d == compute_authority_policy_said_v1(raw)


def authority_policy_derivation_json(raw: Dict[str, Any]) -> str:
    """Return the exact canonical JSON string hashed (debug/conformance)."""
    return python_derivation_json(raw)


# ============================================================
# Validation helpers (binding-agnostic)
# ============================================================

_ALLOWED_STATUS = {"active", "disabled", "deprecated"}
_ALLOWED_TYPE = {"cca/governance/concept/1.0"}
_ALLOWED_TEMPLATE_CARD_TYPE = {"authority_policy_template"}
_ALLOWED_INSTANCE_CARD_TYPE = {"authority_policy"}

_REQUIRED_TOP_LEVEL_OBJECTS = (
    "policy",
    "constraint_grammars",
    "trigger",
    "required_context",
    "constraints",
    "authority_envelope",
    "termination",
    "explicit_non_goals",
    "alignment_notes",
)

def _is_str(x: Any) -> bool:
    return isinstance(x, str) and bool(x.strip())


def _is_int_or_none(x: Any) -> bool:
    return x is None or (isinstance(x, int) and not isinstance(x, bool))


def _is_bool_or_none(x: Any) -> bool:
    return x is None or isinstance(x, bool)


def _require_obj(raw: Dict[str, Any], key: str, errs: List[str]) -> None:
    if not isinstance(raw.get(key), dict):
        errs.append(f"{key} must be an object")


def _require_list(v: Any, path: str, errs: List[str]) -> None:
    if not isinstance(v, list):
        errs.append(f"{path} must be a list (use [] if empty)")


def _require_int_or_none(v: Any, path: str, errs: List[str]) -> None:
    if not _is_int_or_none(v):
        errs.append(f"{path} must be an integer or null")


def _require_bool_or_none(v: Any, path: str, errs: List[str]) -> None:
    if not _is_bool_or_none(v):
        errs.append(f"{path} must be boolean or null")


def _ensure_policy_shape(p: Dict[str, Any], errs: List[str]) -> None:
    for k in ("name", "label", "description"):
        if k not in p:
            errs.append(f"policy.{k} required (use '' if unset)")
        elif not isinstance(p.get(k), str):
            errs.append(f"policy.{k} must be a string")


def _ensure_constraint_grammars_shape(cg: Dict[str, Any], errs: List[str]) -> None:
    for k in ("privacy_grammar_ref", "execution_grammar_ref"):
        if k not in cg:
            errs.append(f"constraint_grammars.{k} required (use '' if unset)")
        elif not isinstance(cg.get(k), str):
            errs.append(f"constraint_grammars.{k} must be a string")


def _ensure_trigger_shape(t: Dict[str, Any], errs: List[str]) -> None:
    for k in ("role_refs", "persona_refs", "operation_refs", "target_kind_allowlist"):
        if k not in t:
            errs.append(f"trigger.{k} required (use [] if empty)")
        else:
            _require_list(t.get(k), f"trigger.{k}", errs)

    if "max_targets" not in t:
        errs.append("trigger.max_targets required (use null if unset)")
    else:
        _require_int_or_none(t.get("max_targets"), "trigger.max_targets", errs)


def _ensure_required_context_shape(rc: Dict[str, Any], errs: List[str]) -> None:
    for k in ("zone_refs", "overlay_refs", "jurisdiction_refs"):
        if k not in rc:
            errs.append(f"required_context.{k} required (use [] if empty)")
        else:
            _require_list(rc.get(k), f"required_context.{k}", errs)

    if "effective_time_max_skew_seconds" not in rc:
        errs.append("required_context.effective_time_max_skew_seconds required (use null if unset)")
    else:
        _require_int_or_none(
            rc.get("effective_time_max_skew_seconds"),
            "required_context.effective_time_max_skew_seconds",
            errs,
        )


def _ensure_constraints_shape(c: Dict[str, Any], errs: List[str]) -> None:
    dpv = c.get("dpv")
    ocv = c.get("ocv")

    if not isinstance(dpv, dict):
        errs.append("constraints.dpv must be an object")
    else:
        for k in ("purpose", "processing_prohibitions", "recipients_allow", "data_categories_allow", "safeguards"):
            if k not in dpv:
                errs.append(f"constraints.dpv.{k} required (use [] if empty)")
            else:
                _require_list(dpv.get(k), f"constraints.dpv.{k}", errs)

    if not isinstance(ocv, dict):
        errs.append("constraints.ocv must be an object")
    else:
        for k in ("execution_posture", "side_effects", "human_gates", "time", "scope", "safety", "lifecycle"):
            if k not in ocv:
                errs.append(f"constraints.ocv.{k} required (use [] if empty)")
            else:
                _require_list(ocv.get(k), f"constraints.ocv.{k}", errs)


def _ensure_authority_envelope_shape(ae: Dict[str, Any], errs: List[str]) -> None:
    for k in ("ttl_seconds_default", "ttl_seconds_max"):
        if k not in ae:
            errs.append(f"authority_envelope.{k} required (use null if unset)")
        else:
            _require_int_or_none(ae.get(k), f"authority_envelope.{k}", errs)

    sd = ae.get("scope_defaults")
    sm = ae.get("scope_max")

    if not isinstance(sd, dict):
        errs.append("authority_envelope.scope_defaults must be an object")
    else:
        for k in ("max_records", "time_window_seconds"):
            if k not in sd:
                errs.append(f"authority_envelope.scope_defaults.{k} required (use null if unset)")
            else:
                _require_int_or_none(sd.get(k), f"authority_envelope.scope_defaults.{k}", errs)

        for k in ("field_allowlist", "field_blocklist"):
            if k not in sd:
                errs.append(f"authority_envelope.scope_defaults.{k} required (use [] if empty)")
            else:
                _require_list(sd.get(k), f"authority_envelope.scope_defaults.{k}", errs)

    if not isinstance(sm, dict):
        errs.append("authority_envelope.scope_max must be an object")
    else:
        for k in ("max_records", "time_window_seconds"):
            if k not in sm:
                errs.append(f"authority_envelope.scope_max.{k} required (use null if unset)")
            else:
                _require_int_or_none(sm.get(k), f"authority_envelope.scope_max.{k}", errs)


def _ensure_termination_shape(t: Dict[str, Any], errs: List[str]) -> None:
    for k in ("single_execution", "auto_terminate_on_completion", "invalidate_on_context_change"):
        if k not in t:
            errs.append(f"termination.{k} required (use null if unset)")
        else:
            _require_bool_or_none(t.get(k), f"termination.{k}", errs)


def _ensure_alignment_notes_shape(an: Dict[str, Any], errs: List[str]) -> None:
    for k in ("role_alignment", "persona_alignment", "operation_alignment", "authority_posture"):
        if k not in an:
            errs.append(f"alignment_notes.{k} required (use '' if unset)")
        elif not isinstance(an.get(k), str):
            errs.append(f"alignment_notes.{k} must be a string")


# ============================================================
# Template validation
# ============================================================

def validate_authority_policy_template_v1(template: AuthorityPolicyTemplateV1, strict_said: bool = True) -> ValidationResult:
    errs: List[str] = []
    raw = template.raw

    if template.type not in _ALLOWED_TYPE:
        errs.append("type must be 'cca/governance/concept/1.0'")

    if template.card_type not in _ALLOWED_TEMPLATE_CARD_TYPE:
        errs.append("card_type must be 'authority_policy_template'")

    if template.status not in _ALLOWED_STATUS:
        errs.append("status must be one of: active, disabled, deprecated")

    if not _is_str(template.d):
        errs.append("d required (SAID)")

    for k in _REQUIRED_TOP_LEVEL_OBJECTS:
        if k == "explicit_non_goals":
            if raw.get(k) is None:
                errs.append("explicit_non_goals required (use [] if empty)")
            elif not isinstance(raw.get(k), list):
                errs.append("explicit_non_goals must be a list")
        else:
            _require_obj(raw, k, errs)

    if isinstance(raw.get("policy"), dict):
        _ensure_policy_shape(raw["policy"], errs)
    if isinstance(raw.get("constraint_grammars"), dict):
        _ensure_constraint_grammars_shape(raw["constraint_grammars"], errs)
    if isinstance(raw.get("trigger"), dict):
        _ensure_trigger_shape(raw["trigger"], errs)
    if isinstance(raw.get("required_context"), dict):
        _ensure_required_context_shape(raw["required_context"], errs)
    if isinstance(raw.get("constraints"), dict):
        _ensure_constraints_shape(raw["constraints"], errs)
    if isinstance(raw.get("authority_envelope"), dict):
        _ensure_authority_envelope_shape(raw["authority_envelope"], errs)
    if isinstance(raw.get("termination"), dict):
        _ensure_termination_shape(raw["termination"], errs)
    if isinstance(raw.get("alignment_notes"), dict):
        _ensure_alignment_notes_shape(raw["alignment_notes"], errs)

    inv = raw.get("invariants")
    if not isinstance(inv, dict):
        errs.append("invariants must be an object (template-only)")
    else:
        required_bool_keys = (
            "non_authorizing",
            "deterministic_evaluable",
            "deny_by_default",
            "unknown_terms_denied",
            "template_fields_must_not_grant_authority",
        )
        for k in required_bool_keys:
            if k not in inv:
                errs.append(f"invariants.{k} required")
            elif not isinstance(inv.get(k), bool):
                errs.append(f"invariants.{k} must be boolean")

        if inv.get("non_authorizing") is not True:
            errs.append("invariants.non_authorizing must be true")
        if inv.get("deterministic_evaluable") is not True:
            errs.append("invariants.deterministic_evaluable must be true")
        if inv.get("deny_by_default") is not True:
            errs.append("invariants.deny_by_default must be true")

    if strict_said and _is_str(template.d):
        expected = compute_authority_policy_said_v1(raw)
        if template.d != expected:
            errs.append("d mismatch (SAID does not bind to derivation bytes)")

    return ValidationResult(ok=(len(errs) == 0), errors=errs)


# ============================================================
# Instance validation (binding-agnostic; NO invariants)
# ============================================================

def validate_authority_policy_v1(
    policy: AuthorityPolicyV1,
    template: Optional[AuthorityPolicyTemplateV1] = None,
    strict_said: bool = True,
) -> ValidationResult:
    errs: List[str] = []
    raw = policy.raw

    if policy.type not in _ALLOWED_TYPE:
        errs.append("type must be 'cca/governance/concept/1.0'")

    if policy.card_type not in _ALLOWED_INSTANCE_CARD_TYPE:
        errs.append("card_type must be 'authority_policy'")

    if policy.status not in _ALLOWED_STATUS:
        errs.append("status must be one of: active, disabled, deprecated")

    if not _is_str(policy.d):
        errs.append("d required (SAID)")

    if not _is_str(policy.card_template_ref):
        errs.append("card_template_ref required (Authority Policy Template SAID)")

    if "invariants" in raw:
        errs.append("invariants must not be present in Authority Policy instance (template-only)")

    for k in _REQUIRED_TOP_LEVEL_OBJECTS:
        if k == "explicit_non_goals":
            if raw.get(k) is None:
                errs.append("explicit_non_goals required (use [] if empty)")
            elif not isinstance(raw.get(k), list):
                errs.append("explicit_non_goals must be a list")
        else:
            _require_obj(raw, k, errs)

    if isinstance(raw.get("policy"), dict):
        _ensure_policy_shape(raw["policy"], errs)
    if isinstance(raw.get("constraint_grammars"), dict):
        _ensure_constraint_grammars_shape(raw["constraint_grammars"], errs)
    if isinstance(raw.get("trigger"), dict):
        _ensure_trigger_shape(raw["trigger"], errs)
    if isinstance(raw.get("required_context"), dict):
        _ensure_required_context_shape(raw["required_context"], errs)
    if isinstance(raw.get("constraints"), dict):
        _ensure_constraints_shape(raw["constraints"], errs)
    if isinstance(raw.get("authority_envelope"), dict):
        _ensure_authority_envelope_shape(raw["authority_envelope"], errs)
    if isinstance(raw.get("termination"), dict):
        _ensure_termination_shape(raw["termination"], errs)
    if isinstance(raw.get("alignment_notes"), dict):
        _ensure_alignment_notes_shape(raw["alignment_notes"], errs)

    if strict_said and _is_str(policy.d):
        expected = compute_authority_policy_said_v1(raw)
        if policy.d != expected:
            errs.append("d mismatch (SAID does not bind to derivation bytes)")

    if template is not None:
        if template.d.strip() and policy.card_template_ref.strip() and policy.card_template_ref.strip() != template.d.strip():
            errs.append("card_template_ref does not match provided template SAID")

    return ValidationResult(ok=(len(errs) == 0), errors=errs)


# ============================================================
# Builder: template -> instance (invariants removed)
# ============================================================

def build_authority_policy_instance_from_template(
    template_raw: Dict[str, Any],
    *,
    card_template_ref: str,
    status: str,
    policy: Dict[str, str],
    constraint_grammars: Dict[str, str],
    trigger: Dict[str, Any],
    required_context: Dict[str, Any],
    constraints: Dict[str, Any],
    authority_envelope: Dict[str, Any],
    termination: Dict[str, Any],
    explicit_non_goals: List[str],
    alignment_notes: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Create an Authority Policy instance from a template.

    - Sets card_type='authority_policy'
    - Adds card_template_ref
    - Sets constraint_grammars.{privacy_grammar_ref, execution_grammar_ref}
    - Removes invariants (template-only)
    - Embeds SAID into 'd'
    """
    inst = json.loads(json.dumps(template_raw))  # deep copy

    inst["type"] = "cca/governance/concept/1.0"
    inst["card_type"] = "authority_policy"
    inst["card_template_ref"] = str(card_template_ref or "").strip()
    inst["status"] = str(status or "active").strip() or "active"

    inst["policy"] = {
        "name": str(policy.get("name") or "").strip(),
        "label": str(policy.get("label") or "").strip(),
        "description": str(policy.get("description") or "").strip(),
    }

    inst["constraint_grammars"] = {
        "privacy_grammar_ref": str(constraint_grammars.get("privacy_grammar_ref") or "").strip(),
        "execution_grammar_ref": str(constraint_grammars.get("execution_grammar_ref") or "").strip(),
    }

    inst["trigger"] = trigger
    inst["required_context"] = required_context
    inst["constraints"] = constraints
    inst["authority_envelope"] = authority_envelope
    inst["termination"] = termination
    inst["explicit_non_goals"] = explicit_non_goals
    inst["alignment_notes"] = alignment_notes

    inst.pop("invariants", None)

    inst["d"] = inst.get("d") or "############################################"

    return embed_said(inst)


# ============================================================
# Convenience functions for UI integration
# ============================================================

def _slugify_name(label: str) -> str:
    s = (label or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "authority_policy"


def create_authority_policy(
    *,
    ward_path: Path,
    label: str,
    description: str,
    role_refs: List[str],
    persona_refs: List[str],
    operation_refs: List[str],
    privacy_grammar_ref: str,
    execution_grammar_ref: str,
    ttl_seconds_default: Optional[int] = None,
    ttl_seconds_max: Optional[int] = None,
    status: str = "active",
    template_path: Optional[Path] = None,

    # 🔹 NEW (safe additions)
    profile: Optional[str] = None,
    fail_closed: Optional[bool] = None,

    # Optional advanced overrides
    target_kind_allowlist: Optional[List[str]] = None,
    max_targets: Optional[int] = None,
    zone_refs: Optional[List[str]] = None,
    overlay_refs: Optional[List[str]] = None,
    jurisdiction_refs: Optional[List[str]] = None,
    effective_time_max_skew_seconds: Optional[int] = None,
    privacy_constraints: Optional[Dict[str, List[str]]] = None,
    execution_constraints: Optional[Dict[str, List[str]]] = None,
    scope_defaults: Optional[Dict[str, Any]] = None,
    scope_max: Optional[Dict[str, Any]] = None,
    termination: Optional[Dict[str, Any]] = None,
    explicit_non_goals: Optional[List[str]] = None,
    alignment_notes: Optional[Dict[str, str]] = None,
) -> str:
    """
    Create an Authority Policy instance, embed SAID, validate, and write:
      wards/<WARD>/authority_policies/<SAID>.json
    """

    if status not in _ALLOWED_STATUS:
        raise ValueError(f"invalid status: {status}")

    if not (privacy_grammar_ref or "").strip():
        raise ValueError("privacy_grammar_ref is required")

    if not (execution_grammar_ref or "").strip():
        raise ValueError("execution_grammar_ref is required")

    tpl = load_authority_policy_template(template_path=template_path)

    name = _slugify_name(label)

    trigger = {
        "role_refs": list(role_refs or []),
        "persona_refs": list(persona_refs or []),
        "operation_refs": list(operation_refs or []),
        "target_kind_allowlist": list(target_kind_allowlist or []),
        "max_targets": max_targets,
    }

    required_context = {
        "zone_refs": list(zone_refs or []),
        "overlay_refs": list(overlay_refs or []),
        "jurisdiction_refs": list(jurisdiction_refs or []),
        "effective_time_max_skew_seconds": effective_time_max_skew_seconds,
    }

    # 🔧 FIX: correct variable names
    privacy_constraints = privacy_constraints or {}
    execution_constraints = execution_constraints or {}

    constraints = {
        "dpv": {
            "purpose": list(privacy_constraints.get("purpose", [])),
            "processing_prohibitions": list(privacy_constraints.get("processing_prohibitions", [])),
            "recipients_allow": list(privacy_constraints.get("recipients_allow", [])),
            "data_categories_allow": list(privacy_constraints.get("data_categories_allow", [])),
            "safeguards": list(privacy_constraints.get("safeguards", [])),
        },
        "ocv": {
            "execution_posture": list(execution_constraints.get("execution_posture", [])),
            "side_effects": list(execution_constraints.get("side_effects", [])),
            "human_gates": list(execution_constraints.get("human_gates", [])),
            "time": list(execution_constraints.get("time", [])),
            "scope": list(execution_constraints.get("scope", [])),
            "safety": list(execution_constraints.get("safety", [])),
            "lifecycle": list(execution_constraints.get("lifecycle", [])),
        },
    }

    scope_defaults = scope_defaults or {
        "max_records": None,
        "time_window_seconds": None,
        "field_allowlist": [],
        "field_blocklist": [],
    }

    scope_max = scope_max or {
        "max_records": None,
        "time_window_seconds": None,
    }

    authority_envelope = {
        "ttl_seconds_default": ttl_seconds_default,
        "ttl_seconds_max": ttl_seconds_max,
        "scope_defaults": scope_defaults,
        "scope_max": scope_max,
        # 🔹 NEW
        "fail_closed": fail_closed,
    }

    termination_obj = termination or {
        "single_execution": None,
        "auto_terminate_on_completion": None,
        "invalidate_on_context_change": None,
    }

    explicit_non_goals_list = list(explicit_non_goals or [])

    alignment_notes_obj = alignment_notes or {
        "role_alignment": "",
        "persona_alignment": "",
        "operation_alignment": "",
        "authority_posture": "",
    }

    policy_block = {
        "name": name,
        "label": label,
        "description": description,
    }

    # 🔹 NEW: profile (non-authorizing metadata)
    if profile:
        policy_block["profile"] = str(profile).strip()

    inst_raw = build_authority_policy_instance_from_template(
        tpl.raw,
        card_template_ref=tpl.d,
        status=status,
        policy=policy_block,
        constraint_grammars={
            "privacy_grammar_ref": privacy_grammar_ref,
            "execution_grammar_ref": execution_grammar_ref,
        },
        trigger=trigger,
        required_context=required_context,
        constraints=constraints,
        authority_envelope=authority_envelope,
        termination=termination_obj,
        explicit_non_goals=explicit_non_goals_list,
        alignment_notes=alignment_notes_obj,
    )

    vr = validate_authority_policy_v1(AuthorityPolicyV1(inst_raw), strict_said=True)
    if not vr.ok:
        raise ValueError("authority policy instance validation failed: " + "; ".join(vr.errors))

    said = str(inst_raw.get("d") or "").strip()
    if not said:
        raise ValueError("failed to embed SAID into authority policy instance")

    out_path = authority_policy_path(ward_path, said)
    write_json(out_path, inst_raw)

    return said


def delete_authority_policy(ward_path: Path, filename: str) -> None:
    """
    Hard-delete a policy file in wards/<WARD>/authority_policies/.
    'filename' may be '<SAID>.json' or '<SAID>' (without extension).
    """
    authority_policies_dir(ward_path).mkdir(parents=True, exist_ok=True)

    fname = (filename or "").strip()
    if not fname:
        raise ValueError("filename is required")

    if not fname.endswith(".json"):
        fname = f"{fname}.json"

    p = authority_policies_dir(ward_path) / fname
    if not p.exists():
        raise FileNotFoundError(f"authority policy not found: {p}")

    p.unlink()

