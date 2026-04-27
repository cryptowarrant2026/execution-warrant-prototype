# app.py
from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

from src.said_python import embed_said

from src.warden_plane import (
    warden_admit,            # used by agent_operation / CLI path if you still call it anywhere
    warden_check,            # used by preflight (pure check)
    warden_verify_signature, # used by preflight signature verify
)

from src.authority_policy import (
    create_authority_policy,
    delete_authority_policy,
)

from src.role_card import (
    RoleCardV1,
    RoleTemplateV1,
    validate_role_card_v1,
    validate_role_template_v1,
)

from src.persona_card import (
    PersonaCardV1,
    PersonaTemplateV1,
    validate_persona_card_v1,
    validate_persona_template_v1,
)

from src.operation_card import (
    OperationCardV1,
    OperationTemplateV1,
    ValidationResult,
    load_operation_card,
    load_operation_template,
    validate_operation_card_v1,
    validate_operation_template_v1,
)

from src.action_intent import (
    ActionIntentV1,
    ValidationResult as ActionIntentValidationResult,
    validate_action_intent_v1,
    canonical_action_intent_digest_v1,
    build_action_intent_v1,
    resolve_action_intent_id_or_d_to_intent,
)


ROOT = Path(__file__).resolve().parent
WARDS_DIR = ROOT / "wards"
TEMPLATES_DIR = ROOT / "templates"


# ============================================================
# Template layout (authoring vs SAID-bound)
# ============================================================

# Human-authored source templates (editable)
TEMPLATE_SOURCES_DIR = TEMPLATES_DIR / "sources"

ROLE_TEMPLATE_PATH = TEMPLATE_SOURCES_DIR / "role_card_v1_template.json"
PERSONA_TEMPLATE_PATH = TEMPLATE_SOURCES_DIR / "persona_card_v1_template.json"
OPERATION_TEMPLATE_PATH = TEMPLATE_SOURCES_DIR / "operation_card_v1_template.json"

CONSTRAINT_GRAMMAR_PATH = TEMPLATE_SOURCES_DIR / "rbc_constraint_grammar_dpv_minimal_v1.json"
OPERATION_CONSTRAINT_GRAMMAR_PATH = TEMPLATE_SOURCES_DIR / "rbc_constraint_grammar_ocv_minimal_v1.json"


# ============================================================
# SAID-bound (canonical) templates
# ============================================================

TEMPLATE_SAID_DIR = TEMPLATES_DIR / "said"

ROLE_TEMPLATES_DIR = TEMPLATE_SAID_DIR / "roles"
PERSONA_TEMPLATES_DIR = TEMPLATE_SAID_DIR / "personas"
OPERATION_TEMPLATES_DIR = TEMPLATE_SAID_DIR / "operations"
CONSTRAINT_GRAMMARS_DIR = TEMPLATE_SAID_DIR / "constraint_grammars"

# Ensure directories exist (safe to call at import time)
for _d in (
    ROLE_TEMPLATES_DIR,
    PERSONA_TEMPLATES_DIR,
    OPERATION_TEMPLATES_DIR,
    CONSTRAINT_GRAMMARS_DIR,
):
    _d.mkdir(parents=True, exist_ok=True)

RISK_TIERS = ["low", "moderate", "high"]


# ============================================================
# Small utilities
# ============================================================

# ------------------------------------------------------------
# Flash message helpers (persist across st.rerun())
# ------------------------------------------------------------

def ui_safe_path(p: Path, *, base: Path) -> str:
    """
    UI-safe display string for paths:
    - prefer repo-relative paths
    - never reveal absolute paths (which can contain PII like OS usernames)
    """
    try:
        return p.resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        # Fall back to filename only (still useful, never PII)
        return p.name


def flash_success(msg: str) -> None:
    st.session_state["flash_success"] = msg


def flash_error(msg: str) -> None:
    st.session_state["flash_error"] = msg


def render_flash_messages() -> None:
    msg = st.session_state.pop("flash_success", None)
    if msg:
        st.success(msg)

    msg = st.session_state.pop("flash_error", None)
    if msg:
        st.error(msg)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def op_name_from_operation_file(ward_path: Path, op_filename: str) -> str:
    obj = read_json(ward_path / "operations" / op_filename)
    return (obj.get("operation_name") or "").strip()


def write_json(p: Path, obj: Dict[str, Any]) -> None:
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def list_ward_ids() -> List[str]:
    if not WARDS_DIR.exists():
        return []
    return sorted([p.name for p in WARDS_DIR.iterdir() if p.is_dir()])


