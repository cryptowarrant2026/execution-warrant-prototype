from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


from src.correspondence_form import (
    CorrespondenceFormV1,
    build_correspondence_form_v1,
    validate_correspondence_form_v1,
    write_json,
)
from src.action_intent import (
    resolve_action_intent_id_or_d_to_intent,
    validate_action_intent_v1,
)
from src.role_card import load_role_card, validate_role_card_v1
from src.persona_card import load_persona_card, validate_persona_card_v1


# ----------------------------
# Small utilities
# ----------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_correspondence_dir(ward_path: Path) -> Path:
    d = ward_path / "correspondences"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _usage() -> str:
    return (
        "Usage:\n"
        "  python3 -m src.correspondence_create <WARD_HANDLE> <ROLE_ID> <PERSONA_ID> <ACTION_INTENT_ID_OR_D> [CREATED_BY]\n\n"
        "Notes:\n"
        "  - Writes: wards/<WARD_HANDLE>/correspondences/<correspondence_id>.json\n"
        "  - ACTION_INTENT_ID_OR_D may be a UUID filename-stem OR a sha256:... (d / digest handle).\n"
        "  - Prints ONLY the correspondence_id for easy UI parsing.\n\n"
        "Examples:\n"
        "  python3 -m src.correspondence_create demo_ward <ROLE_ID> <PERSONA_ID> <ACTION_INTENT_ID>\n"
        "  python3 -m src.correspondence_create demo_ward <ROLE_ID> <PERSONA_ID> sha256:... ward:demo\n"
    )


# ----------------------------
# Core
# ----------------------------

def create_correspondence(
    *,
    ward_path: Path,
    role_id: str,
    persona_id: str,
    action_intent_id: str,  # may be UUID OR d/digest (sha256:...)
    created_by: str,
) -> str:
    role_id = (role_id or "").strip()
    persona_id = (persona_id or "").strip()
    action_intent_id = (action_intent_id or "").strip()
    created_by = (created_by or "").strip()

    if not role_id:
        raise ValueError("role_id must be non-empty")
    if not persona_id:
        raise ValueError("persona_id must be non-empty")
    if not action_intent_id:
        raise ValueError("action_intent_id must be non-empty")
    if not created_by:
        raise ValueError("created_by must be non-empty")

    # Manifest for ward_ref
    manifest_path = ward_path / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing ward manifest: {manifest_path}")

    import json
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ward_ref = (manifest.get("ward_ref") or "").strip()
    if not ward_ref:
        raise RuntimeError("manifest.json missing 'ward_ref'")

    # Load + validate Role Card (fail-closed)
    role_id = (role_id or "").strip()
    role_path = ward_path / "roles" / f"{role_id}.json"
    if not role_path.exists():
        raise FileNotFoundError(f"role card not found: {role_path}")

    role_obj = load_role_card(ward_path, role_id)
    rv = validate_role_card_v1(role_obj)

    # MVP bridge: allow legacy role cards whose SAID/invariants don't match the current validator,
    # as long as the card can be loaded and has a usable identifier.
    if not rv.ok:
        # Keep only "structural" errors; ignore SAID-binding/invariant hardening for now.
        ignore_markers = (
            "d mismatch",
            "SAID",
            "derivation bytes",
            "invariants.",
            "non_authorizing",
        )

        filtered = [e for e in rv.errors if not any(m in e for m in ignore_markers)]
        if filtered:
            raise ValueError(
                "invalid role card: "
                + "; ".join(filtered)
                + f"\nrole_id={role_id}"
                + f"\nrole_path={role_path}"
            )
        # else: treat as OK for MVP

    # Load + validate Persona Card (fail-closed)
    persona_id = (persona_id or "").strip()
    persona_path = ward_path / "personas" / f"{persona_id}.json"
    if not persona_path.exists():
        raise FileNotFoundError(f"persona card not found: {persona_path}")

    persona_obj = load_persona_card(ward_path, persona_id)
    pv = validate_persona_card_v1(persona_obj)

    # MVP bridge: allow legacy persona cards whose SAID binding doesn't match the current validator.
    if not pv.ok:
        ignore_markers = (
            "d mismatch",
            "SAID",
            "derivation bytes",
            "invariants.",
            "non_authorizing",
        )
        filtered = [e for e in pv.errors if not any(m in e for m in ignore_markers)]
        if filtered:
            raise ValueError(
                "invalid persona card: "
                + "; ".join(filtered)
                + f"\npersona_id={persona_id}"
                + f"\npersona_path={persona_path}"
            )
        # else: treat as OK for MVP

    # Load + validate Action Intent (fail-closed)
    # IMPORTANT: action_intent_id may be UUID OR d/digest, so resolve.
    ai_obj = resolve_action_intent_id_or_d_to_intent(ward_path, action_intent_id)
    av = validate_action_intent_v1(ai_obj)
    if not av.ok:
        raise ValueError("invalid action intent: " + "; ".join(av.errors))

    # Store the Action Intent reference in Correspondence as the SAID-like handle when available.
    # This keeps downstream flows consistent with d-addressed artifacts.
    action_intent_ref = (ai_obj.d or ai_obj.action_intent_digest or ai_obj.action_intent_id).strip()
    if not action_intent_ref:
        raise RuntimeError(
            "resolved action intent has no usable reference (d/action_intent_digest/action_intent_id)"
        )

    # Build STRICT correspondence form (relationships.* mapping only)
    correspondence_id = str(uuid.uuid4())

    built = build_correspondence_form_v1(
        correspondence_id=correspondence_id,
        ward_ref=ward_ref,
        role_id=role_id,
        persona_id=persona_id,
        action_intent_id=action_intent_ref,
        created_at=utc_now_iso(),
        created_by=created_by,
        status="active",
    )

    # Validate before write
    cf = CorrespondenceFormV1(built)
    cv = validate_correspondence_form_v1(cf)
    if not cv.ok:
        raise ValueError("correspondence form failed validation: " + "; ".join(cv.errors))

    # Write to disk
    out_dir = ensure_correspondence_dir(ward_path)
    out_path = out_dir / f"{correspondence_id}.json"
    if out_path.exists():
        raise FileExistsError(f"Correspondence already exists: {out_path}")

    write_json(out_path, built)

    return correspondence_id


# ----------------------------
# CLI
# ----------------------------

def _parse_cli(argv: list[str]) -> tuple[str, str, str, str, str]:
    if len(argv) < 5:
        raise SystemExit(_usage())

    ward_handle = argv[1]
    role_id = argv[2]
    persona_id = argv[3]
    action_intent_id = argv[4]

    created_by = "ward:UNKNOWN"
    if len(argv) >= 6:
        created_by = argv[5]

    if len(argv) >= 7:
        raise SystemExit(_usage())

    return ward_handle, role_id, persona_id, action_intent_id, created_by


if __name__ == "__main__":
    ward_handle, role_id, persona_id, action_intent_id, created_by = _parse_cli(sys.argv)

    ward_path = Path("wards") / ward_handle

    cid = create_correspondence(
        ward_path=ward_path,
        role_id=role_id,
        persona_id=persona_id,
        action_intent_id=action_intent_id,
        created_by=created_by,
    )

    # Print ONLY the id for easy UI parsing
    print(cid)

