# src/operation_card.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.said_python import python_derivation_json, python_said_generate


# ============================================================
# Models
# ============================================================

@dataclass(frozen=True)
class OperationTemplateV1:
    """
    Operation Card TEMPLATE (cca/execution/action/1.0) with SAID in field 'd'.

    IMPORTANT:
      - Template MAY contain invariants (template-only).
      - Template MUST NOT imply bindings to Role/Persona cards.
        It only defines operation-internal structure + constraints.
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
    def operation(self) -> Dict[str, Any]:
        o = self.raw.get("operation")
        return o if isinstance(o, dict) else {}

    @property
    def constraint_grammar_ref(self) -> str:
        cg = self.raw.get("constraint_grammar")
        cg = cg if isinstance(cg, dict) else {}
        return str(cg.get("grammar_ref") or "")

    @property
    def invariants(self) -> Dict[str, Any]:
        inv = self.raw.get("invariants")
        return inv if isinstance(inv, dict) else {}


@dataclass(frozen=True)
class OperationCardV1:
    """
    Operation Card INSTANCE (cca/execution/action/1.0) with SAID in field 'd'.

    IMPORTANT:
      - invariants MUST NOT be present in the instance (template-only rule).
      - No implied bindings to Role/Persona. Any binding/resolution happens upstream.
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
    def operation(self) -> Dict[str, Any]:
        o = self.raw.get("operation")
        return o if isinstance(o, dict) else {}

    @property
    def operation_name(self) -> str: return str(self.operation.get("name") or "")
    @property
    def operation_label(self) -> str: return str(self.operation.get("label") or "")
    @property
    def operation_description(self) -> str: return str(self.operation.get("description") or "")

    @property
    def constraint_grammar_ref(self) -> str:
        cg = self.raw.get("constraint_grammar")
        cg = cg if isinstance(cg, dict) else {}
        return str(cg.get("grammar_ref") or "")


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: List[str]


# ============================================================
# IO
# ============================================================