def list_json_files(dirpath: Path) -> List[Path]:
    if not dirpath.exists():
        return []
    return sorted(dirpath.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def list_constraint_grammars() -> list[str]:
    d = Path("templates/said/constraint_grammars")
    if not d.exists():
        return []
    return sorted([p.stem for p in d.glob("*.json")])


def ensure_dirs(ward_path: Path) -> None:
    for d in [
        "roles",
        "personas",
        "operations",
        "authority_policies",
        "action_intents",
        "correspondences",
        "warrants",
        "receipts",
        "data",
        "keys",
    ]:
        (ward_path / d).mkdir(parents=True, exist_ok=True)


def get_reason(res: Any) -> str:
    if res is None:
        return "Unknown (no result)"
    if isinstance(res, str):
        return res
    if isinstance(res, dict):
        return res.get("reason") or res.get("error") or res.get("message") or json.dumps(res, indent=2)
    return str(res)


def pretty_trace(res: Any) -> List[str]:
    if not isinstance(res, dict):
        return []
    out: List[str] = []
    if "verified_signature" in res:
        out.append(f"Signature: {'✅ valid' if res['verified_signature'] else '❌ invalid'}")
    if "presence_fresh" in res:
        out.append(f"Presence: {'✅ fresh' if res['presence_fresh'] else '❌ stale'}")
    if "ward_ref" in res:
        out.append(f"Ward ref: {res['ward_ref']}")
    if "correspondence_id" in res:
        out.append(f"Correspondence: {res['correspondence_id']}")
    if "role_id" in res:
        out.append(f"Role: {res['role_id']}")
    if "persona_id" in res:
        out.append(f"Persona: {res['persona_id']}")
    if "action_intent_id" in res:
        out.append(f"Action Intent: {res['action_intent_id']}")
    if "operation_digest" in res:
        out.append(f"Operation digest: {res['operation_digest']}")
    if "operation" in res:
        out.append(f"Operation: {res['operation']}")
    return out


def _admission_key(ward_ref: str, warrant_filename: str, proposed_operation: str) -> str:
    po = (proposed_operation or "").strip()
    return f"{ward_ref}|{warrant_filename}|{po}"


def _set_last_admission(key: str, ok: bool, result: Dict[str, Any]) -> None:
    st.session_state["last_admission_key"] = key
    st.session_state["last_admission_ok"] = bool(ok)
    st.session_state["last_admission_result"] = result


def notes_from_textarea(text: str) -> List[str]:
    if not text:
        return []
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def constraint_list(label: str, key_prefix: str, default_count: int = 3) -> List[str]:
    items: List[str] = []
    st.markdown(f"**{label}**")
    count = st.number_input(
        f"Number of {label}",
        min_value=0,
        value=int(default_count or 0),
        step=1,
        key=f"{key_prefix}_count",
    )
    for i in range(int(count)):
        val = st.text_input(f"{label} #{i+1}", key=f"{key_prefix}_{i}")
        if val:
            items.append(val.strip())
    return items


def _token_payload(token_doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Support BOTH warrant token shapes:
      A) New: payload-at-top-level (current mint)
      B) Old: {"payload": {...}, "integrity": {...}}
    """
    if not isinstance(token_doc, dict):
        return {}
    p = token_doc.get("payload")
    return p if isinstance(p, dict) else token_doc


def _load_token_view(warrant_path: Path) -> Dict[str, Any]:
    """
    UI-friendly loader: returns the *payload view* of the warrant token,
    regardless of whether the file is wrapped or payload-at-top-level.
    """
    try:
        doc = read_json(warrant_path)
    except Exception:
        return {}
    return _token_payload(doc) if isinstance(doc, dict) else {}


# ============================================================
# Constraint grammar driven rendering (UI)
# ============================================================

def _get_by_path(obj: Any, path: str) -> Any:
    """Read nested dict by dotted path, e.g. 'allowed_term_sets.purpose'."""
    cur = obj
    for part in (path or "").split("."):
        if not part:
            continue
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _humanize_term(term: str) -> str:
    """UI label helper; keeps term stable but friendlier."""
    t = (term or "").strip()
    return t


def render_constraints_from_grammar_bindings(
    grammar_obj: Dict[str, Any],
    binding_key: str,
    widget_key_prefix: str,
    *,
    show_term_sets: bool = False,
) -> Dict[str, Any]:
    """
    Render UI inputs for constraints/tags based on grammar.bindings[binding_key].fields.

    Returns:
      bound_values: dict[str, Any] mapping dotted json paths -> value
      Example:
        {
          "constraints.scope": ["dpv:EmergencyResponse"],
          "role_tags.scope": ["dpv:EmergencyResponse"]
        }
    """
    if not isinstance(grammar_obj, dict):
        st.error("Constraint grammar is not a JSON object")
        return {}

    bindings = grammar_obj.get("bindings") or {}
    if not isinstance(bindings, dict):
        st.error("Constraint grammar missing 'bindings' object")
        return {}

    binding = bindings.get(binding_key) or {}
    if not isinstance(binding, dict):
        st.error(f"Constraint grammar missing bindings.{binding_key}")
        return {}

    fields = binding.get("fields") or {}
    if not isinstance(fields, dict) or not fields:
        st.warning(f"No fields found in bindings.{binding_key}.fields")
        return {}

    allowed_term_sets = grammar_obj.get("allowed_term_sets") or {}
    if not isinstance(allowed_term_sets, dict):
        allowed_term_sets = {}

    if show_term_sets:
        with st.expander("Show allowed term sets (debug)"):
            st.json(allowed_term_sets)

    bound_values: Dict[str, Any] = {}

    # Keep a stable iteration order (nice UX)
    for field_path in sorted(fields.keys()):
        spec = fields.get(field_path) or {}
        if not isinstance(spec, dict):
            continue

        term_set_key = str(spec.get("term_set") or "").strip()
        rule = str(spec.get("rule") or "").strip()
        note = str(spec.get("note") or "").strip()

        # Derive label text
        label = field_path
        help_txt_parts: List[str] = []
        if term_set_key:
            help_txt_parts.append(f"term_set: {term_set_key}")
        if rule:
            help_txt_parts.append(f"rule: {rule}")
        if note:
            help_txt_parts.append(note)
        help_txt = " | ".join(help_txt_parts) if help_txt_parts else None

        # Widget key must be unique in Streamlit
        wkey = f"{widget_key_prefix}__{binding_key}__{field_path}"

        # If we have a known term set -> multiselect from allowed terms
        if term_set_key and term_set_key in allowed_term_sets:
            options = allowed_term_sets.get(term_set_key) or []
            if not isinstance(options, list):
                options = []

            # Always treat as list-of-strings
            options = [str(x).strip() for x in options if str(x).strip()]

            # Render multiselect
            selected = st.multiselect(
                label,
                options=options,
                default=[],
                key=wkey,
                help=help_txt,
                format_func=_humanize_term,
            )
            bound_values[field_path] = list(selected)

        else:
            # Unknown term set or no term set -> allow free text list
            st.markdown(f"**{label}**")
            if help_txt:
                st.caption(help_txt)
            vals = constraint_list(label, wkey, default_count=1)
            bound_values[field_path] = list(vals)

    return bound_values


def _load_constraint_grammar_raw() -> Dict[str, Any]:
    """
    Load the author-authored constraint grammar JSON (with placeholder 'd').
    """
    if not CONSTRAINT_GRAMMAR_PATH.exists():
        raise FileNotFoundError(
            f"Missing constraint grammar: {CONSTRAINT_GRAMMAR_PATH}. "
            "Expected rbc_constraint_grammar_dpv_minimal_v1.json in templates/."
        )
    obj = read_json(CONSTRAINT_GRAMMAR_PATH)
    if not isinstance(obj, dict):
        raise ValueError("Constraint grammar must be a JSON object")
    return obj


def _constraint_grammar_said_and_obj() -> Tuple[str, Dict[str, Any]]:
    """
    Returns (grammar_said, grammar_obj_with_said_embedded).
    """
    raw = _load_constraint_grammar_raw()
    saided = embed_said(raw)  # uses your SAID embedding (d-field)
    said = str(saided.get("d") or "").strip()
    if not said:
        raise ValueError("Constraint grammar SAID computation failed (missing 'd').")
    return said, saided


def persist_constraint_grammar_saided(grammar_obj_with_said: Dict[str, Any]) -> Path:
    """
    Persist the SAIDed grammar as a first-class object:
      templates/constraint_grammars/<SAID>.json
    """
    if not isinstance(grammar_obj_with_said, dict):
        raise TypeError("grammar_obj_with_said must be a dict")

    said = str(grammar_obj_with_said.get("d") or "").strip()
    if not said:
        raise ValueError("grammar_obj_with_said missing 'd' (SAID)")

    CONSTRAINT_GRAMMARS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CONSTRAINT_GRAMMARS_DIR / f"{said}.json"

    # Idempotent write
    if not out_path.exists():
        write_json(out_path, grammar_obj_with_said)

    return out_path


def allowed_terms(grammar_obj: Dict[str, Any], term_set: str) -> List[str]:
    """
    Convenience: returns grammar.allowed_term_sets.<term_set> as a list[str].
    """
    ats = grammar_obj.get("allowed_term_sets")
    if not isinstance(ats, dict):
        return []
    v = ats.get(term_set, [])
    if not isinstance(v, list):
        return []
    return [t.strip() for t in v if isinstance(t, str) and t.strip()]


def _gen_ai_action_intent_id() -> None:
    st.session_state["ai_action_intent_id"] = str(uuid.uuid4())
    st.session_state["ai_action_intent_id_msg"] = "generated"


def _get_nested(d: Any, *keys: str) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _parse_correspondence_ids(raw: Any) -> Tuple[str, str, str, List[str]]:
    """
    Best-effort extractor for IDs from a correspondence form.
    This intentionally tolerates small schema variations so the UI stays robust.
    """
    errs: List[str] = []
    if not isinstance(raw, dict):
        return "", "", "", ["correspondence is not a JSON object"]

    role_id = str(
        _get_nested(raw, "relationships", "role", "role_id")
        or raw.get("role_id")
        or _get_nested(raw, "role", "role_id")
        or ""
    ).strip()

    persona_id = str(
        _get_nested(raw, "relationships", "persona", "persona_id")
        or raw.get("persona_id")
        or _get_nested(raw, "persona", "persona_id")
        or ""
    ).strip()

    action_intent_id = str(
        _get_nested(raw, "relationships", "action_intent", "action_intent_id")
        or raw.get("action_intent_id")
        or _get_nested(raw, "action_intent", "action_intent_id")
        or ""
    ).strip()

    if not role_id:
        errs.append("missing role_id")
    if not persona_id:
        errs.append("missing persona_id")
    if not action_intent_id:
        errs.append("missing action_intent_id")

    return role_id, persona_id, action_intent_id, errs


# ============================================================
# Ward helpers
# ============================================================

def _require_ward_ref(ward_path: Path) -> Tuple[Dict[str, Any], str]:
    manifest_path = ward_path / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing manifest.json at {manifest_path}")

    manifest = read_json(manifest_path)
    ward_ref = (manifest.get("ward_ref") or "").strip()
    if not ward_ref:
        raise ValueError("manifest.json missing ward_ref (tombed ward required)")
    return manifest, ward_ref


def _append_manifest_index(ward_path: Path, index_key: str, entry: Dict[str, Any]) -> None:
    manifest_path = ward_path / "manifest.json"
    if not manifest_path.exists():
        return
    manifest = read_json(manifest_path)
    manifest.setdefault(index_key, [])
    if isinstance(manifest[index_key], list):
        manifest[index_key].append(entry)
    write_json(manifest_path, manifest)


def _remove_manifest_index_by_id(ward_path: Path, index_key: str, id_key: str, id_val: str) -> None:
    manifest_path = ward_path / "manifest.json"
    if not manifest_path.exists():
        return
    manifest = read_json(manifest_path)
    idx = manifest.get(index_key, [])
    if isinstance(idx, list):
        manifest[index_key] = [x for x in idx if x.get(id_key) != id_val]
        write_json(manifest_path, manifest)


# ============================================================
# Artifact helpers: Role Card v1 (UPDATED to new v1.0 template)
# ============================================================

def create_role_card(
    ward_path: Path,
    role_name: str,
    role_label: str,
    role_description: str,
    accountable_office_ref: str,
    accountable_office_label: str,
    accountable_office_resolution_scope: str,  # "public" | "private"
    # NEW: values pre-rendered from constraint grammar bindings (path -> value)
    bound_values: Optional[Dict[str, Any]] = None,
) -> str:
    _manifest, _ward_ref = _require_ward_ref(ward_path)
    ensure_dirs(ward_path)

    template_said, _tmpl_obj = _role_template_said_and_obj()

    grammar_said, g_obj = _constraint_grammar_said_and_obj()
    try:
        persist_constraint_grammar_saided(g_obj)
    except Exception:
        pass

    # Enforce template ↔ grammar binding (template invariant)
    tmpl_grammar_ref = str(
        tmpl_obj.get("constraint_grammar", {}).get("grammar_ref") or ""
    ).strip()

    if tmpl_grammar_ref and tmpl_grammar_ref != grammar_said:
        raise ValueError(
            "Template grammar_ref mismatch (template not bound to shared grammar SAID)"
        )

    role_name = (role_name or "").strip()
    role_label = (role_label or "").strip()
    role_description = (role_description or "").strip()

    accountable_office_ref = (accountable_office_ref or "").strip()
    accountable_office_label = (accountable_office_label or "").strip()
    accountable_office_resolution_scope = (accountable_office_resolution_scope or "").strip()

    bound_values = bound_values or {}

    # Build role instance with placeholder SAID and EMPTY bound fields (filled by apply_bound_values)
    role_obj: Dict[str, Any] = {
        "d": "#" * 44,
        "type": "cca/agency/concept/1.0",
        "card_type": "role",
        "card_template_ref": template_said,
        "constraint_grammar": {"grammar_ref": grammar_said},
        "status": "active",
        "role": {
            "name": role_name,
            "label": role_label,
            "description": role_description,
        },
        "accountable_office": {
            "ref": accountable_office_ref,
            "label": accountable_office_label,
            "resolution_scope": accountable_office_resolution_scope,
        },

        # Bound-by-grammar fields: placeholders only
        "role_tags": {
            "scope": [],
            "prohibitions": [],
        },
        "permissible_effects": [],
        "constraints": {
            "scope": [],
            "prohibitions": [],
            "obligations": [],
        },
        "invariants": {
            "non_authorizing": True,
        },
    }

    # Apply values coming from grammar bindings (UI-driven)
    role_obj = apply_bound_values(role_obj, bound_values)
    role_said = str(role_obj.get("d") or "").strip()
    if not role_said:
        raise ValueError("Failed to compute SAID for Role Card (missing 'd').")

    card = RoleCardV1(role_obj)
    v = validate_role_card_v1(card, template=None, strict_said=True)
    if not v.ok:
        raise ValueError("Role Card failed validation: " + "; ".join(v.errors))

    role_path = ward_path / "roles" / f"{role_said}.json"
    if role_path.exists():
        return role_said

    write_json(role_path, role_obj)

    _append_manifest_index(
        ward_path,
        "role_index",
        {
            "role_said": role_said,
            "role_name": role_name,
            "role_label": role_label,
            "card_template_ref": template_said,
            "constraint_grammar_ref": grammar_said,  # optional but useful
            "path": f"roles/{role_said}.json",
            "created_at": utc_now_iso(),
        },
    )
    return role_said


def delete_role(ward_path: Path, role_filename: str) -> None:
    role_path = ward_path / "roles" / role_filename
    if not role_path.exists():
        return
    role_obj = read_json(role_path)
    role_said = str(role_obj.get("d") or "").strip()
    role_path.unlink()
    if role_said:
        _remove_manifest_index_by_id(ward_path, "role_index", "role_said", role_said)


def _load_role_template_raw() -> Dict[str, Any]:
    """
    Loads the Role Card template JSON from disk (with placeholder 'd').
    """
    if not ROLE_TEMPLATE_PATH.exists():
        raise FileNotFoundError(
            f"Missing template: {ROLE_TEMPLATE_PATH}. "
            "Expected role_card_v1_template.json at repo root."
        )
    obj = read_json(ROLE_TEMPLATE_PATH)
    if not isinstance(obj, dict):
        raise ValueError("role_card_v1_template.json must be a JSON object")
    return obj


def _role_template_said_and_obj() -> Tuple[str, Dict[str, Any]]:
    """
    Returns (template_said, template_obj_with_said_embedded).
    Ensures constraint_grammar.grammar_ref is bound to the shared Constraint Grammar SAID
    BEFORE SAIDing + validating the template.
    """
    raw = _load_role_template_raw()

    # Inject the shared constraint grammar SAID into the template prior to SAIDing
    grammar_said, _g_obj = _constraint_grammar_said_and_obj()
    cg = raw.get("constraint_grammar")
    if not isinstance(cg, dict):
        cg = {}
        raw["constraint_grammar"] = cg
    cg["grammar_ref"] = grammar_said

    # Now SAID the template (template SAID commits to grammar_ref)
    saided = embed_said(raw)
    tmpl = RoleTemplateV1(saided)

    v = validate_role_template_v1(tmpl, strict_said=True)
    if not v.ok:
        raise ValueError("Role Template failed validation: " + "; ".join(v.errors))

    return str(saided.get("d") or "").strip(), saided


def persist_role_template_saided(template_obj_with_said: Dict[str, Any]) -> Path:
    """
    Persist the SAIDed Role Template as a first-class object:
      templates/roles/<SAID>.json

    Returns the path written (or existing).
    """
    if not isinstance(template_obj_with_said, dict):
        raise TypeError("template_obj_with_said must be a dict")

    said = str(template_obj_with_said.get("d") or "").strip()
    if not said:
        raise ValueError("template_obj_with_said missing 'd' (SAID)")

    ROLE_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ROLE_TEMPLATES_DIR / f"{said}.json"

    # Idempotent write: if it already exists, don't overwrite
    if not out_path.exists():
        write_json(out_path, template_obj_with_said)

    return out_path


# ============================================================
# Artifact helpers: Persona Card v1 (UPDATED to new v1.0 template)
# ============================================================

def create_persona_card(
    ward_path: Path,
    persona_name: str,
    persona_label: str,
    persona_description: str,
    duty_frame_ref: str,
    duty_frame_label: str,
    duty_frame_resolution_scope: str,  # "public" | "private"
    composition_rule: str = "intersection_only",
    # NEW: values pre-rendered from constraint grammar bindings (path -> value)
    bound_values: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Creates a Persona Card instance (card_type='persona') and stores it as:
      wards/<WARD_HANDLE>/personas/<SAID>.json

    - card_template_ref is automatically set to the SAID of PERSONA_TEMPLATE_PATH.
    - SAID is embedded on create.
    - invariants are TEMPLATE ONLY (not repeated in instance).
    """
    _manifest, _ward_ref = _require_ward_ref(ward_path)
    ensure_dirs(ward_path)

    template_said, tmpl_obj = _persona_template_said_and_obj()

    grammar_said, g_obj = _constraint_grammar_said_and_obj()
    try:
        persist_constraint_grammar_saided(g_obj)
    except Exception:
        pass

    # Enforce template ↔ grammar binding (template invariant)
    tmpl_grammar_ref = str(
        tmpl_obj.get("constraint_grammar", {}).get("grammar_ref") or ""
    ).strip()

    if tmpl_grammar_ref and tmpl_grammar_ref != grammar_said:
        raise ValueError(
            "Template grammar_ref mismatch (template not bound to shared grammar SAID)"
        )

    # Normalize strings
    persona_name = (persona_name or "").strip()
    persona_label = (persona_label or "").strip()
    persona_description = (persona_description or "").strip()

    duty_frame_ref = (duty_frame_ref or "").strip()
    duty_frame_label = (duty_frame_label or "").strip()
    duty_frame_resolution_scope = (duty_frame_resolution_scope or "").strip()

    bound_values = bound_values or {}

    # Build instance with placeholder SAID and EMPTY bound fields (filled by apply_bound_values)
    persona_obj: Dict[str, Any] = {
        "d": "#" * 44,
        "type": "cca/agency/concept/1.0",
        "card_type": "persona",
        "status": "active",
        "card_template_ref": template_said,
        "persona": {
            "name": persona_name,
            "label": persona_label,
            "description": persona_description,
        },
        "duty_frame": {
            "ref": duty_frame_ref,
            "label": duty_frame_label,
            "resolution_scope": duty_frame_resolution_scope,
        },

        # IMPORTANT: bind to shared grammar SAID (and keep template locked via invariant)
        "constraint_grammar": {
            "grammar_ref": grammar_said,
        },

        # Bound-by-grammar fields: placeholders only
        "persona_tags": {
            "purpose": [],
            "behavior": [],
            "privacy": [],
            "integrity": [],
            "tooling": [],
        },

        "constraints": {
            "purpose": [],
            "behavior": [],
            "privacy_boundaries": {
                "data_categories_allow": [],
                "data_categories_block": [],
                "allowed_sources": [],
                "prohibited_sources": [],
                "allowed_sinks": [],
                "prohibited_sinks": [],
                "prohibited_transformations": [],
                "retention_limits": [],
                "correlation_limits": [],
            },
            "tooling_modality": {
                "allowed_tools": [],
                "prohibited_tools": [],
                "allowed_channels": [],
                "prohibited_channels": [],
                "modality_limits": [],
                "human_in_loop": [],
            },
            "time_scope_budget": {
                "max_execution_duration_seconds": None,
                "execution_windows": [],
                "rate_limits": [],
                "scope_limits": [],
                "resource_limits": [],
            },
            "integrity": [],
        },

        "composition": {
            "compatible_personae": [],
            "excluded_personae": [],
            "composition_rule": (composition_rule or "intersection_only").strip() or "intersection_only",
        },
    }

    # Apply values coming from grammar bindings (UI-driven)
    persona_obj = apply_bound_values(persona_obj, bound_values)
    persona_said = str(persona_obj.get("d") or "").strip()
    if not persona_said:
        raise ValueError("Failed to compute SAID for Persona Card (missing 'd').")

    # Validate instance against template invariants (locked grammar, subset tags, etc.)
    tmpl = PersonaTemplateV1(tmpl_obj)
    card = PersonaCardV1(persona_obj)
    v = validate_persona_card_v1(card, template=tmpl, strict_said=True)
    if not v.ok:
        raise ValueError("Persona Card failed validation: " + "; ".join(v.errors))

    # Write with SAID filename
    out_path = ward_path / "personas" / f"{persona_said}.json"
    if not out_path.exists():
        write_json(out_path, persona_obj)

    # Index in manifest (optional)
    _append_manifest_index(
        ward_path,
        "persona_index",
        {
            "persona_said": persona_said,
            "persona_name": persona_name,
            "persona_label": persona_label,
            "card_template_ref": template_said,
            "constraint_grammar_ref": grammar_said,  # optional but useful
            "path": f"personas/{persona_said}.json",
            "created_at": utc_now_iso(),
        },
    )

    return persona_said


def delete_persona(ward_path: Path, persona_filename: str) -> None:
    persona_path = ward_path / "personas" / persona_filename
    if not persona_path.exists():
        return
    persona_obj = read_json(persona_path)
    persona_said = str(persona_obj.get("d") or "").strip()
    persona_path.unlink()
    if persona_said:
        _remove_manifest_index_by_id(ward_path, "persona_index", "persona_said", persona_said)


def _load_persona_template_raw() -> Dict[str, Any]:
    """
    Loads the Persona Card template JSON from disk (with placeholder 'd').
    """
    if not PERSONA_TEMPLATE_PATH.exists():
        raise FileNotFoundError(
            f"Missing template: {PERSONA_TEMPLATE_PATH}. "
            "Expected persona_card_v1_template.json under src/."
        )
    obj = read_json(PERSONA_TEMPLATE_PATH)
    if not isinstance(obj, dict):
        raise ValueError("persona_card_v1_template.json must be a JSON object")
    return obj


def _persona_template_said_and_obj() -> Tuple[str, Dict[str, Any]]:
    """
    Returns (template_said, template_obj_with_said_embedded).
    Ensures constraint_grammar.grammar_ref is bound to the shared Constraint Grammar SAID
    BEFORE SAIDing + validating the template.
    """
    raw = _load_persona_template_raw()

    # Inject the shared constraint grammar SAID into the template prior to SAIDing
    grammar_said, _g_obj = _constraint_grammar_said_and_obj()
    cg = raw.get("constraint_grammar")
    if not isinstance(cg, dict):
        cg = {}
        raw["constraint_grammar"] = cg
    cg["grammar_ref"] = grammar_said

    # Now SAID the template (template SAID commits to grammar_ref)
    saided = embed_said(raw)
    tmpl = PersonaTemplateV1(saided)

    v = validate_persona_template_v1(tmpl, strict_said=True)
    if not v.ok:
        raise ValueError("Persona Template failed validation: " + "; ".join(v.errors))

    return str(saided.get("d") or "").strip(), saided


def persist_persona_template_saided(template_obj_with_said: Dict[str, Any]) -> Path:
    """
    Persist the SAIDed Persona Template as a first-class object:
      templates/personas/<SAID>.json

    Returns the path written (or existing).
    """
    if not isinstance(template_obj_with_said, dict):
        raise TypeError("template_obj_with_said must be a dict")

    said = str(template_obj_with_said.get("d") or "").strip()
    if not said:
        raise ValueError("template_obj_with_said missing 'd' (SAID)")

    PERSONA_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PERSONA_TEMPLATES_DIR / f"{said}.json"

    if not out_path.exists():
        write_json(out_path, template_obj_with_said)

    return out_path


def _load_operation_template() -> Dict[str, Any]:
    if not OPERATION_TEMPLATE_PATH.exists():
        raise FileNotFoundError(
            f"Missing template: {OPERATION_TEMPLATE_PATH}. "
            "Expected operation_card_v1_template.json at repo root."
        )
    obj = read_json(OPERATION_TEMPLATE_PATH)
    if not isinstance(obj, dict):
        raise ValueError("operation_card_v1_template.json must be a JSON object")
    return obj


