# src/persona_card.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.said_python import python_derivation_json, python_said_generate, embed_said


# ============================================================
# Types
# ============================================================

@dataclass(frozen=True)
class PersonaTemplateV1:
    """
    Persona Card Template (CCA / agency / concept / 1.0) with SAID in field 'd'.

    Invariants live here (template-only rule).
    """
    raw: Dict[str, Any]

    @property
    def d(self) -> str:
        return str(self.raw.get("d") or "")

    @property
    def type(self) -> str:
        return str(self.raw.get("type") or "")

    @property
    def card_type(self) -> str:
        return str(self.raw.get("card_type") or "")

    @property
    def status(self) -> str:
        return str(self.raw.get("status") or "")

    @property
    def persona(self) -> Dict[str, Any]:
        p = self.raw.get("persona")
        return p if isinstance(p, dict) else {}

    @property
    def persona_name(self) -> str:
        return str(self.persona.get("name") or "")

    @property
    def persona_label(self) -> str:
        return str(self.persona.get("label") or "")

    @property
    def persona_description(self) -> str:
        return str(self.persona.get("description") or "")

    @property
    def duty_frame(self) -> Dict[str, Any]:
        df = self.raw.get("duty_frame")
        return df if isinstance(df, dict) else {}

    @property
    def duty_frame_ref(self) -> str:
        return str(self.duty_frame.get("ref") or "")

    @property
    def duty_frame_label(self) -> str:
        return str(self.duty_frame.get("label") or "")

    @property
    def duty_frame_resolution_scope(self) -> str:
        return str(self.duty_frame.get("resolution_scope") or "")

    @property
    def constraint_grammar_ref(self) -> str:
        cg = self.raw.get("constraint_grammar")
        cg = cg if isinstance(cg, dict) else {}
        return str(cg.get("grammar_ref") or "")

    @property
    def persona_tags(self) -> Dict[str, Any]:
        t = self.raw.get("persona_tags")
        return t if isinstance(t, dict) else {}

    @property
    def constraints(self) -> Dict[str, Any]:
        c = self.raw.get("constraints")
        return c if isinstance(c, dict) else {}

    @property
    def composition(self) -> Dict[str, Any]:
        comp = self.raw.get("composition")
        return comp if isinstance(comp, dict) else {}

    @property
    def invariants(self) -> Dict[str, Any]:
        inv = self.raw.get("invariants")
        return inv if isinstance(inv, dict) else {}


@dataclass(frozen=True)
class PersonaCardV1:
    """
    Persona Card instance (CCA / agency / concept / 1.0) with SAID in field 'd'.

    NOTE: Per your rule, invariants are TEMPLATE-ONLY and MUST NOT be repeated here.
    """
    raw: Dict[str, Any]

    @property
    def d(self) -> str:
        return str(self.raw.get("d") or "")

    @property
    def type(self) -> str:
        return str(self.raw.get("type") or "")

    @property
    def card_type(self) -> str:
        return str(self.raw.get("card_type") or "")

    @property
    def status(self) -> str:
        return str(self.raw.get("status") or "")

    @property
    def card_template_ref(self) -> str:
        return str(self.raw.get("card_template_ref") or "")

    @property
    def persona(self) -> Dict[str, Any]:
        p = self.raw.get("persona")
        return p if isinstance(p, dict) else {}

    @property
    def persona_name(self) -> str:
        return str(self.persona.get("name") or "")

    @property
    def persona_label(self) -> str:
        return str(self.persona.get("label") or "")

    @property
    def persona_description(self) -> str:
        return str(self.persona.get("description") or "")

    @property
    def duty_frame(self) -> Dict[str, Any]:
        df = self.raw.get("duty_frame")
        return df if isinstance(df, dict) else {}

    @property
    def duty_frame_ref(self) -> str:
        return str(self.duty_frame.get("ref") or "")

    @property
    def duty_frame_label(self) -> str:
        return str(self.duty_frame.get("label") or "")

    @property
    def duty_frame_resolution_scope(self) -> str:
        return str(self.duty_frame.get("resolution_scope") or "")

    @property
    def constraint_grammar_ref(self) -> str:
        cg = self.raw.get("constraint_grammar")
        cg = cg if isinstance(cg, dict) else {}
        return str(cg.get("grammar_ref") or "")

    @property
    def persona_tags(self) -> Dict[str, Any]:
        t = self.raw.get("persona_tags")
        return t if isinstance(t, dict) else {}

    @property
    def constraints(self) -> Dict[str, Any]:
        c = self.raw.get("constraints")
        return c if isinstance(c, dict) else {}

    @property
    def composition(self) -> Dict[str, Any]:
        comp = self.raw.get("composition")
        return comp if isinstance(comp, dict) else {}


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
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def persona_card_path(ward_path: Path, persona_said: str) -> Path:
    return ward_path / "personas" / f"{persona_said}.json"