def read_json(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(p: Path, obj: Dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def operations_dir(ward_path: Path) -> Path:
    return ward_path / "operations"


def operation_template_path(ward_path: Path, filename: str) -> Path:
    # NOTE: if you ever store templates elsewhere, change it upstream in app.py;
    # this module stays ward-centric for operational artifacts.
    return operations_dir(ward_path) / filename


def operation_card_path(ward_path: Path, operation_said: str) -> Path:
    return operations_dir(ward_path) / f"{operation_said}.json"


def operation_card_path_legacy_by_name(ward_path: Path, operation_name: str) -> Path:
    # legacy convenience; prefer SAID-addressed files
    return operations_dir(ward_path) / f"{operation_name}.json"


def load_operation_template(ward_path: Path, filename: str) -> OperationTemplateV1:
    p = operation_template_path(ward_path, filename)
    if not p.exists():
        raise FileNotFoundError(f"operation template not found: {p}")
    return OperationTemplateV1(read_json(p))


def load_operation_card(ward_path: Path, operation_name_or_said: str) -> OperationCardV1:
    """
    Load an operation card either by SAID (preferred) or legacy filename by operation.name.

    Preferred on-disk convention:
      wards/<WARD>/operations/<SAID>.json
    Legacy fallback:
      wards/<WARD>/operations/<operation_name>.json
    """
    p = operation_card_path(ward_path, operation_name_or_said)
    if not p.exists():
        p = operation_card_path_legacy_by_name(ward_path, operation_name_or_said)
    if not p.exists():
        raise FileNotFoundError(f"operation not found: {p}")
    return OperationCardV1(read_json(p))


# ============================================================
# SAID helpers (d-field)
# ============================================================

def compute_operation_said_v1(raw: Dict[str, Any]) -> str:
    """
    Compute SAID for template or instance by canonical derivation (via said_python).
    """
    saided = python_said_generate(raw)
    return str(saided.get("d") or "")


def embed_said(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Return a new object with 'd' correctly embedded."""
    return python_said_generate(raw)


def operation_said_matches(raw: Dict[str, Any]) -> bool:
    """Verify binding by recomputing SAID and comparing to raw['d']."""
    d = str(raw.get("d") or "").strip()
    if not d:
        return False
    return d == compute_operation_said_v1(raw)


def operation_derivation_json(raw: Dict[str, Any]) -> str:
    """Return the exact canonical JSON string hashed (debug/conformance)."""
    return python_derivation_json(raw)


# ============================================================
# Validation helpers (binding-agnostic)
# ============================================================

_ALLOWED_STATUS = {"active", "disabled", "deprecated"}
_ALLOWED_TYPE = {"cca/execution/action/1.0"}
_ALLOWED_TEMPLATE_CARD_TYPE = {"operation_template"}
_ALLOWED_INSTANCE_CARD_TYPE = {"operation"}
_ALLOWED_RESOLUTION_SCOPE = {"public", "private"}

_REQUIRED_TOP_LEVEL_OBJECTS = (
    "operation_tags",
    "defined_by",
    "execution_modality",
    "required_preconditions",
    "input_boundaries",
    "processing_constraints",
    "output_constraints",
    "post_execution_handling",
    "alignment_notes",
)

# NOTE: This validator is deliberately "binding-agnostic":
# - It DOES NOT assume Role Cards or Persona Cards exist.
# - It DOES NOT attempt to resolve required_roles / persona_context.
# - It DOES NOT enforce OCV semantics; only shape/types.


def _is_str(x: Any) -> bool:
    return isinstance(x, str) and bool(x.strip())


def _is_dict(x: Any) -> bool:
    return isinstance(x, dict)


def _is_bool_or_none(x: Any) -> bool:
    return x is None or isinstance(x, bool)


def _is_int_or_none(x: Any) -> bool:
    return x is None or (isinstance(x, int) and not isinstance(x, bool))


def _require_obj(raw: Dict[str, Any], key: str, errs: List[str]) -> None:
    if not isinstance(raw.get(key), dict):
        errs.append(f"{key} must be an object")


def _require_list(v: Any, path: str, errs: List[str]) -> None:
    if not isinstance(v, list):
        errs.append(f"{path} must be a list (use [] if empty)")


def _require_str(v: Any, path: str, errs: List[str]) -> None:
    if not isinstance(v, str):
        errs.append(f"{path} must be a string")


def _require_resolution_scope(obj: Dict[str, Any], path: str, errs: List[str]) -> None:
    rs = str(obj.get("resolution_scope") or "").strip()
    if rs and rs not in _ALLOWED_RESOLUTION_SCOPE:
        errs.append(f"{path}.resolution_scope must be 'public' or 'private'")


def _ensure_operation_tags_shape(tags: Dict[str, Any], errs: List[str]) -> None:
    if "operation_type" not in tags:
        errs.append("operation_tags.operation_type required (use '' if unset)")
    elif not isinstance(tags.get("operation_type"), str):
        errs.append("operation_tags.operation_type must be a string")

    for k in ("domain", "sensitivity", "audience", "capabilities"):
        if k not in tags:
            errs.append(f"operation_tags.{k} required (use [] if empty)")
        else:
            _require_list(tags.get(k), f"operation_tags.{k}", errs)


def _ensure_defined_by_shape(db: Dict[str, Any], errs: List[str]) -> None:
    for k in ("service_ref", "label", "resolution_scope"):
        if k not in db:
            errs.append(f"defined_by.{k} required (use '' for strings)")
        elif not isinstance(db.get(k), str):
            errs.append(f"defined_by.{k} must be a string")
    _require_resolution_scope(db, "defined_by", errs)


def _ensure_execution_modality_shape(em: Dict[str, Any], errs: List[str]) -> None:
    if "mutability" not in em:
        errs.append("execution_modality.mutability required (use '' if unset)")
    elif not isinstance(em.get("mutability"), str):
        errs.append("execution_modality.mutability must be a string")

    for k in (
        "draft_only",
        "read_only",
        "transform_only",
        "synchronous_only",
        "single_execution",
        "auto_termination_on_completion",
    ):
        if k not in em:
            errs.append(f"execution_modality.{k} required (use null if unset)")
        elif not _is_bool_or_none(em.get(k)):
            errs.append(f"execution_modality.{k} must be boolean or null")


def _ensure_required_preconditions_shape(rp: Dict[str, Any], errs: List[str]) -> None:
    for k in (
        "admission_state",
        "required_persona_context",
        "compatible_persona_contexts",
        "required_roles",
        "human_in_loop",
    ):
        if k not in rp:
            errs.append(f"required_preconditions.{k} required (use [] if empty)")
        else:
            _require_list(rp.get(k), f"required_preconditions.{k}", errs)

    if "non_authorizing_notice" not in rp:
        errs.append("required_preconditions.non_authorizing_notice required (use '' if empty)")
    elif not isinstance(rp.get("non_authorizing_notice"), str):
        errs.append("required_preconditions.non_authorizing_notice must be a string")


def _ensure_record_limits_shape(ib: Dict[str, Any], errs: List[str]) -> None:
    rl = ib.get("record_limits")
    if not isinstance(rl, dict):
        errs.append("input_boundaries.record_limits must be an object")
        return

    for k in ("max_contacts_considered", "max_patient_records", "max_records_touched"):
        if k not in rl:
            errs.append(f"input_boundaries.record_limits.{k} required (use null if unset)")
        elif not _is_int_or_none(rl.get(k)):
            errs.append(f"input_boundaries.record_limits.{k} must be an integer or null")


def _ensure_input_boundaries_shape(ib: Dict[str, Any], errs: List[str]) -> None:
    for k in (
        "allowed_data_categories",
        "required_inputs",
        "optional_inputs",
        "explicitly_prohibited_inputs",
    ):
        if k not in ib:
            errs.append(f"input_boundaries.{k} required (use [] if empty)")
        else:
            _require_list(ib.get(k), f"input_boundaries.{k}", errs)
    _ensure_record_limits_shape(ib, errs)


def _ensure_processing_constraints_shape(pc: Dict[str, Any], errs: List[str]) -> None:
    for k in ("allowed_actions", "prohibited_actions", "transformation_limits"):
        if k not in pc:
            errs.append(f"processing_constraints.{k} required (use [] if empty)")
        else:
            _require_list(pc.get(k), f"processing_constraints.{k}", errs)


def _ensure_output_constraints_shape(oc: Dict[str, Any], errs: List[str]) -> None:
    for k in ("output_type", "format", "detail_level"):
        if k not in oc:
            errs.append(f"output_constraints.{k} required (use '' if empty)")
        else:
            _require_str(oc.get(k), f"output_constraints.{k}", errs)

    for k in ("required_sections", "redaction_rules", "use_restrictions"):
        if k not in oc:
            errs.append(f"output_constraints.{k} required (use [] if empty)")
        else:
            _require_list(oc.get(k), f"output_constraints.{k}", errs)


def _ensure_post_execution_handling_shape(peh: Dict[str, Any], errs: List[str]) -> None:
    if "retention" not in peh:
        errs.append("post_execution_handling.retention required (use '' if empty)")
    elif not isinstance(peh.get("retention"), str):
        errs.append("post_execution_handling.retention must be a string")

    for k in ("draft_lifecycle", "audit_requirements", "completion_acknowledgement"):
        if k not in peh:
            errs.append(f"post_execution_handling.{k} required (use [] if empty)")
        else:
            _require_list(peh.get(k), f"post_execution_handling.{k}", errs)


def _ensure_alignment_notes_shape(an: Dict[str, Any], errs: List[str]) -> None:
    for k in ("role_alignment", "persona_alignment", "privacy_posture"):
        if k not in an:
            errs.append(f"alignment_notes.{k} required (use '' if empty)")
        elif not isinstance(an.get(k), str):
            errs.append(f"alignment_notes.{k} must be a string")


# ============================================================
# Template validation
# ============================================================

def validate_operation_template_v1(template: OperationTemplateV1, strict_said: bool = True) -> ValidationResult:
    errs: List[str] = []
    raw = template.raw

    if template.type not in _ALLOWED_TYPE:
        errs.append("type must be 'cca/execution/action/1.0'")

    if template.card_type not in _ALLOWED_TEMPLATE_CARD_TYPE:
        errs.append("card_type must be 'operation_template'")

    if template.status not in _ALLOWED_STATUS:
        errs.append("status must be one of: active, disabled, deprecated")

    if not _is_str(template.d):
        errs.append("d required (SAID)")

    if not _is_dict(raw.get("operation")):
        errs.append("operation must be an object")
    else:
        for k in ("name", "label", "description"):
            if k not in raw["operation"]:
                errs.append(f"operation.{k} required (use '' if unset)")
            elif not isinstance(raw["operation"].get(k), str):
                errs.append(f"operation.{k} must be a string")

    # grammar required
    cg = raw.get("constraint_grammar")
    if not _is_dict(cg) or not _is_str(cg.get("grammar_ref")):
        errs.append("constraint_grammar.grammar_ref required")

    # required top-level objects
    for k in _REQUIRED_TOP_LEVEL_OBJECTS:
        _require_obj(raw, k, errs)

    # section shape checks
    if isinstance(raw.get("operation_tags"), dict):
        _ensure_operation_tags_shape(raw["operation_tags"], errs)
    if isinstance(raw.get("defined_by"), dict):
        _ensure_defined_by_shape(raw["defined_by"], errs)
    if isinstance(raw.get("execution_modality"), dict):
        _ensure_execution_modality_shape(raw["execution_modality"], errs)
    if isinstance(raw.get("required_preconditions"), dict):
        _ensure_required_preconditions_shape(raw["required_preconditions"], errs)
    if isinstance(raw.get("input_boundaries"), dict):
        _ensure_input_boundaries_shape(raw["input_boundaries"], errs)
    if isinstance(raw.get("processing_constraints"), dict):
        _ensure_processing_constraints_shape(raw["processing_constraints"], errs)
    if isinstance(raw.get("output_constraints"), dict):
        _ensure_output_constraints_shape(raw["output_constraints"], errs)
    if isinstance(raw.get("post_execution_handling"), dict):
        _ensure_post_execution_handling_shape(raw["post_execution_handling"], errs)
    if isinstance(raw.get("alignment_notes"), dict):
        _ensure_alignment_notes_shape(raw["alignment_notes"], errs)

    eng = raw.get("explicit_non_goals")
    if eng is None:
        errs.append("explicit_non_goals required (use [] if empty)")
    elif not isinstance(eng, list):
        errs.append("explicit_non_goals must be a list")

    # invariants required in template (template-only)
    inv = raw.get("invariants")
    if not isinstance(inv, dict):
        errs.append("invariants must be an object (template-only)")
    else:
        required_bool_keys = (
            "non_authorizing",
            "deterministic_evaluable",
            "deny_by_default_on_missing_preconditions",
            "no_external_side_effects_unless_explicitly_declared",
            "execution_modality_must_be_consistent",
            "template_fields_must_not_grant_authority",
            "constraint_grammar_locked",
        )
        for k in required_bool_keys:
            if k not in inv:
                errs.append(f"invariants.{k} required")
            elif not isinstance(inv.get(k), bool):
                errs.append(f"invariants.{k} must be boolean")
        if inv.get("non_authorizing") is not True:
            errs.append("invariants.non_authorizing must be true")
        if inv.get("constraint_grammar_locked") is not True:
            errs.append("invariants.constraint_grammar_locked must be true")

    # strict SAID binding
    if strict_said and _is_str(template.d):
        expected = compute_operation_said_v1(raw)
        if template.d != expected:
            errs.append("d mismatch (SAID does not bind to derivation bytes)")

    return ValidationResult(ok=(len(errs) == 0), errors=errs)


# ============================================================
# Instance validation (binding-agnostic; NO invariants)
# ============================================================

def validate_operation_card_v1(
    card: OperationCardV1,
    template: Optional[OperationTemplateV1] = None,
    strict_said: bool = True,
) -> ValidationResult:
    errs: List[str] = []
    raw = card.raw

    if card.type not in _ALLOWED_TYPE:
        errs.append("type must be 'cca/execution/action/1.0'")

    if card.card_type not in _ALLOWED_INSTANCE_CARD_TYPE:
        errs.append("card_type must be 'operation'")

    if card.status not in _ALLOWED_STATUS:
        errs.append("status must be one of: active, disabled, deprecated")

    if not _is_str(card.d):
        errs.append("d required (SAID)")

    if not _is_str(card.card_template_ref):
        errs.append("card_template_ref required (Operation Template SAID)")

    # template-only invariant rule
    if "invariants" in raw:
        errs.append("invariants must not be present in Operation instance (template-only)")

    # operation identity
    if not _is_dict(raw.get("operation")):
        errs.append("operation must be an object")
    else:
        if not _is_str(card.operation_name):
            errs.append("operation.name required")
        if not _is_str(card.operation_label):
            errs.append("operation.label required")
        if not _is_str(card.operation_description):
            errs.append("operation.description required")

    # grammar required on instance
    cg = raw.get("constraint_grammar")
    if not _is_dict(cg) or not _is_str(cg.get("grammar_ref")):
        errs.append("constraint_grammar.grammar_ref required")

    # required sections (shape only)
    for k in _REQUIRED_TOP_LEVEL_OBJECTS:
        _require_obj(raw, k, errs)

    # section shape checks (binding-agnostic)
    if isinstance(raw.get("operation_tags"), dict):
        _ensure_operation_tags_shape(raw["operation_tags"], errs)
    if isinstance(raw.get("defined_by"), dict):
        _ensure_defined_by_shape(raw["defined_by"], errs)
    if isinstance(raw.get("execution_modality"), dict):
        _ensure_execution_modality_shape(raw["execution_modality"], errs)
    if isinstance(raw.get("required_preconditions"), dict):
        _ensure_required_preconditions_shape(raw["required_preconditions"], errs)
    if isinstance(raw.get("input_boundaries"), dict):
        _ensure_input_boundaries_shape(raw["input_boundaries"], errs)
    if isinstance(raw.get("processing_constraints"), dict):
        _ensure_processing_constraints_shape(raw["processing_constraints"], errs)
    if isinstance(raw.get("output_constraints"), dict):
        _ensure_output_constraints_shape(raw["output_constraints"], errs)
    if isinstance(raw.get("post_execution_handling"), dict):
        _ensure_post_execution_handling_shape(raw["post_execution_handling"], errs)
    if isinstance(raw.get("alignment_notes"), dict):
        _ensure_alignment_notes_shape(raw["alignment_notes"], errs)

    eng = raw.get("explicit_non_goals")
    if eng is None:
        errs.append("explicit_non_goals required (use [] if empty)")
    elif not isinstance(eng, list):
        errs.append("explicit_non_goals must be a list")

    # strict SAID binding
    if strict_said and _is_str(card.d):
        expected = compute_operation_said_v1(raw)
        if card.d != expected:
            errs.append("d mismatch (SAID does not bind to derivation bytes)")

    # Template is optional; if provided, only check template identity + optional grammar lock.
    if template is not None:
        if template.d.strip() and card.card_template_ref.strip() and card.card_template_ref.strip() != template.d.strip():
            errs.append("card_template_ref does not match provided template SAID")

        inv = template.invariants
        if isinstance(inv, dict) and inv.get("constraint_grammar_locked") is True:
            t_ref = template.constraint_grammar_ref.strip()
            i_ref = str(cg.get("grammar_ref") or "").strip() if isinstance(cg, dict) else ""
            if t_ref and i_ref and t_ref != i_ref:
                errs.append("constraint_grammar.grammar_ref must match template (constraint_grammar_locked=true)")

    return ValidationResult(ok=(len(errs) == 0), errors=errs)


# ============================================================
# Builder: template -> instance (invariants removed)
# ============================================================

def build_operation_instance_from_template(
    template_raw: Dict[str, Any],
    *,
    card_template_ref: str,
    constraint_grammar_ref: str,
    status: str,
    operation: Dict[str, str],
    operation_tags: Dict[str, Any],
    defined_by: Dict[str, Any],
    execution_modality: Dict[str, Any],
    required_preconditions: Dict[str, Any],
    input_boundaries: Dict[str, Any],
    processing_constraints: Dict[str, Any],
    output_constraints: Dict[str, Any],
    post_execution_handling: Dict[str, Any],
    explicit_non_goals: List[str],
    alignment_notes: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Create an Operation instance from a template.

    - Sets card_type='operation'
    - Adds card_template_ref
    - Sets constraint_grammar.grammar_ref
    - Removes invariants (template-only)
    - Embeds SAID into 'd'
    """
    inst = json.loads(json.dumps(template_raw))  # deep copy

    inst["type"] = "cca/execution/action/1.0"
    inst["card_type"] = "operation"
    inst["card_template_ref"] = str(card_template_ref or "").strip()
    inst["status"] = str(status or "active").strip() or "active"

    inst["operation"] = {
        "name": str(operation.get("name") or "").strip(),
        "label": str(operation.get("label") or "").strip(),
        "description": str(operation.get("description") or "").strip(),
    }

    inst["constraint_grammar"] = {"grammar_ref": str(constraint_grammar_ref or "").strip()}

    inst["operation_tags"] = operation_tags
    inst["defined_by"] = defined_by
    inst["execution_modality"] = execution_modality
    inst["required_preconditions"] = required_preconditions
    inst["input_boundaries"] = input_boundaries
    inst["processing_constraints"] = processing_constraints
    inst["output_constraints"] = output_constraints
    inst["post_execution_handling"] = post_execution_handling
    inst["explicit_non_goals"] = explicit_non_goals
    inst["alignment_notes"] = alignment_notes

    # template-only invariants
    inst.pop("invariants", None)

    # ensure d placeholder exists for derivation
    inst["d"] = inst.get("d") or "############################################"

    return embed_said(inst)