def _merge_list(dst: Dict[str, Any], path: List[str], values: List[str]) -> None:
    """
    Ensure nested path exists and set list at the final key.
    path like ["compatibility_constraints","role","scope_any_of"]
    """
    cur: Dict[str, Any] = dst
    for k in path[:-1]:
        nxt = cur.get(k)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[k] = nxt
        cur = nxt
    cur[path[-1]] = list(values)


# ============================================================
# Operation Card (v1) helpers — SAID-first, binding-agnostic
# ============================================================

def _load_operation_constraint_grammar_raw() -> Dict[str, Any]:
    if not OPERATION_CONSTRAINT_GRAMMAR_PATH.exists():
        raise FileNotFoundError(f"Missing op constraint grammar: {OPERATION_CONSTRAINT_GRAMMAR_PATH}")
    return read_json(OPERATION_CONSTRAINT_GRAMMAR_PATH)


def _operation_constraint_grammar_said_and_obj() -> Tuple[str, Dict[str, Any]]:
    """
    Returns (grammar_said, saided_obj) for the Operation constraint grammar.
    """
    raw = _load_operation_constraint_grammar_raw()
    saided = embed_said(raw)  # embeds 'd'
    said = str(saided.get("d") or "").strip()
    if not said:
        raise ValueError("Operation constraint grammar SAID failed to compute (missing 'd').")
    return said, saided


def persist_operation_constraint_grammar_saided(grammar_obj: Dict[str, Any]) -> Path:
    """
    Persist SAIDed operation constraint grammar to:
      templates/said/constraint_grammars/<SAID>.json
    """
    said = str(grammar_obj.get("d") or "").strip()
    if not said:
        raise ValueError("persist_operation_constraint_grammar_saided: missing 'd'")
    out = CONSTRAINT_GRAMMARS_DIR / f"{said}.json"
    if not out.exists():
        write_json(out, grammar_obj)
    return out


def _load_operation_template_raw() -> Dict[str, Any]:
    if not OPERATION_TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Missing operation template: {OPERATION_TEMPLATE_PATH}")
    return read_json(OPERATION_TEMPLATE_PATH)


def _operation_template_said_and_obj() -> Tuple[str, Dict[str, Any]]:
    """
    Loads operation template JSON from templates/sources/,
    injects the Operation constraint grammar SAID into:
      constraint_grammar.grammar_ref
    then returns (template_said, saided_template_obj).
    """
    grammar_said, grammar_obj = _operation_constraint_grammar_said_and_obj()

    # Persist SAIDed grammar (idempotent)
    persist_operation_constraint_grammar_saided(grammar_obj)

    tmpl = _load_operation_template_raw()

    if not isinstance(tmpl.get("constraint_grammar"), dict):
        tmpl["constraint_grammar"] = {}
    tmpl["constraint_grammar"]["grammar_ref"] = grammar_said

    saided = embed_said(tmpl)  # embeds 'd'
    said = str(saided.get("d") or "").strip()
    if not said:
        raise ValueError("Operation template SAID failed to compute (missing 'd').")
    return said, saided


def persist_operation_template_saided(template_obj: Dict[str, Any]) -> Path:
    """
    Persist SAIDed operation template to:
      templates/said/operations/<SAID>.json
    """
    said = str(template_obj.get("d") or "").strip()
    if not said:
        raise ValueError("persist_operation_template_saided: missing 'd'")
    out = OPERATION_TEMPLATES_DIR / f"{said}.json"
    if not out.exists():
        write_json(out, template_obj)
    return out


def operation_card_path(ward_path: Path, operation_said: str) -> Path:
    return ward_path / "operations" / f"{operation_said}.json"


def create_operation_card(
    *,
    ward_path: Path,
    operation_name: str,
    operation_label: str,
    operation_description: str,
    defined_by_service_ref: str,
    defined_by_label: str,
    defined_by_resolution_scope: str,
    bound_values: Dict[str, Any],
    operation_tags: Dict[str, Any],
    execution_modality: Dict[str, Any],
    required_preconditions: Dict[str, Any],
    input_boundaries: Dict[str, Any],
    processing_constraints: Dict[str, Any],
    output_constraints: Dict[str, Any],
    post_execution_handling: Dict[str, Any],
    explicit_non_goals: List[str],
    alignment_notes: Dict[str, Any],
) -> str:
    """
    Create an Operation Card INSTANCE:
      - binding-agnostic (no implied Role/Persona bindings)
      - invariants are TEMPLATE-ONLY (instance must not include invariants)
      - file saved as wards/<ward>/operations/<SAID>.json
    """
    # Template SAID (card_template_ref) + persist SAIDed template (idempotent)
    template_said, tmpl_obj = _operation_template_said_and_obj()
    persist_operation_template_saided(tmpl_obj)

    grammar_ref = str(tmpl_obj.get("constraint_grammar", {}).get("grammar_ref") or "").strip()
    if not grammar_ref:
        raise ValueError("Operation template has no constraint_grammar.grammar_ref after injection.")

    base = {
        "d": "############################################",
        "type": "cca/execution/action/1.0",
        "card_type": "operation",
        "card_template_ref": template_said,
        "status": "active",
        "operation": {
            "name": (operation_name or "").strip(),
            "label": (operation_label or "").strip(),
            "description": (operation_description or "").strip(),
        },
        "constraint_grammar": {"grammar_ref": grammar_ref},
        "operation_tags": dict(operation_tags or {}),
        "defined_by": {
            "service_ref": (defined_by_service_ref or "").strip(),
            "label": (defined_by_label or "").strip(),
            "resolution_scope": (defined_by_resolution_scope or "public").strip(),
        },
        "execution_modality": dict(execution_modality or {}),
        "required_preconditions": dict(required_preconditions or {}),
        "input_boundaries": dict(input_boundaries or {}),
        "processing_constraints": dict(processing_constraints or {}),
        "output_constraints": dict(output_constraints or {}),
        "post_execution_handling": dict(post_execution_handling or {}),
        "explicit_non_goals": list(explicit_non_goals or []),
        "alignment_notes": dict(alignment_notes or {}),
    }

    # Optional: apply grammar-driven bindings to nested paths
    if bound_values:
        apply_bound_values(base, bound_values)

    # HARD RULE: invariants are template-only
    base.pop("invariants", None)

    # SAID last
    saided = embed_said(base)
    op_said = str(saided.get("d") or "").strip()
    if not op_said:
        raise ValueError("Operation instance SAID failed to compute (missing 'd').")

    # Validate (binding-agnostic): shape/type only, no Role/Persona resolution
    from src.operation_card import OperationCardV1, validate_operation_card_v1  # local import to avoid cycles
    v = validate_operation_card_v1(OperationCardV1(saided))
    if not v.ok:
        raise ValueError("Operation Card failed validation: " + "; ".join(v.errors))

    # Write
    op_path = operation_card_path(ward_path, op_said)
    op_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(op_path, saided)

    # Manifest index (optional, but consistent with your other create flows)
    _append_manifest_index(
        ward_path,
        "operation_index",
        {
            "operation_said": op_said,
            "operation_name": saided["operation"]["name"],
            "label": saided["operation"]["label"],
            "path": f"operations/{op_said}.json",
            "created_at": utc_now_iso(),
        },
    )

    return op_said


def delete_operation(ward_path: Path, operation_filename: str) -> None:
    """
    Hard delete by filename (typically <SAID>.json).
    Removes manifest index entry keyed by operation_said if present.
    """
    op_path = ward_path / "operations" / operation_filename
    if not op_path.exists():
        return

    op_obj = read_json(op_path)
    op_said = str(op_obj.get("d") or "").strip()

    op_path.unlink()

    if op_said:
        _remove_manifest_index_by_id(ward_path, "operation_index", "operation_said", op_said)


# ============================================================
# Warrant mint + agent execution hooks
# ============================================================

def correspondence_create_cli(
    ward_handle: str,
    role_id: str,
    persona_id: str,
    action_intent_id: str,
) -> str:
    """
    Creates a correspondence form that locks Role + Persona + Action Intent together.

    Expects src.correspondence_create CLI to accept:
      <WARD_HANDLE> <ROLE_ID> <PERSONA_ID> <ACTION_INTENT_ID>

    And to print ONLY the correspondence_id to stdout.
    """
    import sys

    ward_handle = (ward_handle or "").strip()
    role_id = (role_id or "").strip()
    persona_id = (persona_id or "").strip()
    action_intent_id = (action_intent_id or "").strip()

    if not ward_handle:
        raise ValueError("correspondence_create_cli: ward_handle is required")
    if not role_id:
        raise ValueError("correspondence_create_cli: role_id is required")
    if not persona_id:
        raise ValueError("correspondence_create_cli: persona_id is required")
    if not action_intent_id:
        raise ValueError("correspondence_create_cli: action_intent_id is required")

    cmd = [
        sys.executable,
        "-m",
        "src.correspondence_create",
        ward_handle,
        role_id,
        persona_id,
        action_intent_id,
    ]

    proc = subprocess.run(cmd, text=True, capture_output=True, cwd=str(ROOT))
    if proc.returncode != 0:
        raise RuntimeError(
            "correspondence_create failed\n"
            f"CMD: {' '.join(cmd)}\n\n"
            f"STDOUT:\n{proc.stdout}\n\n"
            f"STDERR:\n{proc.stderr}"
        )

    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError("correspondence_create produced no output (expected correspondence_id on stdout)")
    return out


def mint_warrant_cli(
    ward_handle: str,
    correspondence_id: str,
    ttl: int,
) -> str:
    """
    Correspondence-centric mint wrapper.

    Expects src.warrant_mint CLI to accept:
      <WARD_HANDLE> <CORRESPONDENCE_ID> <TTL_SECONDS>

    And to print ONLY the warrant_id to stdout.
    """
    import sys

    ward_handle = (ward_handle or "").strip()
    correspondence_id = (correspondence_id or "").strip()
    ttl = int(ttl)

    if not ward_handle:
        raise ValueError("mint_warrant_cli: ward_handle is required")
    if not correspondence_id:
        raise ValueError("mint_warrant_cli: correspondence_id is required")
    if ttl <= 0:
        raise ValueError("mint_warrant_cli: ttl must be > 0")

    cmd = [
        sys.executable,
        "-m",
        "src.warrant_mint",
        ward_handle,
        correspondence_id,
        str(ttl),
    ]

    proc = subprocess.run(cmd, text=True, capture_output=True, cwd=str(ROOT))
    if proc.returncode != 0:
        raise RuntimeError(
            "warrant_mint failed\n"
            f"CMD: {' '.join(cmd)}\n\n"
            f"STDOUT:\n{proc.stdout}\n\n"
            f"STDERR:\n{proc.stderr}"
        )

    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError("warrant_mint produced no output (expected warrant_id on stdout)")
    return out


def run_agent_operation_cli(ward_handle: str, warrant_id: str) -> str:
    import sys

    cmd = [sys.executable, "-m", "src.agent_operation", ward_handle, warrant_id]
    proc = subprocess.run(cmd, text=True, capture_output=True, cwd=str(ROOT))

    if proc.returncode != 0:
        raise RuntimeError(
            "agent_operation failed\n"
            f"CMD: {' '.join(cmd)}\n\n"
            f"STDOUT:\n{proc.stdout}\n\n"
            f"STDERR:\n{proc.stderr}"
        )

    return (proc.stdout or "").strip()


# ============================================================
# Operation Card loading for UI (validated)
# ============================================================

def list_operation_cards_v1(ward_path: Path) -> List[Tuple[OperationCardV1, ValidationResult, Path]]:
    out: List[Tuple[OperationCardV1, ValidationResult, Path]] = []
    for p in list_json_files(ward_path / "operations"):
        try:
            raw = read_json(p)
            card = OperationCardV1(raw)
            v = validate_operation_card_v1(card)
            out.append((card, v, p))
        except Exception as e:
            card = OperationCardV1({"operation_name": p.stem, "status": "disabled", "schema": "", "schema_version": ""})
            out.append((card, ValidationResult(ok=False, errors=[f"not readable: {e}"]), p))
    return out


def active_valid_operation_cards(ward_path: Path) -> List[OperationCardV1]:
    """
    Operation Cards are provider-authored capability declarations and are not ward-scoped.
    Ward anchoring happens via warrant payload ward_ref + Warden checks, not via the card.
    """
    cards: List[OperationCardV1] = []
    for card, v, _p in list_operation_cards_v1(ward_path):
        if not v.ok:
            continue
        if (card.status or "").strip() != "active":
            continue
        cards.append(card)

    cards.sort(key=lambda c: (c.operation_name or "").strip())
    return cards


# ============================================================
# Action-intent loading for UI (validated) — stable + UI-friendly
# ============================================================

@dataclass(frozen=True)
class ActionIntentRecord:
    """UI-safe wrapper so unreadable files don't crash the UI."""
    intent: Optional["ActionIntentV1"]
    validation: "ActionIntentValidationResult"
    path: Path
    mtime: float  # for stable sorting / recency ordering


def list_action_intents_v1(
    ward_path: Path,
) -> List[ActionIntentRecord]:
    out: List[ActionIntentRecord] = []

    intents_dir = ward_path / "action_intents"
    if (not intents_dir.exists()) or (not intents_dir.is_dir()):
        return out

    for p in list_json_files(intents_dir):
        try:
            raw = read_json(p)
            intent = ActionIntentV1(raw)
            v = validate_action_intent_v1(intent)
            out.append(
                ActionIntentRecord(
                    intent=intent,
                    validation=v,
                    path=p,
                    mtime=p.stat().st_mtime,
                )
            )
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            out.append(
                ActionIntentRecord(
                    intent=None,
                    validation=ActionIntentValidationResult(ok=False, errors=[f"not readable: {msg}"]),
                    path=p,
                    mtime=p.stat().st_mtime if p.exists() else 0.0,
                )
            )

    out.sort(key=lambda r: r.mtime, reverse=True)
    return out


def active_proposed_action_intents(ward_path: Path) -> List["ActionIntentV1"]:
    """Return validated intents that are in 'proposed' state, newest first."""
    records = list_action_intents_v1(ward_path)

    intents: List["ActionIntentV1"] = []
    for r in records:
        if not r.validation.ok:
            continue
        if r.intent is None:
            continue
        if (getattr(r.intent, "status", "") or "").strip() != "proposed":
            continue
        intents.append(r.intent)

    return intents


def _as_dict(obj: Any) -> Dict[str, Any]:
    # Accept either a dict, or a model-like object with .raw
    if isinstance(obj, dict):
        return obj
    raw = getattr(obj, "raw", None)
    if isinstance(raw, dict):
        return raw
    return {}