def load_persona_card(ward_path: Path, persona_id: str) -> PersonaCardV1:
    persona_id = (persona_id or "").strip()
    if not persona_id:
        raise ValueError("persona_id must be non-empty")

    p = ward_path / "personas" / f"{persona_id}.json"
    if p.exists():
        raw = read_json(p)
        return PersonaCardV1(raw)

    if persona_id.endswith(".json"):
        p2 = ward_path / "personas" / persona_id
        if p2.exists():
            raw = read_json(p2)
            return PersonaCardV1(raw)

    p3 = ward_path / "personas" / persona_id
    if p3.exists():
        raw = read_json(p3)
        return PersonaCardV1(raw)

    raise FileNotFoundError(f"persona card not found: {p}")


# ============================================================
# SAID helpers (d-field)
# ============================================================

def compute_persona_said_v1(raw: Dict[str, Any]) -> str:
    """
    Compute the SAID (field 'd') for a Persona Template or Persona instance:
      - Build derivation JSON with placeholder in 'd'
      - Blake3-256 hash
      - CESR/QB64 encode (E + 43 chars)
      - Embed into 'd'
    """
    saided = python_said_generate(raw)
    return str(saided.get("d") or "")


def embed_said(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Return a new object with 'd' correctly embedded."""
    return python_said_generate(raw)


def persona_said_matches(raw: Dict[str, Any]) -> bool:
    """Verify binding by recomputing SAID and comparing to raw['d']."""
    d = str(raw.get("d") or "").strip()
    if not d:
        return False
    return d == compute_persona_said_v1(raw)


def persona_derivation_json(raw: Dict[str, Any]) -> str:
    """For debugging/conformance: return the exact canonical JSON string hashed."""
    return python_derivation_json(raw)


# ============================================================
# Validation helpers
# ============================================================

_ALLOWED_STATUS = {"active", "disabled", "deprecated"}
_ALLOWED_TYPE = {"cca/agency/concept/1.0"}
_ALLOWED_TEMPLATE_CARD_TYPE = {"persona_template"}
_ALLOWED_INSTANCE_CARD_TYPE = {"persona"}
_ALLOWED_RESOLUTION_SCOPE = {"public", "private"}

# Keys expected in persona_tags for template + instance
_PERSONA_TAG_KEYS = ("purpose", "behavior", "privacy", "integrity", "tooling")


def _is_str(x: Any) -> bool:
    return isinstance(x, str) and bool(x.strip())


def _is_list_of_str(x: Any) -> bool:
    return isinstance(x, list) and all(isinstance(i, str) and bool(i.strip()) for i in x)


def _ensure_tag_shape(tag_obj: Any) -> Tuple[bool, List[str]]:
    """
    Enforce persona_tags contains known keys and each value is a list of strings.
    Returns (ok, errors).
    """
    errs: List[str] = []
    if not isinstance(tag_obj, dict):
        return False, ["persona_tags must be an object"]

    for k in _PERSONA_TAG_KEYS:
        v = tag_obj.get(k)
        if v is None:
            errs.append(f"persona_tags.{k} required (use [] if empty)")
        elif not isinstance(v, list):
            errs.append(f"persona_tags.{k} must be a list")
        elif not _is_list_of_str(v) and v != []:
            errs.append(f"persona_tags.{k} must be a list of strings")
    return (len(errs) == 0), errs


def _ensure_constraints_min_shape(constraints_obj: Any) -> Tuple[bool, List[str]]:
    """
    Minimal shape check for constraints: must be object.
    We don't enforce every nested list type here, but we guard obvious schema breaks.
    """
    errs: List[str] = []
    if not isinstance(constraints_obj, dict):
        return False, ["constraints must be an object"]

    # Top-level keys we expect (from your template)
    for k in ("purpose", "behavior", "privacy_boundaries", "tooling_modality", "time_scope_budget", "integrity"):
        if k not in constraints_obj:
            errs.append(f"constraints.{k} required")
    # Basic lists
    for k in ("purpose", "behavior", "integrity"):
        v = constraints_obj.get(k)
        if not isinstance(v, list):
            errs.append(f"constraints.{k} must be a list (use [] if empty)")
        elif v != [] and not _is_list_of_str(v):
            errs.append(f"constraints.{k} must be a list of strings")

    # privacy_boundaries / tooling_modality / time_scope_budget should be objects
    for k in ("privacy_boundaries", "tooling_modality", "time_scope_budget"):
        v = constraints_obj.get(k)
        if not isinstance(v, dict):
            errs.append(f"constraints.{k} must be an object")

    return (len(errs) == 0), errs


def _tags_subset_of_constraints(tags: Dict[str, Any], constraints: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Enforce template invariant: tags must be subset of constraints.

    Mapping:
      tags.purpose    ⊆ constraints.purpose
      tags.behavior   ⊆ constraints.behavior
      tags.privacy    ⊆ union of lists inside constraints.privacy_boundaries
      tags.integrity  ⊆ constraints.integrity
      tags.tooling    ⊆ union of lists inside constraints.tooling_modality
    """
    errs: List[str] = []
    if not isinstance(tags, dict) or not isinstance(constraints, dict):
        return False, ["tags/constraints not objects (cannot compare)"]

    def as_set_list(path_val: Any) -> set[str]:
        if isinstance(path_val, list):
            return {str(x).strip() for x in path_val if isinstance(x, str) and x.strip()}
        return set()

    # purpose, behavior, integrity are direct lists
    purpose_allowed = as_set_list(constraints.get("purpose"))
    behavior_allowed = as_set_list(constraints.get("behavior"))
    integrity_allowed = as_set_list(constraints.get("integrity"))

    for t in as_set_list(tags.get("purpose")):
        if t not in purpose_allowed:
            errs.append(f"persona_tags.purpose contains '{t}' not in constraints.purpose")

    for t in as_set_list(tags.get("behavior")):
        if t not in behavior_allowed:
            errs.append(f"persona_tags.behavior contains '{t}' not in constraints.behavior")

    for t in as_set_list(tags.get("integrity")):
        if t not in integrity_allowed:
            errs.append(f"persona_tags.integrity contains '{t}' not in constraints.integrity")

    # privacy/tooling are unions of nested lists
    pb = constraints.get("privacy_boundaries")
    if isinstance(pb, dict):
        privacy_union: set[str] = set()
        for _k, _v in pb.items():
            privacy_union |= as_set_list(_v)
        for t in as_set_list(tags.get("privacy")):
            if t not in privacy_union:
                errs.append(f"persona_tags.privacy contains '{t}' not present in constraints.privacy_boundaries")
    else:
        # already flagged by shape check; don't add noisy error
        pass

    tm = constraints.get("tooling_modality")
    if isinstance(tm, dict):
        tooling_union: set[str] = set()
        for _k, _v in tm.items():
            tooling_union |= as_set_list(_v)
        for t in as_set_list(tags.get("tooling")):
            if t not in tooling_union:
                errs.append(f"persona_tags.tooling contains '{t}' not present in constraints.tooling_modality")
    else:
        pass

    return (len(errs) == 0), errs


# ============================================================
# Template validation (invariants live here)
# ============================================================

def validate_persona_template_v1(template: PersonaTemplateV1, strict_said: bool = True) -> ValidationResult:
    errs: List[str] = []
    raw = template.raw

    if template.type not in _ALLOWED_TYPE:
        errs.append("type must be 'cca/agency/concept/1.0'")

    if template.card_type not in _ALLOWED_TEMPLATE_CARD_TYPE:
        errs.append("card_type must be 'persona_template'")

    if template.status not in _ALLOWED_STATUS:
        errs.append("status must be one of: active, disabled, deprecated")

    if not _is_str(template.d):
        errs.append("d required (SAID)")

    # persona object
    if not isinstance(raw.get("persona"), dict):
        errs.append("persona must be an object")
    else:
        # template can leave these empty placeholders; allow empty strings
        # but keep them as strings if present
        for k in ("name", "label", "description"):
            v = template.persona.get(k)
            if v is not None and not isinstance(v, str):
                errs.append(f"persona.{k} must be a string")

    # duty_frame object
    if not isinstance(raw.get("duty_frame"), dict):
        errs.append("duty_frame must be an object")
    else:
        rs = template.duty_frame_resolution_scope.strip()
        if rs and rs not in _ALLOWED_RESOLUTION_SCOPE:
            errs.append("duty_frame.resolution_scope must be 'public' or 'private'")

        # template may carry empty placeholders, but keep types correct if present
        for k in ("ref", "label", "resolution_scope"):
            v = template.duty_frame.get(k)
            if v is not None and not isinstance(v, str):
                errs.append(f"duty_frame.{k} must be a string")

    # constraint grammar required
    if not _is_str(template.constraint_grammar_ref):
        errs.append("constraint_grammar.grammar_ref required")

    # persona_tags required shape
    ok, tag_errs = _ensure_tag_shape(raw.get("persona_tags"))
    if not ok:
        errs.extend(tag_errs)

    # constraints required minimum shape
    ok, c_errs = _ensure_constraints_min_shape(raw.get("constraints"))
    if not ok:
        errs.extend(c_errs)

    # composition required shape
    comp = raw.get("composition")
    if not isinstance(comp, dict):
        errs.append("composition must be an object")
    else:
        for k in ("compatible_personae", "excluded_personae"):
            v = comp.get(k)
            if v is None:
                errs.append(f"composition.{k} required (use [] if empty)")
            elif not isinstance(v, list):
                errs.append(f"composition.{k} must be a list")
            elif v != [] and not _is_list_of_str(v):
                errs.append(f"composition.{k} must be a list of strings")
        rule = comp.get("composition_rule")
        if rule is None:
            errs.append("composition.composition_rule required")
        elif not isinstance(rule, str):
            errs.append("composition.composition_rule must be a string")

    # invariants required (template-only)
    inv = raw.get("invariants")
    if not isinstance(inv, dict):
        errs.append("invariants must be an object (template-only)")
    else:
        # enforce your key set / boolean types if present
        required_bool_keys = (
            "non_authorizing",
            "tags_must_be_subset_of_constraints",
            "constraint_grammar_locked",
            "deterministic_evaluable",
            "composition_is_intersection_only",
            "no_substitution",
        )
        for k in required_bool_keys:
            if k not in inv:
                errs.append(f"invariants.{k} required")
            elif not isinstance(inv.get(k), bool):
                errs.append(f"invariants.{k} must be boolean")

        # If invariant says intersection_only, template composition_rule must match.
        if inv.get("composition_is_intersection_only") is True:
            rule = str(template.composition.get("composition_rule") or "").strip()
            if rule and rule != "intersection_only":
                errs.append("invariants.composition_is_intersection_only=true requires composition.composition_rule='intersection_only'")

        # If invariant says tags subset, enforce it on the template itself too (good hygiene)
        if inv.get("tags_must_be_subset_of_constraints") is True:
            tags = raw.get("persona_tags")
            constraints = raw.get("constraints")
            ok2, subset_errs = _tags_subset_of_constraints(tags if isinstance(tags, dict) else {}, constraints if isinstance(constraints, dict) else {})
            if not ok2:
                errs.extend(subset_errs)

    # strict SAID binding
    if strict_said and _is_str(template.d):
        expected = compute_persona_said_v1(raw)
        if template.d != expected:
            errs.append("d mismatch (SAID does not bind to derivation bytes)")

    return ValidationResult(ok=(len(errs) == 0), errors=errs)


# ============================================================
# Instance validation (NO invariants here)
# ============================================================

def validate_persona_card_v1(
    card: PersonaCardV1,
    template: Optional[PersonaTemplateV1] = None,
    strict_said: bool = True,
) -> ValidationResult:
    """
    Validate a Persona Card instance.

    Per your rule:
      - invariants must NOT appear in the instance
      - invariant-dependent checks are enforced ONLY if template is provided
        (and its SAID matches card.card_template_ref)
    """
    errs: List[str] = []
    raw = card.raw

    if card.type not in _ALLOWED_TYPE:
        errs.append("type must be 'cca/agency/concept/1.0'")

    if card.card_type not in _ALLOWED_INSTANCE_CARD_TYPE:
        errs.append("card_type must be 'persona'")

    if card.status not in _ALLOWED_STATUS:
        errs.append("status must be one of: active, disabled, deprecated")

    if not _is_str(card.d):
        errs.append("d required (SAID)")

    if not _is_str(card.card_template_ref):
        errs.append("card_template_ref required (Persona Template SAID)")

    # Hard rule: instance must not repeat invariants
    if "invariants" in raw:
        errs.append("invariants must not be present in Persona instance (template-only)")

    # persona object requirements (instance must have concrete fields)
    if not isinstance(raw.get("persona"), dict):
        errs.append("persona must be an object")
    else:
        if not _is_str(card.persona_name):
            errs.append("persona.name required")
        if not _is_str(card.persona_label):
            errs.append("persona.label required")
        if not _is_str(card.persona_description):
            errs.append("persona.description required")

    # duty_frame requirements (instance must have concrete bindings)
    if not isinstance(raw.get("duty_frame"), dict):
        errs.append("duty_frame must be an object")
    else:
        if not _is_str(card.duty_frame_ref):
            errs.append("duty_frame.ref required")
        if not _is_str(card.duty_frame_label):
            errs.append("duty_frame.label required")
        rs = card.duty_frame_resolution_scope.strip()
        if rs not in _ALLOWED_RESOLUTION_SCOPE:
            errs.append("duty_frame.resolution_scope must be 'public' or 'private'")

    # constraint grammar required
    if not _is_str(card.constraint_grammar_ref):
        errs.append("constraint_grammar.grammar_ref required")

    # persona_tags required shape
    ok, tag_errs = _ensure_tag_shape(raw.get("persona_tags"))
    if not ok:
        errs.extend(tag_errs)

    # constraints required minimum shape
    ok, c_errs = _ensure_constraints_min_shape(raw.get("constraints"))
    if not ok:
        errs.extend(c_errs)

    # composition required shape
    comp = raw.get("composition")
    if not isinstance(comp, dict):
        errs.append("composition must be an object")
    else:
        for k in ("compatible_personae", "excluded_personae"):
            v = comp.get(k)
            if v is None:
                errs.append(f"composition.{k} required (use [] if empty)")
            elif not isinstance(v, list):
                errs.append(f"composition.{k} must be a list")
            elif v != [] and not _is_list_of_str(v):
                errs.append(f"composition.{k} must be a list of strings")
        rule = comp.get("composition_rule")
        if rule is None:
            errs.append("composition.composition_rule required")
        elif not isinstance(rule, str):
            errs.append("composition.composition_rule must be a string")

    # strict SAID binding
    if strict_said and _is_str(card.d):
        expected = compute_persona_said_v1(raw)
        if card.d != expected:
            errs.append("d mismatch (SAID does not bind to derivation bytes)")

    # If template is provided, enforce invariants + ref match
    if template is not None:
        tmpl_said = template.d.strip()
        if tmpl_said and card.card_template_ref.strip() and card.card_template_ref.strip() != tmpl_said:
            errs.append("card_template_ref does not match provided template SAID")

        # enforce invariant-dependent rules
        inv = template.invariants
        if isinstance(inv, dict):
            # tags subset rule
            if inv.get("tags_must_be_subset_of_constraints") is True:
                tags = raw.get("persona_tags")
                constraints = raw.get("constraints")
                ok2, subset_errs = _tags_subset_of_constraints(
                    tags if isinstance(tags, dict) else {},
                    constraints if isinstance(constraints, dict) else {},
                )
                if not ok2:
                    errs.extend(subset_errs)

            # composition semantics locked
            if inv.get("composition_is_intersection_only") is True:
                rule = str(card.composition.get("composition_rule") or "").strip()
                if rule != "intersection_only":
                    errs.append("template invariant requires composition.composition_rule='intersection_only'")

            # grammar locked: instance grammar_ref must match template grammar_ref
            if inv.get("constraint_grammar_locked") is True:
                if template.constraint_grammar_ref.strip() and card.constraint_grammar_ref.strip():
                    if card.constraint_grammar_ref.strip() != template.constraint_grammar_ref.strip():
                        errs.append("constraint_grammar.grammar_ref must match template (constraint_grammar_locked=true)")

    return ValidationResult(ok=(len(errs) == 0), errors=errs)