def _norm_digest(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("sha256:"):
        return s.split("sha256:", 1)[1].strip()
    return s


def operation_compat_with_role_persona(op: OperationCardV1, role_obj: Any, persona_obj: Any) -> Tuple[bool, List[str]]:
    reasons: List[str] = []

    role_d = _as_dict(role_obj)
    persona_d = _as_dict(persona_obj)

    role_name = ((role_d.get("role") or {}).get("name") or "").strip()
    persona_name = ((persona_d.get("persona") or {}).get("name") or "").strip()

    # OperationCardV1 is model-like; its JSON is in op.raw
    op_raw = getattr(op, "raw", {}) or {}
    rp = op_raw.get("required_preconditions") or {}

    required_roles = [str(s).strip() for s in (rp.get("required_roles") or []) if str(s).strip()]
    required_persona = [str(s).strip() for s in (rp.get("required_persona_context") or []) if str(s).strip()]
    compatible_personae = [str(s).strip() for s in (rp.get("compatible_persona_contexts") or []) if str(s).strip()]

    # Gate A: role match (if declared)
    if required_roles:
        if not role_name:
            reasons.append("Selected Role missing role.role.name (cannot satisfy operation.required_roles).")
        elif role_name not in required_roles:
            reasons.append(f"Role '{role_name}' not in operation.required_roles {required_roles}.")

    # Gate B: persona match
    if required_persona:
        if not persona_name:
            reasons.append("Selected Persona missing persona.persona.name (cannot satisfy required_persona_context).")
        elif persona_name not in required_persona:
            reasons.append(f"Persona '{persona_name}' not in operation.required_persona_context {required_persona}.")
    elif compatible_personae:
        if not persona_name:
            reasons.append("Selected Persona missing persona.persona.name (cannot satisfy compatible_persona_contexts).")
        elif persona_name not in compatible_personae:
            reasons.append(f"Persona '{persona_name}' not in operation.compatible_persona_contexts {compatible_personae}.")

    return (len(reasons) == 0), reasons


# ============================================================
# Streamlit app
# ============================================================

st.set_page_config(
    page_title="Role-Based Containment™ (RBC) Execution Workbench",
    layout="wide",
)

title_col, logo_col = st.columns([9, 1])

with title_col:
    st.markdown(
        "# Role-Based Containment™ (RBC) Execution Workbench",
        unsafe_allow_html=False,
    )
    st.caption(
        "Ward → Identity & Authority (Role + Persona + Operation) "
        "→ Intent & Consent (Action Intent + Correspondence) "
        "→ Mint Warrant "
        "→ Warden Gate (single-use) "
        "→ Execute → Receipt"
    )

with logo_col:
    st.image(
        "assets/rbc_logo.png",  # adjust path as needed
        width=140,
    )

render_flash_messages()

ward_ids = list_ward_ids()
if not ward_ids:
    st.warning("No wards found. (Create one from CLI first.)")
    st.stop()

ward_handle = st.selectbox("Select Ward", ward_ids)

# Helper line (UI microcopy)
st.caption("The Ward is the protected locus of agency and context in which actions are evaluated.")

ward_path = WARDS_DIR / ward_handle
ensure_dirs(ward_path)

manifest_path = ward_path / "manifest.json"
manifest: Dict[str, Any] = read_json(manifest_path) if manifest_path.exists() else {}
ward_ref = (manifest.get("ward_ref") or "").strip()


# ----------------------------
# Create protocol artifacts (Authority Policy / Role Card / Persona Card / Operation Card)
# ----------------------------

st.subheader("Create protocol artifacts")
st.caption("Durable, stewarded definitions that govern lawful execution without participating in any execution attempt.")

with st.expander("Create Authority Policy"):
    st.caption(
        "Create a Ward-Scoped Authority Policy (governance envelope). "
        "On Create, a SAID is embedded and the file is saved as authority_policies/<SAID>.json"
    )

    # --- Core metadata ---
    ap_label = st.text_input(
        "policy.label",
        value="Emergency break-glass: allergy read-only",
        key="ap_label",
    )
    ap_description = st.text_area(
        "policy.description",
        value="Allow read-only allergy disclosure under verified emergency treatment context.",
        key="ap_desc",
        height=90,
    )

    ap_profile = st.selectbox(
        "policy.profile",
        options=["break_glass", "routine", "governance", "custom"],
        index=0,
        key="ap_profile",
        help="A lightweight grouping label for policy intent (non-authorizing metadata).",
    )

    ap_status = st.selectbox(
        "status",
        options=["active", "disabled", "deprecated"],
        index=0,
        key="ap_status",
    )

    ap_fail_closed = st.checkbox(
        "fail_closed",
        value=True,
        key="ap_fail_closed",
        help="If true, deny when constraints cannot be evaluated.",
    )

    # --- TTL controls ---
    ttl_seconds_default = st.number_input(
        "ttl_seconds_default (0 = unset)",
        min_value=0,
        value=900,
        step=60,
        key="ap_ttl_seconds_default",
        help="Typical break-glass TTL: 10–15 minutes (e.g., 600–900 seconds).",
    )

    ttl_seconds_max = st.number_input(
        "ttl_seconds_max (0 = unset)",
        min_value=0,
        value=0,
        step=60,
        key="ap_ttl_seconds_max",
        help="Optional hard max TTL. Leave 0 to unset.",
    )

    ttl_default = int(ttl_seconds_default) if int(ttl_seconds_default) > 0 else None
    ttl_max = int(ttl_seconds_max) if int(ttl_seconds_max) > 0 else None

    # --- SAID-based triggers (ward-local) ---
    role_saids = [p.stem for p in list_json_files(ward_path / "roles")]
    persona_saids = [p.stem for p in list_json_files(ward_path / "personas")]
    operation_saids = [p.stem for p in list_json_files(ward_path / "operations")]

    # --- Grammar defaults (UI convenience) ---
    grammar_options_privacy = list_constraint_grammars()
    grammar_options_execution = list_constraint_grammars()

    DEFAULT_PRIVACY_GRAMMAR_SAID = "EFpXm7q67EDyiuXTDwKNdBWaKLZEgeGFxCm6RgGXk_MF"
    DEFAULT_EXECUTION_GRAMMAR_SAID = "EDULFtjSGk0kLd7EpFN8dZJ9_UmvTVphTbiCt3OlIFsc"

    privacy_default_idx = (
        grammar_options_privacy.index(DEFAULT_PRIVACY_GRAMMAR_SAID)
        if DEFAULT_PRIVACY_GRAMMAR_SAID in grammar_options_privacy
        else 0
    )
    execution_default_idx = (
        grammar_options_execution.index(DEFAULT_EXECUTION_GRAMMAR_SAID)
        if DEFAULT_EXECUTION_GRAMMAR_SAID in grammar_options_execution
        else 0
    )

    privacy_grammar_ref = st.selectbox(
        "Privacy Grammar (SAID)",
        options=grammar_options_privacy,
        index=privacy_default_idx,
        key="ap_privacy_grammar_ref",
    )
    execution_grammar_ref = st.selectbox(
        "Execution Grammar (SAID)",
        options=grammar_options_execution,
        index=execution_default_idx,
        key="ap_execution_grammar_ref",
    )

    role_refs = st.multiselect(
        "role_refs (SAIDs)",
        options=sorted(role_saids),
        default=[],
        key="ap_role_refs",
    )
    persona_refs = st.multiselect(
        "persona_refs (SAIDs)",
        options=sorted(persona_saids),
        default=[],
        key="ap_persona_refs",
    )
    operation_refs = st.multiselect(
        "operation_refs (SAIDs)",
        options=sorted(operation_saids),
        default=[],
        key="ap_operation_refs",
    )

    if st.button("Create Authority Policy", key="btn_create_authority_policy"):
        try:
            said = create_authority_policy(
                ward_path=ward_path,
                label=(ap_label or "").strip(),
                description=(ap_description or "").strip(),
                profile=(ap_profile or "").strip(),          # NEW (requires create_authority_policy support)
                privacy_grammar_ref=privacy_grammar_ref,
                execution_grammar_ref=execution_grammar_ref,
                role_refs=role_refs,
                persona_refs=persona_refs,
                operation_refs=operation_refs,
                ttl_seconds_default=ttl_default,
                ttl_seconds_max=ttl_max,
                status=ap_status,
                fail_closed=bool(ap_fail_closed),           # NEW (requires create_authority_policy support)
            )
            flash_success(f"✅ Created Authority Policy (SAID): {said}")
            st.rerun()
        except Exception as e:
            flash_error(f"❌ Create Authority Policy failed: {e}")
            st.rerun()


with st.expander("Create Role Card"):
    st.caption("Create a Role Card instance (card_type='role') and bind it to the Role Template via card_template_ref.")
    st.caption("On Create, a SAID is embedded and the file is saved as roles/<SAID>.json")

    # --- Load + show Role template SAID (auto) + persist SAIDed template ---
    try:
        # Role template
        template_said, _tmpl_obj = _role_template_said_and_obj()
        st.markdown("**Role Template SAID (auto)**")
        st.code(template_said, language=None)

        # Persist role template as first-class SAIDed object (best-effort)
        try:
            persisted_path = persist_role_template_saided(_tmpl_obj)
            st.caption(f"Template persisted: {ui_safe_path(persisted_path, base=ROOT)}")
        except Exception as e:
            st.warning(f"Could not persist template: {e}")

        # --- VIEW CARD TEMPLATE toggle ---
        if "show_role_template_json" not in st.session_state:
            st.session_state["show_role_template_json"] = False

        col_btn, col_spacer = st.columns([2, 4])
        with col_btn:
            if st.button("VIEW CARD TEMPLATE", key="btn_view_role_template"):
                st.session_state["show_role_template_json"] = not st.session_state["show_role_template_json"]

        if st.session_state["show_role_template_json"]:
            st.json(_tmpl_obj)

        # ----------------------------
        # Constraint grammar (shared, template-locked)
        # ----------------------------
        grammar_said, g_obj = _constraint_grammar_said_and_obj()

        # Enforce: role template must be bound to this shared grammar SAID
        tmpl_grammar_ref = str(_tmpl_obj.get("constraint_grammar", {}).get("grammar_ref") or "").strip()
        if not tmpl_grammar_ref:
            raise ValueError("Role template missing constraint_grammar.grammar_ref")
        if tmpl_grammar_ref != grammar_said:
            raise ValueError(
                "Role template grammar_ref mismatch "
                "(template not bound to shared constraint grammar SAID)"
            )

        # Match header styling with the template header
        st.markdown("**Constraint grammar (template-locked)**")
        st.code(grammar_said, language=None)

        # Persist constraint grammar as first-class SAIDed object (best-effort)
        # (Placed AFTER the field so the caption appears below it in the UI.)
        try:
            persisted_g_path = persist_constraint_grammar_saided(g_obj)
            st.caption(f"Constraint grammar persisted: {ui_safe_path(persisted_g_path, base=ROOT)}")
        except Exception as e:
            st.warning(f"Could not persist constraint grammar: {e}")

        # --- VIEW CONSTRAINT GRAMMAR toggle ---
        if "show_role_constraint_grammar_json" not in st.session_state:
            st.session_state["show_role_constraint_grammar_json"] = False

        col_g_btn, col_g_spacer = st.columns([2, 4])
        with col_g_btn:
            # Keep button text on one line by giving the button column more width
            if st.button("VIEW CONSTRAINT GRAMMAR", key="btn_view_role_constraint_grammar"):
                st.session_state["show_role_constraint_grammar_json"] = (
                    not st.session_state["show_role_constraint_grammar_json"]
                )

        if st.session_state["show_role_constraint_grammar_json"]:
            st.json(g_obj)

    except Exception as e:
        st.error(f"Role Template error: {e}")
        st.stop()

    # ----------------------------
    # Role core fields
    # ----------------------------
    st.markdown("### Role")
    role_name = st.text_input("role.name", value="treating_provider", key="new_role_name")
    role_label = st.text_input("role.label", value="Treating Provider", key="new_role_label")
    role_description = st.text_area(
        "role.description",
        value="Licensed healthcare professional operating under legally accountable emergency care authority",
        key="new_role_description",
        height=90,
    )

    # ----------------------------
    # Accountable office
    # ----------------------------
    st.markdown("### Accountable office")
    accountable_office_ref = st.text_input(
        "accountable_office.ref",
        value="UNSPECIFIED_OFFICE_REF",
        key="new_role_office_ref",
        help="A suitable identifier for the accountable office (could be DID, registry ref, etc.).",
    )
    accountable_office_label = st.text_input(
        "accountable_office.label",
        value="Licensed healthcare professional operating under legally accountable emergency care authority",
        key="new_role_office_label",
    )
    accountable_office_resolution_scope = st.selectbox(
        "accountable_office.resolution_scope",
        options=["public", "private"],
        index=0,
        key="new_role_office_scope",
    )

    # ----------------------------
    # Constraints & tags (rendered from grammar bindings)
    # ----------------------------
    st.markdown("### Constraints (from constraint grammar)")
    bound = render_constraints_from_grammar_bindings(
        grammar_obj=g_obj,
        binding_key="role_card",
        widget_key_prefix="role_bound",
    )

    # ----------------------------
    # Create button (with flash messages)
    # ----------------------------
    if st.button("Create Role", key="btn_create_role"):
        try:
            said = create_role_card(
                ward_path=ward_path,
                role_name=role_name,
                role_label=role_label,
                role_description=role_description,
                accountable_office_ref=accountable_office_ref,
                accountable_office_label=accountable_office_label,
                accountable_office_resolution_scope=accountable_office_resolution_scope,
                bound_values=bound,
            )
            flash_success(f"✅ Created Role Card (SAID): {said}")
            st.rerun()
        except Exception as e:
            flash_error(f"❌ Create Role failed: {e}")
            st.rerun()


with st.expander("Create Persona Card"):
    st.caption("Create a Persona Card instance (card_type='persona') and bind it to the Persona Template via card_template_ref.")
    st.caption("On Create, a SAID is embedded and the file is saved as personas/<SAID>.json")

    # --- Load + show Persona template SAID (auto) + persist SAIDed template ---
    try:
        # Persona template
        template_said, _tmpl_obj = _persona_template_said_and_obj()
        st.markdown("**Persona Template SAID (auto)**")
        st.code(template_said, language=None)

        # Persist persona template as first-class SAIDed object (best-effort)
        try:
            persisted_path = persist_persona_template_saided(_tmpl_obj)
            st.caption(f"Template persisted: {ui_safe_path(persisted_path, base=ROOT)}")
        except Exception as e:
            st.warning(f"Could not persist template: {e}")

        # --- VIEW CARD TEMPLATE toggle ---
        if "show_persona_template_json" not in st.session_state:
            st.session_state["show_persona_template_json"] = False

        col_btn, col_spacer = st.columns([2, 4])
        with col_btn:
            if st.button("VIEW CARD TEMPLATE", key="btn_view_persona_template"):
                st.session_state["show_persona_template_json"] = not st.session_state["show_persona_template_json"]

        if st.session_state["show_persona_template_json"]:
            st.json(_tmpl_obj)

        # ----------------------------
        # Constraint grammar (shared, template-locked)
        # ----------------------------
        grammar_said, g_obj = _constraint_grammar_said_and_obj()

        # Enforce: persona template must be bound to this shared grammar SAID
        tmpl_grammar_ref = str(_tmpl_obj.get("constraint_grammar", {}).get("grammar_ref") or "").strip()
        if not tmpl_grammar_ref:
            raise ValueError("Persona template missing constraint_grammar.grammar_ref")
        if tmpl_grammar_ref != grammar_said:
            raise ValueError(
                "Persona template grammar_ref mismatch "
                "(template not bound to shared constraint grammar SAID)"
            )

        # Match header styling with the template header
        st.markdown("**Constraint grammar (template-locked)**")
        st.code(grammar_said, language=None)

        # Persist constraint grammar as first-class SAIDed object (best-effort)
        # (Placed AFTER the field so the caption appears below it in the UI.)
        try:
            persisted_g_path = persist_constraint_grammar_saided(g_obj)
            st.caption(f"Constraint grammar persisted: {ui_safe_path(persisted_g_path, base=ROOT)}")
        except Exception as e:
            st.warning(f"Could not persist constraint grammar: {e}")

        # --- VIEW CONSTRAINT GRAMMAR toggle ---
        if "show_persona_constraint_grammar_json" not in st.session_state:
            st.session_state["show_persona_constraint_grammar_json"] = False

        # Give the button column more width to keep label on one line
        col_g_btn, col_g_spacer = st.columns([3, 3])
        with col_g_btn:
            if st.button("VIEW CONSTRAINT GRAMMAR", key="btn_view_persona_constraint_grammar"):
                st.session_state["show_persona_constraint_grammar_json"] = (
                    not st.session_state["show_persona_constraint_grammar_json"]
                )

        if st.session_state["show_persona_constraint_grammar_json"]:
            st.json(g_obj)

    except Exception as e:
        st.error(f"Persona Template error: {e}")
        st.stop()

    # ----------------------------
    # Inputs: Persona core fields
    # ----------------------------
    st.markdown("### Persona")
    persona_name = st.text_input("persona.name", value="imminent_threat_context", key="new_persona_name")
    persona_label = st.text_input("persona.label", value="Imminent threat context", key="new_persona_label")
    persona_description = st.text_area(
        "persona.description",
        value="Execution posture for imminent threat mitigation where serious harm to health or safety is reasonably believed to be impending and time-critical action is required.",
        key="new_persona_description",
        height=90,
    )

    # ----------------------------
    # Inputs: Duty frame
    # ----------------------------
    st.markdown("### Duty frame")
    duty_frame_ref = st.text_input(
        "duty_frame.ref",
        value="REPLACE_WITH_SUITABLE_IDENTIFIER",
        key="new_persona_duty_ref",
        help="A suitable identifier for the duty frame (could be DID, registry ref, etc.).",
    )
    duty_frame_label = st.text_input(
        "duty_frame.label",
        value="Imminent Threat Duty of Care",
        key="new_persona_duty_label",
    )
    duty_frame_resolution_scope = st.selectbox(
        "duty_frame.resolution_scope",
        options=["public", "private"],
        index=0,
        key="new_persona_duty_scope",
    )

    # ----------------------------
    # Constraints & tags (rendered from grammar bindings)
    # ----------------------------
    st.markdown("### Constraints (from constraint grammar)")
    bound = render_constraints_from_grammar_bindings(
        grammar_obj=g_obj,
        binding_key="persona_card",
        widget_key_prefix="persona_bound",
    )

    # ----------------------------
    # Composition
    # ----------------------------
    st.markdown("### Composition")
    compatible_personae = constraint_list("composition.compatible_personae", "comp_compatible", default_count=1)
    excluded_personae = constraint_list("composition.excluded_personae", "comp_excluded", default_count=1)
    composition_rule = st.selectbox(
        "composition.composition_rule",
        options=["intersection_only"],
        index=0,
        key="persona_composition_rule",
    )

    # ----------------------------
    # Create button (with flash messages)
    # ----------------------------
    if st.button("Create Persona", key="btn_create_persona"):
        try:
            said = create_persona_card(
                ward_path=ward_path,
                persona_name=persona_name,
                persona_label=persona_label,
                persona_description=persona_description,
                duty_frame_ref=duty_frame_ref,
                duty_frame_label=duty_frame_label,
                duty_frame_resolution_scope=duty_frame_resolution_scope,
                bound_values=bound,
                compatible_personae=compatible_personae or None,
                excluded_personae=excluded_personae or None,
                composition_rule=composition_rule,
            )
            flash_success(f"✅ Created Persona Card (SAID): {said}")
            st.rerun()
        except Exception as e:
            flash_error(f"❌ Create Persona failed: {e}")
            st.rerun()


with st.expander("Create Operation Card"):
    st.caption("Create an Operation Card instance (card_type='operation') and bind it to the Operation Template via card_template_ref.")
    st.caption("On Create, a SAID is embedded and the file is saved as operations/<SAID>.json")
    st.caption("This is binding-agnostic: no implied Role/Persona bindings are expressed here.")

    # --- Load + show Operation template SAID (auto) + persist SAIDed template ---
    try:
        # Operation template
        op_template_said, op_tmpl_obj = _operation_template_said_and_obj()
        st.markdown("**Operation Template SAID (auto)**")
        st.code(op_template_said, language=None)

        # Persist operation template as first-class SAIDed object (best-effort)
        try:
            persisted_path = persist_operation_template_saided(op_tmpl_obj)
            st.caption(f"Template persisted: {ui_safe_path(persisted_g_path, base=ROOT)}")
        except Exception as e:
            st.warning(f"Could not persist operation template: {e}")

        # --- VIEW CARD TEMPLATE toggle ---
        if "show_operation_template_json" not in st.session_state:
            st.session_state["show_operation_template_json"] = False

        col_btn, col_spacer = st.columns([2, 4])
        with col_btn:
            if st.button("VIEW CARD TEMPLATE", key="btn_view_operation_template"):
                st.session_state["show_operation_template_json"] = (
                    not st.session_state["show_operation_template_json"]
                )

        if st.session_state["show_operation_template_json"]:
            st.json(op_tmpl_obj)

        # ----------------------------
        # Operation constraint grammar (template-locked)
        # ----------------------------
        op_grammar_said, op_g_obj = _operation_constraint_grammar_said_and_obj()

        # Enforce: operation template must be bound to this operation grammar SAID
        tmpl_grammar_ref = str(op_tmpl_obj.get("constraint_grammar", {}).get("grammar_ref") or "").strip()
        if not tmpl_grammar_ref:
            raise ValueError("Operation template missing constraint_grammar.grammar_ref")
        if tmpl_grammar_ref != op_grammar_said:
            raise ValueError(
                "Operation template grammar_ref mismatch "
                "(template not bound to operation constraint grammar SAID)"
            )

        # Match header styling with the template header (mirror Persona UI)
        st.markdown("**Constraint grammar (template-locked)**")
        st.code(op_grammar_said, language=None)

        # Persist operation constraint grammar as first-class SAIDed object (best-effort)
        # (Placed AFTER the field so the caption appears below it in the UI.)
        try:
            persisted_g_path = persist_operation_constraint_grammar_saided(op_g_obj)
            st.caption(f"Constraint grammar persisted: {ui_safe_path(persisted_g_path, base=ROOT)}")
        except Exception as e:
            st.warning(f"Could not persist operation constraint grammar: {e}")

        # --- VIEW CONSTRAINT GRAMMAR toggle ---
        if "show_operation_constraint_grammar_json" not in st.session_state:
            st.session_state["show_operation_constraint_grammar_json"] = False

        col_g_btn, col_g_spacer = st.columns([3, 3])
        with col_g_btn:
            if st.button("VIEW CONSTRAINT GRAMMAR", key="btn_view_operation_constraint_grammar"):
                st.session_state["show_operation_constraint_grammar_json"] = (
                    not st.session_state["show_operation_constraint_grammar_json"]
                )

        if st.session_state["show_operation_constraint_grammar_json"]:
            st.json(op_g_obj)

    except Exception as e:
        st.error(f"Operation Template error: {e}")
        st.stop()

    # ----------------------------
    # Operation core fields
    # ----------------------------
    st.markdown("### Operation")
    operation_name = st.text_input("operation.name", value="capable_recipients_identification_read_only", key="new_op_name_v2")
    operation_label = st.text_input("operation.label", value="Capable recipients identification (read-only)", key="new_op_label_v2")
    operation_description = st.text_area(
        "operation.description",
        value="Identifies and lists recipient categories capable of mitigating an imminent threat or supporting emergency response, without selecting a specific recipient or initiating disclosure.",
        key="new_op_desc_v2",
        height=90,
    )

    # ----------------------------
    # Defined-by
    # ----------------------------
    st.markdown("### Defined by")
    defined_by_service_ref = st.text_input("defined_by.service_ref", value="UNSPECIFIED_SERVICE_REF", key="new_op_defined_by_ref")
    defined_by_label = st.text_input("defined_by.label", value="Emergency disclosure assessment procedure", key="new_op_defined_by_label")
    defined_by_resolution_scope = st.selectbox(
        "defined_by.resolution_scope",
        options=["public", "private"],
        index=0,
        key="new_op_defined_by_scope",
    )

    # ----------------------------
    # Grammar-bound constraints (from operation constraint grammar bindings)
    # ----------------------------
    st.markdown("### Constraints (from operation constraint grammar)")
    bound = render_constraints_from_grammar_bindings(
        grammar_obj=op_g_obj,
        binding_key="operation_card",
        widget_key_prefix="op_bound",
    )

    # ----------------------------
    # Tags / modality / preconditions / boundaries (template-aligned, but no implied bindings)
    # ----------------------------
    st.markdown("### Operation tags")
    op_operation_type = st.text_input("operation_tags.operation_type", value="ocv:ReadOnly", key="op_tag_type")
    op_domain = constraint_list("operation_tags.domain", "op_tag_domain", default_count=2)
    op_sensitivity = constraint_list("operation_tags.sensitivity", "op_tag_sensitivity", default_count=1)
    op_audience = constraint_list("operation_tags.audience", "op_tag_audience", default_count=2)
    op_capabilities = constraint_list("operation_tags.capabilities", "op_tag_capabilities", default_count=2)

    operation_tags = {
        "operation_type": (op_operation_type or "").strip(),
        "domain": list(op_domain or []),
        "sensitivity": list(op_sensitivity or []),
        "audience": list(op_audience or []),
        "capabilities": list(op_capabilities or []),
    }

    st.markdown("### Execution modality")
    mutability = st.text_input("execution_modality.mutability", value="ocv:NoStateChange", key="op_mutability")

    col_m1, col_m2, col_m3 = st.columns(3)
    with col_m1:
        draft_only = st.checkbox("draft_only", value=False, key="op_draft_only")
        read_only = st.checkbox("read_only", value=True, key="op_read_only")
        transform_only = st.checkbox("transform_only", value=False, key="op_transform_only")
    with col_m2:
        synchronous_only = st.checkbox("synchronous_only", value=True, key="op_sync_only")
        single_execution = st.checkbox("single_execution", value=True, key="op_single_exec")
        auto_term = st.checkbox("auto_termination_on_completion", value=True, key="op_auto_term")
    with col_m3:
        pass

    execution_modality = {
        "mutability": (mutability or "").strip(),
        "draft_only": bool(draft_only),
        "read_only": bool(read_only),
        "transform_only": bool(transform_only),
        "synchronous_only": bool(synchronous_only),
        "single_execution": bool(single_execution),
        "auto_termination_on_completion": bool(auto_term),
    }

    st.markdown("### Required preconditions")
    admission_state = constraint_list("required_preconditions.admission_state", "op_pre_admission", default_count=1)
    required_persona_context = constraint_list("required_preconditions.required_persona_context", "op_pre_req_persona", default_count=1)
    compatible_persona_contexts = constraint_list("required_preconditions.compatible_persona_contexts", "op_pre_comp_persona", default_count=0)
    required_roles = constraint_list("required_preconditions.required_roles", "op_pre_roles", default_count=1)
    human_in_loop = constraint_list("required_preconditions.human_in_loop", "op_pre_hil", default_count=0)
    non_authorizing_notice = st.text_area(
        "required_preconditions.non_authorizing_notice",
        value="This operation is non-authorizing. Any disclosure/transfer requires an upstream authorization pathway.",
        key="op_pre_notice",
        height=70,
    )

    required_preconditions = {
        "admission_state": list(admission_state or []),
        "required_persona_context": list(required_persona_context or []),
        "compatible_persona_contexts": list(compatible_persona_contexts or []),
        "required_roles": list(required_roles or []),
        "human_in_loop": list(human_in_loop or []),
        "non_authorizing_notice": (non_authorizing_notice or "").strip(),
    }

    st.markdown("### Input boundaries")
    allowed_data_categories = constraint_list("input_boundaries.allowed_data_categories", "op_in_allow", default_count=2)
    required_inputs = constraint_list("input_boundaries.required_inputs", "op_in_required", default_count=1)
    optional_inputs = constraint_list("input_boundaries.optional_inputs", "op_in_optional", default_count=2)
    explicitly_prohibited_inputs = constraint_list("input_boundaries.explicitly_prohibited_inputs", "op_in_block", default_count=2)

    col_rl1, col_rl2, col_rl3 = st.columns(3)
    with col_rl1:
        max_contacts_considered = st.number_input("record_limits.max_contacts_considered (0=null)", min_value=0, value=0, step=1, key="op_rl_contacts")
    with col_rl2:
        max_patient_records = st.number_input("record_limits.max_patient_records (0=null)", min_value=0, value=0, step=1, key="op_rl_patients")
    with col_rl3:
        max_records_touched = st.number_input("record_limits.max_records_touched (0=null)", min_value=0, value=5, step=1, key="op_rl_touched")

    input_boundaries = {
        "allowed_data_categories": list(allowed_data_categories or []),
        "required_inputs": list(required_inputs or []),
        "optional_inputs": list(optional_inputs or []),
        "explicitly_prohibited_inputs": list(explicitly_prohibited_inputs or []),
        "record_limits": {
            "max_contacts_considered": None if int(max_contacts_considered) == 0 else int(max_contacts_considered),
            "max_patient_records": None if int(max_patient_records) == 0 else int(max_patient_records),
            "max_records_touched": None if int(max_records_touched) == 0 else int(max_records_touched),
        },
    }

    st.markdown("### Processing constraints")
    allowed_actions = constraint_list("processing_constraints.allowed_actions", "op_proc_allow", default_count=2)
    prohibited_actions = constraint_list("processing_constraints.prohibited_actions", "op_proc_block", default_count=3)
    transformation_limits = constraint_list("processing_constraints.transformation_limits", "op_proc_limits", default_count=2)

    processing_constraints = {
        "allowed_actions": list(allowed_actions or []),
        "prohibited_actions": list(prohibited_actions or []),
        "transformation_limits": list(transformation_limits or []),
    }

    st.markdown("### Output constraints")
    output_type = st.text_input("output_constraints.output_type", value="recipient_category_list", key="op_out_type")
    out_format = st.text_input("output_constraints.format", value="structured_list", key="op_out_format")
    required_sections = constraint_list("output_constraints.required_sections", "op_out_sections", default_count=3)
    detail_level = st.text_input("output_constraints.detail_level", value="high_level", key="op_out_detail")
    redaction_rules = constraint_list("output_constraints.redaction_rules", "op_out_redact", default_count=2)
    use_restrictions = constraint_list("output_constraints.use_restrictions", "op_out_use", default_count=3)

    output_constraints = {
        "output_type": (output_type or "").strip(),
        "format": (out_format or "").strip(),
        "required_sections": list(required_sections or []),
        "detail_level": (detail_level or "").strip(),
        "redaction_rules": list(redaction_rules or []),
        "use_restrictions": list(use_restrictions or []),
    }

    st.markdown("### Post-execution handling")
    retention = st.text_input("post_execution_handling.retention", value="ocv:SessionScoped", key="op_post_retention")
    draft_lifecycle = constraint_list("post_execution_handling.draft_lifecycle", "op_post_draft", default_count=0)
    audit_requirements = constraint_list("post_execution_handling.audit_requirements", "op_post_audit", default_count=1)
    completion_ack = constraint_list("post_execution_handling.completion_acknowledgement", "op_post_ack", default_count=0)

    post_execution_handling = {
        "retention": (retention or "").strip(),
        "draft_lifecycle": list(draft_lifecycle or []),
        "audit_requirements": list(audit_requirements or []),
        "completion_acknowledgement": list(completion_ack or []),
    }

    st.markdown("### Explicit non-goals")
    explicit_non_goals = constraint_list("explicit_non_goals", "op_non_goals", default_count=3)

    st.markdown("### Alignment notes")
    role_alignment = st.text_input("alignment_notes.role_alignment", value="", key="op_align_role")
    persona_alignment = st.text_input("alignment_notes.persona_alignment", value="", key="op_align_persona")
    privacy_posture = st.text_input("alignment_notes.privacy_posture", value="", key="op_align_privacy")

    alignment_notes = {
        "role_alignment": (role_alignment or "").strip(),
        "persona_alignment": (persona_alignment or "").strip(),
        "privacy_posture": (privacy_posture or "").strip(),
    }

    # ----------------------------
    # Create button
    # ----------------------------
    if st.button("Create Operation", key="btn_create_operation_v2"):
        try:
            op_said = create_operation_card(
                ward_path=ward_path,
                operation_name=operation_name,
                operation_label=operation_label,
                operation_description=operation_description,
                defined_by_service_ref=defined_by_service_ref,
                defined_by_label=defined_by_label,
                defined_by_resolution_scope=defined_by_resolution_scope,
                bound_values=bound,
                operation_tags=operation_tags,
                execution_modality=execution_modality,
                required_preconditions=required_preconditions,
                input_boundaries=input_boundaries,
                processing_constraints=processing_constraints,
                output_constraints=output_constraints,
                post_execution_handling=post_execution_handling,
                explicit_non_goals=explicit_non_goals or [],
                alignment_notes=alignment_notes,
            )
            flash_success(f"✅ Created Operation Card (SAID): {op_said}")
            st.rerun()
        except Exception as e:
            flash_error(f"❌ Create Operation failed: {e}")
            st.rerun()


# ----------------------------
# Delete protocol artifacts (Authority Policy / Role Card / Persona Card / Operation Card)
# ----------------------------

st.subheader("Delete protocol artifacts")
st.caption("Hard-delete Authority Policies, Role Cards, Persona Cards, and Operation Cards. (Use with care.)")

authority_policies_for_delete = list_json_files(ward_path / "authority_policies")
roles_for_delete = list_json_files(ward_path / "roles")
personas_for_delete = list_json_files(ward_path / "personas")
ops_for_delete = list_json_files(ward_path / "operations")

with st.expander("Delete Authority Policy (hard delete)", expanded=False):
    if authority_policies_for_delete:
        delete_target = st.selectbox(
            "Select Authority Policy file to delete",
            [p.name for p in authority_policies_for_delete],
            key="authority_policy_delete_select_top",
        )
        st.caption("Preview (double-check before deleting):")
        st.json(read_json(ward_path / "authority_policies" / delete_target))

        with st.form("authority_policy_delete_form_top", clear_on_submit=True):
            confirm = st.text_input("Type DELETE to confirm", key="authority_policy_delete_confirm_top")
            submitted = st.form_submit_button("Delete Authority Policy")

        if submitted:
            if confirm.strip() != "DELETE":
                st.error("Confirmation failed. Type DELETE exactly.")
            else:
                delete_authority_policy(ward_path, delete_target)
                flash_success(f"🗑️ Deleted Authority Policy: {delete_target}")
                st.rerun()
    else:
        st.info("No authority policies available to delete.")

with st.expander("Delete Role Card (hard delete)", expanded=False):
    if roles_for_delete:
        delete_target = st.selectbox(
            "Select Role file to delete",
            [p.name for p in roles_for_delete],
            key="role_delete_select_top",
        )
        st.caption("Preview (double-check before deleting):")
        st.json(read_json(ward_path / "roles" / delete_target))

        with st.form("role_delete_form_top", clear_on_submit=True):
            confirm = st.text_input("Type DELETE to confirm", key="role_delete_confirm_top")
            submitted = st.form_submit_button("Delete Role")

        if submitted:
            if confirm.strip() != "DELETE":
                st.error("Confirmation failed. Type DELETE exactly.")
            else:
                delete_role(ward_path, delete_target)
                flash_success(f"🗑️ Deleted Role Card: {delete_target}")
                st.rerun()
    else:
        st.info("No roles available to delete.")

with st.expander("Delete Persona Card (hard delete)", expanded=False):
    if personas_for_delete:
        delete_target = st.selectbox(
            "Select Persona file to delete",
            [p.name for p in personas_for_delete],
            key="persona_delete_select_top",
        )
        st.caption("Preview (double-check before deleting):")
        st.json(read_json(ward_path / "personas" / delete_target))

        with st.form("persona_delete_form_top", clear_on_submit=True):
            confirm = st.text_input("Type DELETE to confirm", key="persona_delete_confirm_top")
            submitted = st.form_submit_button("Delete Persona")

        if submitted:
            if confirm.strip() != "DELETE":
                st.error("Confirmation failed. Type DELETE exactly.")
            else:
                delete_persona(ward_path, delete_target)
                flash_success(f"🗑️ Deleted Persona Card: {delete_target}")
                st.rerun()
    else:
        st.info("No personas available to delete.")

with st.expander("Delete Operation Card (hard delete)", expanded=False):
    if ops_for_delete:
        delete_target = st.selectbox(
            "Select Operation file to delete",
            [p.name for p in ops_for_delete],
            key="op_delete_select_top",
        )
        st.caption("Preview (double-check before deleting):")
        st.json(read_json(ward_path / "operations" / delete_target))

        with st.form("op_delete_form_top", clear_on_submit=True):
            confirm = st.text_input("Type DELETE to confirm", key="op_delete_confirm_top")
            submitted = st.form_submit_button("Delete Operation")

        if submitted:
            if confirm.strip() != "DELETE":
                st.error("Confirmation failed. Type DELETE exactly.")
            else:
                delete_operation(ward_path, delete_target)
                st.success(f"Deleted operation: {delete_target}")
                st.rerun()
    else:
        st.info("No operations available to delete.")

st.divider()

# ----------------------------
# Manifest / Contents
# ----------------------------
colA, colB = st.columns([1, 1])
with colA:
    st.subheader("Ward Manifest")
    st.json(manifest)

with colB:
    st.subheader("Ward Contents")

    st.write("**Roles**", len(list_json_files(ward_path / "roles")))
    st.caption("Classes of effects that may be permitted at execution time.")

    st.write("**Personas**", len(list_json_files(ward_path / "personas")))
    st.caption("Behavioral constraints under which actions may be executed.")

    st.write("**Operations**", len(list_json_files(ward_path / "operations")))
    st.caption("Procedural structures that may be proposed for execution.")

    st.write("**Action Intents**", len(list_json_files(ward_path / "action_intents")))
    st.caption("Specified proposals for single actions to be evaluated at execution.")

    st.write("**Correspondences**", len(list_json_files(ward_path / "correspondences")))
    st.caption("Explicit structural alignments between independent artifacts.")

    st.write("**Warrants**", len(list_json_files(ward_path / "warrants")))
    st.caption("Action-time authorization conditions for specific executions.")

    st.write("**Receipts**", len(list_json_files(ward_path / "receipts")))
    st.caption("Recorded outcomes of execution attempts after authority is exhausted.")

st.divider()

# ============================================================
# Viewer columns: col1 Roles, col2 Personas, col3 Operations
# ============================================================

roles = list_json_files(ward_path / "roles")
personas = list_json_files(ward_path / "personas")
operations = list_operation_cards_v1(ward_path)

col1, col2, col3 = st.columns([1, 1, 1])

role_obj: Optional[Dict[str, Any]] = None
persona_obj: Optional[Dict[str, Any]] = None

with col1:
    st.subheader("Roles")

    if roles:
        # Build labels: "<name> (<status>)"
        role_label_by_file: dict[str, str] = {}

        for p in roles:
            try:
                raw = read_json(ward_path / "roles" / p.name)
                role = raw.get("role") or {}
                name = (role.get("name") or "Unnamed role").strip()
                status = (raw.get("status") or "unknown").strip()
                role_label_by_file[p.name] = f"{name} ({status})"
            except Exception:
                role_label_by_file[p.name] = p.name  # safe fallback

        role_file = st.selectbox(
            "Role Card",
            options=[p.name for p in roles],
            format_func=lambda fn: role_label_by_file.get(fn, fn),
            key="role_file_select",
        )

        role_obj = read_json(ward_path / "roles" / role_file)
        st.json(role_obj)

    else:
        st.info("No roles yet.")

with col2:
    st.subheader("Personas")

    if personas:
        # Build labels: "<name> (<status>)"
        persona_label_by_file: dict[str, str] = {}

        for p in personas:
            try:
                raw = read_json(ward_path / "personas" / p.name)
                persona = raw.get("persona") or {}
                name = (persona.get("name") or "Unnamed persona").strip()
                status = (raw.get("status") or "unknown").strip()
                persona_label_by_file[p.name] = f"{name} ({status})"
            except Exception:
                persona_label_by_file[p.name] = p.name  # safe fallback

        persona_file = st.selectbox(
            "Persona Card",
            options=[p.name for p in personas],
            format_func=lambda fn: persona_label_by_file.get(fn, fn),
            key="persona_file_select",
        )

        persona_obj = read_json(ward_path / "personas" / persona_file)
        st.json(persona_obj)

    else:
        st.info("No personas yet.")

with col3:
    st.subheader("Operations")

    if not operations:
        st.info("No operation cards yet. (Add JSONs under operations/.)")
    else:
        options: List[Tuple[str, OperationCardV1, ValidationResult, Path]] = []
        blocked_debug: List[Tuple[str, List[str], Path]] = []

        filter_for_context = bool(role_obj and persona_obj)

        for card, v, p in operations:
            name = (card.operation_name or p.stem).strip()

            if not v.ok:
                continue

            if (card.status or "").strip() != "active":
                continue

            if filter_for_context:
                ok, reasons = operation_compat_with_role_persona(card, role_obj, persona_obj)
                if not ok:
                    blocked_debug.append((name, reasons, p))
                    continue

            label = f"✅ {name} (active)"
            options.append((label, card, v, p))

        options.sort(key=lambda t: ((t[1].operation_name or t[3].stem).strip()))

        if filter_for_context:
            st.caption("Showing only operations compatible with the selected Role + Persona.")
        else:
            st.caption("Select a Role and Persona to filter operations to lawful ones.")

        def _sync_selected_operation(sel: Tuple[str, OperationCardV1, ValidationResult, Path]) -> None:
            _label, sel_card, _sel_v, _sel_path = sel

            op_name = (sel_card.operation_name or "").strip()
            op_digest = (sel_card.d or "").strip()
            
            st.session_state["selected_operation_name"] = op_name
            st.session_state["selected_operation_digest"] = op_digest

            st.session_state["ai_operation_name"] = op_name
            st.session_state["ai_operation_digest"] = op_digest

            if op_name:
                st.session_state["mint_operation_name"] = op_name
                st.session_state["mint_operation_preferred_name"] = op_name
                st.session_state.pop("mint_operation_choice", None)

        if not options:
            st.info("No lawful Operation Cards for the selected Role + Persona.")
            st.session_state.pop("op_select", None)

            for k in [
                "selected_operation_name",
                "selected_operation_digest",
                "ai_operation_name",
                "ai_operation_digest",
                "mint_operation_name",
                "mint_operation_preferred_name",
            ]:
                st.session_state.pop(k, None)
        else:
            current_sel = st.session_state.get("op_select")
            if current_sel not in options:
                st.session_state["op_select"] = options[0]
                _sync_selected_operation(options[0])

            def _on_operation_select_change() -> None:
                sel = st.session_state.get("op_select")
                if sel in options:
                    _sync_selected_operation(sel)

            sel = st.selectbox(
                "Operation Card",
                options=options,
                format_func=lambda t: t[0],
                index=options.index(st.session_state["op_select"])
                if st.session_state.get("op_select") in options
                else 0,
                key="op_select",
                on_change=_on_operation_select_change,
            )

            if sel is not None:
                _sync_selected_operation(sel)

            sel_label, sel_card, sel_v, sel_path = sel

            if not sel_v.ok:
                st.error("Operation Card v1 is invalid:")
                for e in sel_v.errors:
                    st.write(f"- {e}")

            st.caption(
                f"Operation Card reference: "
                f"wards/{ward_path.name}/operations/{sel_path.name}"
            )
            st.json(sel_card.raw)

st.divider()

# ============================================================
# Lawful Operation Selection (Story-first) — SAID-native
# ============================================================

st.subheader("Lawful Operation Selection")
st.caption("Given the selected Role + Persona, what operations are permitted?")

with st.expander("Lawful operation surface (Role + Persona → allowed operations)", expanded=True):
    # Guard: we only need Role + Persona + ward_path (not ward_ref)
    if not (role_obj and persona_obj and ward_path):
        st.info("Select one Role and one Persona (left columns) to compute the lawful operation surface.")
    else:
        role_d = _as_dict(role_obj)
        persona_d = _as_dict(persona_obj)

        # ---- SAID-native identity + semantic compatibility keys ----
        role_said = (role_d.get("d") or "").strip()
        role_name = (
            ((role_d.get("role") or {}).get("label"))
            or ((role_d.get("role") or {}).get("name"))
            or ""
        ).strip()
        role_key = ((role_d.get("role") or {}).get("name") or "").strip()  # compatibility key

        persona_said = (persona_d.get("d") or "").strip()
        persona_name = (
            ((persona_d.get("persona") or {}).get("label"))
            or ((persona_d.get("persona") or {}).get("name"))
            or ""
        ).strip()
        persona_key = ((persona_d.get("persona") or {}).get("name") or "").strip()  # compatibility key

        # ---- Ward identity (handle vs ref) ----
        ward_handle_display = (ward_handle or "").strip()
        ward_ref_display = (ward_ref or "").strip()  # metadata only

        st.markdown("### Selected governance context")
        ctx_cols = st.columns(3)

        with ctx_cols[0]:
            st.markdown("**Ward**")
            # Filesystem anchor (authoritative)
            st.caption("handle (folder)")
            st.code(ward_handle_display or "(missing ward_handle)", language=None)

        with ctx_cols[1]:
            st.markdown("**Role**")
            st.write(role_name or "(unnamed)")
            st.caption(role_said or "(missing SAID in field 'd')")
            if role_key:
                st.caption(f"compat key: {role_key}")

        with ctx_cols[2]:
            st.markdown("**Persona**")
            st.write(persona_name or "(unnamed)")
            st.caption(persona_said or "(missing SAID in field 'd')")
            if persona_key:
                st.caption(f"compat key: {persona_key}")

        st.markdown("---")

        # Filesystem discovery
        ops_dir = ward_path / "operations"
        if not ops_dir.exists():
            st.error(f"Operations directory not found: {ops_dir}")
            st.stop()

        # Load operation cards from the authoritative ward folder
        cards = active_valid_operation_cards(ward_path)
        if not cards:
            # Extra diagnostics: do files exist but fail validation / status?
            json_files = sorted(list(ops_dir.glob("*.json")))
            st.warning("No active + valid Operation Cards found under this ward (wards/<ward>/operations/*.json).")
            st.caption(f"Found {len(json_files)} operation JSON file(s) on disk under: {ops_dir}")

            if json_files:
                with st.expander("Show operation files on disk (debug)", expanded=False):
                    for f in json_files[:50]:
                        st.write(f"- {f.name}")
        else:
            compatible: List[Tuple[OperationCardV1, List[str]]] = []
            incompatible: List[Tuple[OperationCardV1, List[str]]] = []

            for c in cards:
                ok, reasons = operation_compat_with_role_persona(c, role_d, persona_d)
                if ok:
                    compatible.append((c, reasons))
                else:
                    incompatible.append((c, reasons))

            def _op_sort_key(t: Tuple[OperationCardV1, List[str]]) -> Tuple[str, str]:
                c, _ = t
                label = ((getattr(c, "raw", {}) or {}).get("operation") or {}).get("label") or ""
                return (str(label).strip(), str(getattr(c, "operation_name", "") or "").strip())

            compatible.sort(key=_op_sort_key)
            incompatible.sort(key=_op_sort_key)

            st.markdown("### ✅ Lawful operations permitted")
            st.caption("These operations are compatible with the selected Role + Persona constraints. This is NOT execution—only selection.")

            if not compatible:
                st.error("No lawful operations for this Role–Persona pairing.")
                st.caption(
                    "Tip: adjust the Role constraints (scope/prohibitions/obligations) or Persona constraints "
                    "(purpose/privacy/tooling/time/integrity) to change this surface."
                )
            else:
                for c, _reasons in compatible:
                    op_name = (getattr(c, "operation_name", "") or "").strip()
                    raw = getattr(c, "raw", {}) or {}
                    label = ((raw.get("operation") or {}).get("label")) or raw.get("label") or ""
                    label = str(label).strip()

                    shown = f"**{label}**" if label else f"**{op_name}**"

                    st.markdown(f"✅ {shown}")
                    if op_name:
                        st.code(op_name, language=None)

                    st.caption("Why allowed: meets Role + Persona compatibility constraints for this operation.")
                    st.write("")

            st.markdown("---")

            st.markdown("### 🚫 Not lawful (and why)")
            st.caption("These operations exist, but are not permitted under the selected Role + Persona.")

            if not incompatible:
                st.success("None — all active operations are lawful under this Role–Persona pairing.")
            else:
                with st.expander(f"Show {len(incompatible)} disallowed operation(s)", expanded=False):
                    for c, reasons in incompatible:
                        op_name = (getattr(c, "operation_name", "") or "").strip()
                        raw = getattr(c, "raw", {}) or {}
                        label = ((raw.get("operation") or {}).get("label")) or raw.get("label") or ""
                        label = str(label).strip()

                        shown = f"**{label}**" if label else f"**{op_name}**"

                        st.markdown(f"❌ {shown}")
                        if op_name:
                            st.code(op_name, language=None)

                        if reasons:
                            st.markdown("**Blocked because:**")
                            for r in reasons:
                                st.write(f"- {r}")
                        else:
                            st.caption("Blocked because: incompatible constraints.")
                        st.write("")

            st.markdown("---")

            st.markdown("### What happens next")
            st.write(
                "Selecting a lawful operation does **not** execute it. "
                "Next, the Ward will construct a **single-attempt Action Intent**, then create a **Correspondence Form** "
                "locking **Role + Persona + Intent**, and finally mint a **single-use Warrant** for Warden evaluation."
            )

st.divider()

# ============================================================
# Execution artifacts (Action Intent + Correspondence)
# ============================================================

st.subheader("Execution artifacts")
st.caption("Action Intents and Correspondences live here (execution-time artifacts), separate from protocol governance artifacts.")

# ----------------------------
# Create Action-intent (+ Correspondence)
# ----------------------------
with st.expander("Create Action Intent"):
    st.caption("Writes Action Intent v1 into wards/<WARD_HANDLE>/action_intents/<action_intent_id>.json")
    st.caption("On success, also creates a Correspondence Form to lock Role + Persona + Action Intent for minting.")

    # One-shot banner for Create outcomes (shows once, then clears)
    _msgs = st.session_state.pop("ai_create_msgs", None) or []
    for level, text in _msgs:
        {
            "success": st.success,
            "error": st.error,
            "warning": st.warning,
            "info": st.info,
        }.get(level, st.info)(text)

    cards = active_valid_operation_cards(ward_path)
    if not cards:
        st.warning("No active + valid Operation Cards found. Add/ingest operations first.")
    else:
        op_by_name: Dict[str, OperationCardV1] = {
            (c.operation_name or "").strip(): c
            for c in cards
            if (c.operation_name or "").strip()
        }

        selected_op_name = (st.session_state.get("selected_operation_name") or "").strip()
        selected_op_digest = (st.session_state.get("selected_operation_digest") or "").strip()

        if (not selected_op_name) or (selected_op_name not in op_by_name):
            st.info("Select an Operation Card in the Operations column to prefill this Action Intent.")
        elif not selected_op_digest:
            st.warning("Selected Operation is missing a computed digest. Re-select the Operation Card.")
        else:
            # Role + Persona are required for correspondence locking
            role_id_locked = (getattr(role_obj, "d", "") or _as_dict(role_obj).get("d") or "").strip()
            persona_id_locked = (getattr(persona_obj, "d", "") or _as_dict(persona_obj).get("d") or "").strip()

            st.caption(f"Locked role SAID: {role_id_locked or '(missing)'}")
            st.caption(f"Locked persona SAID: {persona_id_locked or '(missing)'}")

            if not role_id_locked or not persona_id_locked:
                st.warning("Select a Role and Persona (left columns) to lock this Action Intent into a Correspondence Form.")
                can_create = False
            else:
                can_create = True

            # Store the normalized digest (important)
            st.session_state["ai_operation_name"] = selected_op_name
            st.session_state["ai_operation_digest"] = selected_op_digest
            st.session_state["ai_operation_name_locked"] = selected_op_name
            st.session_state["ai_operation_digest_locked"] = selected_op_digest

            st.text_input(
                "Operation (from registry)",
                value=selected_op_name,
                disabled=True,
                key="ai_operation_name_locked",
                help="Sourced from the Operations column. Change the selection there.",
            )

            st.text_input(
                "operation_digest (auto)",
                value=selected_op_digest,
                disabled=True,
                key="ai_operation_digest_locked",
                help="Sourced from the Operations column selection and bound into the Action Intent.",
            )

            # --- Identity
            if "ai_action_intent_id" not in st.session_state:
                st.session_state["ai_action_intent_id"] = str(uuid.uuid4())

            def _gen_ai_action_intent_id() -> None:
                st.session_state["ai_action_intent_id"] = str(uuid.uuid4())
                st.session_state["ai_action_intent_id_msg"] = "generated"

            col_id1, col_id2 = st.columns([1, 1])

            with col_id1:
                action_intent_id = st.text_input(
                    "action_intent_id",
                    key="ai_action_intent_id",
                    help="UUID for this intent. Use Generate to refresh.",
                )

            with col_id2:
                st.button(
                    "Generate new action_intent_id",
                    on_click=_gen_ai_action_intent_id,
                    key="ai_gen_action_intent_id",
                )

            if st.session_state.pop("ai_action_intent_id_msg", None) == "generated":
                st.success("New action_intent_id generated.")

            created_by = st.text_input(
                "created_by",
                value=f"ward:{ward_ref}" if ward_ref else "ward:UNKNOWN",
                key="ai_created_by",
                help="Actor reference that created the intent (Ward-driven in Flow 1).",
            )

            # --- Targets
            st.markdown("### Targets")
            st.caption("At least one target is required. Add more if needed.")
            tgt_count = st.number_input(
                "Number of targets",
                min_value=1,
                max_value=10,
                value=1,
                step=1,
                key="ai_target_count",
            )

            targets: List[Dict[str, Any]] = []
            for i in range(int(tgt_count)):
                st.markdown(f"**Target #{i+1}**")
                c1, c2, c3 = st.columns([1, 2, 2])
                with c1:
                    target_kind = st.selectbox(
                        "target_kind",
                        options=["patient", "encounter", "document", "family_member", "freeform_ref"],
                        key=f"ai_target_kind_{i}",
                    )
                with c2:
                    target_ref = st.text_input(
                        "target_ref",
                        value="",
                        key=f"ai_target_ref_{i}",
                        help="Opaque reference or identifier (e.g., patient_id:123, mrn:..., etc.).",
                    )
                with c3:
                    selector = st.text_input(
                        "selector",
                        value="",
                        key=f"ai_target_selector_{i}",
                        help="Optional selector (string). Leave blank if not needed.",
                    )

                targets.append(
                    {
                        "target_kind": (target_kind or "").strip(),
                        "target_ref": (target_ref or "").strip(),
                        "selector": (selector or ""),
                    }
                )

            # --- Context binding
            st.markdown("### Context binding")
            zone_ref = st.text_input("zone_ref", value="zone:ed", key="ai_zone_ref")
            overlay_refs = constraint_list("overlay_refs", "ai_overlay_refs", default_count=1)
            jurisdiction_ref = st.text_input("jurisdiction_ref", value="jurisdiction:unspecified", key="ai_jurisdiction_ref")
            effective_time = st.text_input(
                "effective_time (ISO8601)",
                value=utc_now_iso(),
                key="ai_effective_time",
                help="Usually now (UTC).",
            )

            # --- Parameters (raw JSON)
            st.markdown("### Parameters")
            st.caption("JSON object. Keep {} for MVP.")
            params_text = st.text_area(
                "parameters (JSON)",
                value=json.dumps({}, indent=2),
                key="ai_parameters_json",
                height=140,
            )

            # --- Scope
            st.markdown("### Scope")
            st.caption("Keep narrow. These are the per-attempt bounds the Ward is asking for.")
            col_s1, col_s2 = st.columns(2)
            with col_s1:
                max_records = st.number_input(
                    "max_records (0 = null)",
                    min_value=0,
                    value=1,
                    step=1,
                    key="ai_scope_max_records",
                )
            with col_s2:
                time_window_seconds = st.number_input(
                    "time_window_seconds (0 = null)",
                    min_value=0,
                    value=0,
                    step=60,
                    key="ai_scope_time_window_seconds",
                )

            field_allowlist = constraint_list("field_allowlist", "ai_scope_field_allowlist", default_count=1)
            field_blocklist = constraint_list("field_blocklist", "ai_scope_field_blocklist", default_count=1)

            if st.button(
                "Create Action Intent + Correspondence",
                type="primary",
                key="btn_create_action_intent_v1",
                disabled=not can_create,
                help=None if can_create else "Select a Role + Persona to create the Correspondence Form.",
            ):
                msgs: List[Tuple[str, str]] = []

                # Parse parameters JSON
                try:
                    parameters = json.loads(params_text or "{}")
                    if not isinstance(parameters, dict):
                        raise ValueError("parameters must be a JSON object")
                except Exception as e:
                    st.session_state["ai_create_msgs"] = [("error", f"Invalid parameters JSON: {e}")]
                    st.rerun()

                max_records_val = None if int(max_records) == 0 else int(max_records)
                time_window_val = None if int(time_window_seconds) == 0 else int(time_window_seconds)

                context_binding = {
                    "zone_ref": (zone_ref or "").strip(),
                    "overlay_refs": list(overlay_refs or []),
                    "jurisdiction_ref": (jurisdiction_ref or "").strip(),
                    "effective_time": (effective_time or "").strip(),
                }

                scope = {
                    "max_records": max_records_val,
                    "time_window_seconds": time_window_val,
                    "field_allowlist": list(field_allowlist or []),
                    "field_blocklist": list(field_blocklist or []),
                }

                built = build_action_intent_v1(
                    action_intent_id=(action_intent_id or "").strip(),
                    operation_digest=selected_op_digest,
                    targets=targets,
                    context_binding=context_binding,
                    parameters=parameters,
                    scope=scope,
                    created_at=utc_now_iso(),
                    created_by=(created_by or "").strip(),
                    status="proposed",
                )

                try:
                    intent_obj = ActionIntentV1(built)
                    v = validate_action_intent_v1(intent_obj)
                except Exception as e:
                    st.session_state["ai_create_msgs"] = [
                        ("error", f"Failed to construct/validate Action Intent: {type(e).__name__}: {e}")
                    ]
                    st.rerun()

                if not v.ok:
                    # Show detailed errors now and keep a banner next run
                    st.error("Action Intent failed validation:")
                    for e in v.errors:
                        st.write(f"- {e}")
                    st.session_state["ai_create_msgs"] = [
                        ("error", "Action Intent rejected: failed validation (see errors above).")
                    ]
                    st.stop()

                ensure_dirs(ward_path)
                ai_path = ward_path / "action_intents" / f"{intent_obj.action_intent_id}.json"

                if ai_path.exists():
                    st.session_state["ai_create_msgs"] = [
                        ("error", f"Action Intent already exists: {ai_path.name} (generate a new action_intent_id).")
                    ]
                    st.rerun()

                try:
                    write_json(ai_path, built)
                except Exception as e:
                    st.session_state["ai_create_msgs"] = [
                        ("error", f"Failed to write Action Intent: {type(e).__name__}: {e}")
                    ]
                    st.rerun()

                st.session_state["last_action_intent_id"] = intent_obj.action_intent_id
                st.session_state["last_action_intent_digest"] = intent_obj.action_intent_digest
                st.session_state["last_action_intent_d"] = intent_obj.d

                msgs.append(
                    (
                        "success",
                        f"Created Action Intent (proposed): id={intent_obj.action_intent_id} | d={intent_obj.d}",
                    )
                )

                # 👇 SAID-like handle (copyable, stable)
                st.text_input(
                    "action_intent.d (SAID-like)",
                    value=intent_obj.d,
                    disabled=True,
                )

                # Create Correspondence (locks Role + Persona + Intent)

                # Resolve the Action Intent reference (UUID or SAID-like handle)
                ai_ref = (intent_obj.d or intent_obj.action_intent_digest or intent_obj.action_intent_id).strip()

                try:
                    correspondence_id = correspondence_create_cli(
                        ward_handle=ward_handle,
                        role_id=role_id_locked,
                        persona_id=persona_id_locked,
                        action_intent_id=ai_ref,
                    )
                    st.session_state["last_correspondence_id"] = correspondence_id
                    st.session_state["mint_correspondence_file"] = f"{correspondence_id}.json"
                    msgs.append(
                        ("success", f"Created Correspondence: {correspondence_id} (Role + Persona + Intent locked)")
                    )
                except Exception as e:
                    msgs.append(
                        ("error", f"Action Intent created, but Correspondence creation failed (ai_ref={ai_ref}): {e}")
                    )

                st.session_state["ai_create_msgs"] = msgs
                st.rerun()

# ----------------------------
# Action-intent Viewer
# ----------------------------
with st.expander("Action Intents — viewer", expanded=False):
    intents = list_action_intents_v1(ward_path)
    if not intents:
        st.info("No action intents yet.")
    else:
        labels = []
        rows = []
        for r in intents:
            intent = r.intent
            v = r.validation
            p = r.path
            badge = "✅" if v.ok else "❌"
            iid = ((intent.action_intent_id if intent else "") or p.stem).strip()
            stt = ((intent.status if intent else "") or "").strip()
            labels.append(f"{badge} {iid} ({stt})")
            rows.append((intent, v, p))

        choice = st.selectbox("Select Action Intent", labels, key="ai_view_select")
        intent, v, p = rows[labels.index(choice)]
        st.caption(f"File: {p}")
        if not v.ok:
            st.error("Invalid Action Intent:")
            for e in v.errors:
                st.write(f"- {e}")
        if intent is not None:
            st.json(intent.raw)

# ----------------------------
# Correspondence Viewer
# ----------------------------
with st.expander("Correspondences — viewer", expanded=False):
    corr_files = list_json_files(ward_path / "correspondences")
    if not corr_files:
        st.info("No correspondences yet.")
    else:
        corr_names = [p.name for p in corr_files]
        cf = st.selectbox("Select Correspondence File", corr_names, key="corr_view_select")

        p = ward_path / "correspondences" / cf
        try:
            raw = read_json(p)
            st.caption(f"File: {p}")
            st.json(raw)
        except Exception as e:
            st.error(f"Could not read correspondence JSON: {e}")

st.divider()

# ============================================================
# Mint token (left) + Warden plane (right)
# ============================================================

mint_col, warden_col = st.columns([1, 1])

# ----------------------------
# Mint Warrant Token (Correspondence-centric)
# ----------------------------
with mint_col:
    st.subheader("Mint Warrant Token (single-use)")
    st.caption("Select a Correspondence Form (locks Role + Persona + Action Intent), then mint a single-use warrant token.")

    def _norm_digest(s: str) -> str:
        s = (s or "").strip()
        if s.startswith("sha256:"):
            return s.split("sha256:", 1)[1].strip()
        return s

    def _short(s: str, n: int = 8) -> str:
        s = (s or "").strip()
        return s[:n] if s else "--------"

    if not ward_ref:
        st.error("This ward is missing ward_ref (tombed ward required).")
    else:
        corr_files = list_json_files(ward_path / "correspondences")
        if not corr_files:
            st.info("No correspondences found. Create an Action Intent + Correspondence first.")
        else:
            # Active+valid ops to resolve labels (optional UX)
            op_cards = active_valid_operation_cards(ward_path)

            # Map normalized digest -> op card
            op_by_digest: Dict[str, OperationCardV1] = {}
            for oc in op_cards:
                oc_d = (_as_dict(oc).get("d") or "").strip()
                oc_d_norm = _norm_digest(oc_d)
                if oc_d_norm:
                    op_by_digest[oc_d_norm] = oc

            corr_names = [p.name for p in corr_files]

            # Default to most recent unless session_state already points at a valid file
            cur = st.session_state.get("mint_correspondence_file")
            if cur not in corr_names:
                st.session_state["mint_correspondence_file"] = corr_names[0]

            # Precompute labels
            label_by_file: Dict[str, str] = {}
            for p in corr_files[:50]:  # hygiene: don't scan unbounded for label formatting
                try:
                    raw = read_json(p)
                    corr_id = (raw.get("correspondence_id") or p.stem).strip()
                    role_id, persona_id, ai_id, errs = _parse_correspondence_ids(raw)

                    op_part = "unknown_operation"

                    if ai_id:
                        try:
                            # IMPORTANT: ai_id may be UUID (filename stem) OR sha256:... handle
                            ai_obj = resolve_action_intent_id_or_d_to_intent(ward_path, ai_id)
                            op_digest = (ai_obj.operation_digest or "").strip()
                            op_digest_norm = _norm_digest(op_digest)

                            oc = op_by_digest.get(op_digest_norm)
                            if oc is not None:
                                op_name = (oc.operation_name or "").strip()
                                op_lbl = ""
                                oc_raw = getattr(oc, "raw", None)
                                if isinstance(oc_raw, dict):
                                    op_lbl = (oc_raw.get("label") or "").strip()

                                if op_lbl and op_name:
                                    op_part = f"{op_lbl} — {op_name}"
                                else:
                                    op_part = op_name or op_digest_norm or op_part
                            else:
                                # No matching registry card; still show the digest (normalized) for transparency
                                op_part = op_digest_norm or op_part
                        except Exception:
                            pass

                    label_by_file[p.name] = (
                        f"{op_part}  ·  corr:{corr_id}  ·  "
                        f"role:{_short(role_id)}  persona:{_short(persona_id)}  intent:{_short(ai_id)}"
                    )
                except Exception:
                    label_by_file[p.name] = p.name

            corr_file = st.selectbox(
                "Select Correspondence File",
                options=corr_names,
                key="mint_correspondence_file",
                format_func=lambda fn: label_by_file.get(fn, fn),
                help="Correspondence Forms bind Role + Persona + Action Intent into a mintable unit.",
            )

            corr_path = ward_path / "correspondences" / corr_file
            corr_raw: Dict[str, Any] = {}
            try:
                corr_raw = read_json(corr_path)
            except Exception as e:
                st.error(f"Could not read correspondence JSON: {e}")
                corr_raw = {}

            corr_id = (corr_raw.get("correspondence_id") or Path(corr_file).stem).strip()
            role_id, persona_id, ai_id, errs = _parse_correspondence_ids(corr_raw)

            with st.expander("Resolved bindings (read-only)", expanded=False):
                st.markdown("**Correspondence**")
                st.code(corr_id or "(missing)", language=None)

                st.markdown("**Role**")
                if role_id:
                    rp = ward_path / "roles" / f"{role_id}.json"
                    st.caption(f"{rp.name if rp.exists() else '(missing file)'}")
                    if rp.exists():
                        st.json(read_json(rp))
                else:
                    st.caption("(missing role_id in correspondence)")

                st.markdown("**Persona**")
                if persona_id:
                    pp = ward_path / "personas" / f"{persona_id}.json"
                    st.caption(f"{pp.name if pp.exists() else '(missing file)'}")
                    if pp.exists():
                        st.json(read_json(pp))
                else:
                    st.caption("(missing persona_id in correspondence)")

                st.markdown("**Action Intent**")
                if ai_id:
                    try:
                        ai_obj = resolve_action_intent_id_or_d_to_intent(ward_path, ai_id)

                        # Display the actual on-disk filename (UUID.json) when available
                        ap = ward_path / "action_intents" / f"{(ai_obj.action_intent_id or '').strip()}.json"
                        st.caption(f"{ap.name if ap.exists() else '(resolved, but file missing)'}")

                        st.json(ai_obj.raw)
                    except Exception:
                        st.caption("(missing file)")
                else:
                    st.caption("(missing action_intent_id in correspondence)")

                if errs:
                    st.warning("Correspondence is missing required fields: " + "; ".join(errs))

            ttl_seconds = st.number_input(
                "Justification validity window (seconds)",
                min_value=30,
                max_value=3600,
                value=600,
                step=30,
                key="mint_ttl",
                help="After expiry, the warrant can no longer justify execution.",
            )

            if st.button("Mint Warrant Token", type="primary", key="btn_mint_warrant"):
                wid = mint_warrant_cli(
                    ward_handle=ward_handle,
                    correspondence_id=corr_id,
                    ttl=int(ttl_seconds),
                )

                st.success(f"Minted warrant token: {wid}")
                st.session_state["last_warrant_id"] = wid

                # Keep downstream panes in sync
                st.session_state["warden_warrant_file"] = f"{wid}.json"
                # Force Warden pane to refresh its derived default
                st.session_state.pop("warden_proposed_operation_source", None)

# ----------------------------
# Warden Enforcement Plane
# ----------------------------
with warden_col:
    st.subheader("Warden Enforcement Plane (Control Plane)")
    st.caption("Preflight checks: signature + presence + Role/Persona/Operation compatibility. Execution burns single-use.")

    notice = st.session_state.pop("last_execution_notice", None)
    if notice:
        st.info(notice)

    warr_files = list_json_files(ward_path / "warrants")
    if not warr_files:
        st.info("No warrants found. Mint one first.")
    else:
        warrant_filename = st.selectbox(
            "Select Warrant File",
            [p.name for p in warr_files],
            key="warden_warrant_file",
        )
        selected_warrant_path = ward_path / "warrants" / warrant_filename

        try:
            warrant_obj = read_json(selected_warrant_path)
        except Exception as e:
            st.error(f"Could not read warrant JSON: {e}")
            warrant_obj = {}

        # Tolerate both {"payload": {...}} and root-token style
        if isinstance(warrant_obj, dict) and isinstance(warrant_obj.get("payload"), dict):
            token_view: Dict[str, Any] = warrant_obj.get("payload", {})
        elif isinstance(warrant_obj, dict):
            token_view = warrant_obj
        else:
            token_view = {}

        bindings = token_view.get("bindings", {}) if isinstance(token_view, dict) else {}

        def _bound_operation_said(bindings_obj: Dict[str, Any]) -> str:
            if not isinstance(bindings_obj, dict):
                return ""
            return (
                (bindings_obj.get("operation_said") or "").strip()
                or (bindings_obj.get("operation_ref") or "").strip()
                or (bindings_obj.get("operation_digest") or "").strip()
            )

        bound_op_said = _bound_operation_said(bindings)

        # Burn marker is authoritative for single-use
        def _burn_marker_exists(ward_path: Path, warrant_id: str) -> bool:
            p = ward_path / "warrants_burned" / f"{warrant_id}.burn"
            return p.exists()

        warrant_id_for_ui = ""
        if isinstance(token_view, dict):
            warrant_id_for_ui = (token_view.get("warrant_id") or "").strip()
        if not warrant_id_for_ui:
            warrant_id_for_ui = warrant_filename.replace(".json", "").strip()

        consumed = _burn_marker_exists(ward_path, warrant_id_for_ui)

        st.markdown(f"**Bound operation (SAID):** `{bound_op_said or 'missing'}`")
        st.markdown(f"**Single-use consumed:** `{str(consumed)}`")

        # IMPORTANT: default override to blank (no override) when switching warrants
        if st.session_state.get("warden_proposed_operation_source") != warrant_filename:
            st.session_state["warden_proposed_operation"] = ""
            st.session_state["warden_proposed_operation_source"] = warrant_filename

        proposed_operation = st.text_input(
            "Proposed operation SAID (optional override)",
            key="warden_proposed_operation",
            help=(
                "Leave blank to evaluate the warrant's bound operation. "
                "If you enter an override, it MUST be the operation SAID and must match bindings.operation_said exactly."
            ),
        )

        presence_max_age = st.number_input(
            "Presence freshness window (seconds)",
            min_value=30,
            max_value=3600,
            value=300,
            step=30,
            key="warden_presence_max_age",
        )

        if not ward_ref:
            st.error("This ward is missing ward_ref (tombed ward required).")
        else:
            admission_key = _admission_key(ward_ref, warrant_filename, proposed_operation)
            colW1, colW2 = st.columns([1, 1])

            # --- PRE-FLIGHT (pure check): verify signature + warden_check (NO burn) ---
            with colW1:
                if st.button("Run Warden Check", type="primary", key="btn_run_warden_check"):
                    if consumed:
                        ok = False
                        res = {"ok": False, "reason": "DENIED: warrant already consumed (burn marker exists)"}
                    else:
                        try:
                            sig_ok, sig_reason = warden_verify_signature(ward_path, warrant_obj)
                        except Exception as e:
                            sig_ok, sig_reason = False, f"signature verification error: {e}"

                        if not sig_ok:
                            ok = False
                            res = {
                                "ok": False,
                                "reason": f"DENIED: {sig_reason}",
                                "verified_signature": False,
                            }
                        else:
                            decision = warden_check(
                                ward_path=ward_path,
                                warrant_obj=warrant_obj,
                                presence_max_age_seconds=int(presence_max_age),
                            )
                            decision.verified_signature = True

                            # Optional override check (SAID compare)
                            if proposed_operation.strip():
                                expected = bound_op_said
                                proposed = proposed_operation.strip()
                                if expected and proposed != expected:
                                    decision.ok = False
                                    decision.reason = "DENIED: operation_mismatch (preflight)"
                                    d = decision.to_dict()
                                    d.update({
                                        "expected_operation_said": expected,
                                        "proposed_operation_said": proposed,
                                    })
                                    ok, res = False, d
                                else:
                                    ok, res = bool(decision.ok), decision.to_dict()
                            else:
                                ok, res = bool(decision.ok), decision.to_dict()

                    _set_last_admission(admission_key, ok, res)

            last_key = st.session_state.get("last_admission_key")
            last_ok = st.session_state.get("last_admission_ok", False)

            # STRICT single-use UX gate:
            # - must have an ADMITTED preflight for this exact key
            # - must NOT be consumed yet
            can_execute = (last_key == admission_key) and bool(last_ok) and (not consumed)

            exec_help = None
            if consumed:
                exec_help = "This warrant is already consumed. Mint a new warrant to execute again."
            elif not ((last_key == admission_key) and bool(last_ok)):
                exec_help = "Run Warden Check and get ADMITTED before attempting execution."

            with colW2:
                exec_now = st.button(
                    "Attempt Execution",
                    disabled=not can_execute,
                    key="btn_admit_execute",
                    help=exec_help,
                )

            # Show last preflight result
            if st.session_state.get("last_admission_key") == admission_key:
                res = st.session_state.get("last_admission_result")
                if res is not None:
                    if st.session_state.get("last_admission_ok"):
                        st.success("✅ ADMITTED (preflight)")
                    else:
                        st.error(f"❌ {get_reason(res)}")

                    trace_lines = pretty_trace(res)
                    if trace_lines:
                        st.markdown("**Compliance trace**")
                        for line in trace_lines:
                            st.write(f"- {line}")

                    st.markdown("**Warrant token (what preflight evaluated)**")
                    st.json(token_view if isinstance(token_view, dict) else {})

            # --- EXECUTION: do NOT call warden_admit here.
            # agent_operation will call warden_admit and burn atomically.
            if exec_now:
                warrant_id_to_execute = warrant_filename.replace(".json", "").strip()
                try:
                    output = run_agent_operation_cli(ward_handle, warrant_id_to_execute)
                    st.success("Operation attempted under warrant evidence (agent_operation enforced single-use)")
                    st.code(output)

                    st.session_state["last_warrant_id"] = warrant_id_to_execute
                    st.session_state["last_execution_notice"] = (
                        "Operation attempted. Warrant is now consumed (burn marker). Mint a new warrant token to run again."
                    )

                    for k in ["last_admission_key", "last_admission_ok", "last_admission_result"]:
                        st.session_state.pop(k, None)

                    st.rerun()
                except subprocess.CalledProcessError as e:
                    st.error("Execution failed / denied")
                    st.code(getattr(e, "output", "") or str(e))

st.divider()

# ============================================================
# Post-state: Records + Receipts
# ============================================================

colX, colY = st.columns([1, 1])

with colX:
    st.subheader("Execution Records (Warrant Tokens)")
    st.caption("Read-only records of evaluated and attempted actions")
    st.caption("These warrant tokens have already been evaluated by the Warden and consumed during an execution attempt. They are displayed here for inspection and audit purposes only.")
    warrs = list_json_files(ward_path / "warrants")
    if warrs:
        wf = st.selectbox("Select Warrant File", [p.name for p in warrs], key="warrant_select_post")
        st.json(read_json(ward_path / "warrants" / wf))
    else:
        st.info("No warrants found.")

with colY:
    st.subheader("Execution Receipts")
    st.caption("Read-only execution-boundary audit records")
    st.caption("These receipts were generated by the Warden after a warrant token was evaluated and an execution attempt was made. They are displayed here for inspection and audit purposes only.")
    recs = list_json_files(ward_path / "receipts")
    if recs:
        rf = st.selectbox("Select Receipt File", [p.name for p in recs], key="receipt_select_post")
        st.json(read_json(ward_path / "receipts" / rf))
    else:
        st.info("No receipts found.")

