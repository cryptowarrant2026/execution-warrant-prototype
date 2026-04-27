# benchmarks/harness.py
"""
Shared harness for RBC benchmark suite.

Provides:
  - timed_ms()        : run a callable N times, return per-call timings in ms
  - stats()           : compute mean / p50 / p95 / p99 / max over a timing list
  - BenchmarkFixtures : create a fully-wired temporary Ward environment
"""
from __future__ import annotations

import json
import os
import platform
import statistics
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.ward_create import create_ward
from src.said_python import python_said_generate
from src.action_intent import build_action_intent_v1, ActionIntentV1
from src.correspondence_form import build_correspondence_form_v1, CorrespondenceFormV1
from src.warrant_mint import mint_warrant


# ──────────────────────────────────────────────────────────────────
# Timing helpers
# ──────────────────────────────────────────────────────────────────

def timed_ms(fn: Callable[[], Any], n: int = 1000, warmup: int = 50) -> List[float]:
    """
    Run fn() (warmup + n) times; discard the first `warmup` results.
    Returns a list of n timings in milliseconds.
    """
    for _ in range(warmup):
        fn()

    times: List[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1_000.0)
    return times


def stats(timings: List[float]) -> Dict[str, Any]:
    """Return mean, p50, p95, p99, max, min (all in ms) plus sample size."""
    if not timings:
        return {"n": 0}
    s = sorted(timings)
    n = len(s)

    def pct(p: float) -> float:
        idx = (n - 1) * p / 100.0
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        frac = idx - lo
        return s[lo] + (s[hi] - s[lo]) * frac

    return {
        "n": n,
        "mean_ms": round(statistics.mean(timings), 4),
        "p50_ms":  round(pct(50), 4),
        "p95_ms":  round(pct(95), 4),
        "p99_ms":  round(pct(99), 4),
        "max_ms":  round(max(timings), 4),
        "min_ms":  round(min(timings), 4),
        "stdev_ms": round(statistics.stdev(timings) if n > 1 else 0.0, 4),
    }


# ──────────────────────────────────────────────────────────────────
# Hardware metadata
# ──────────────────────────────────────────────────────────────────

def hardware_info() -> Dict[str, str]:
    """Best-effort hardware / OS snapshot for reporting."""
    info: Dict[str, str] = {
        "python_version": platform.python_version(),
        "os":             platform.platform(),
        "cpu":            platform.processor() or platform.machine(),
        "cpu_count":      str(os.cpu_count()),
    }
    try:
        import psutil  # type: ignore
        mem = psutil.virtual_memory()
        info["ram_gb"] = str(round(mem.total / 1e9, 1))
    except ImportError:
        info["ram_gb"] = "unknown (psutil not installed)"
    return info


# ──────────────────────────────────────────────────────────────────
# JSON / path helpers
# ──────────────────────────────────────────────────────────────────

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(p: Path, obj: Dict[str, Any]) -> None:
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def read_json(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


# ──────────────────────────────────────────────────────────────────
# Minimal valid artifact builders
# ──────────────────────────────────────────────────────────────────

def _build_role_card(template_ref: str = "BENCH_TEMPLATE_REF") -> Dict[str, Any]:
    """Build a minimal valid Role Card (instance) and embed its SAID."""
    raw = {
        "type": "cca/agency/concept/1.0",
        "card_type": "role",
        "status": "active",
        "d": "",
        "card_template_ref": template_ref,
        "role": {
            "name": "bench_clinician",
            "label": "Benchmark Clinician",
            "description": "Benchmark role for RBC metrics extraction.",
        },
        "accountable_office": {
            "ref": "office:benchmark",
            "label": "Benchmark Accountable Office",
            "resolution_scope": "private",
        },
        "constraint_grammar": {"grammar_ref": "rbc_constraint_grammar_dpv_minimal_v1"},
        "constraints": {
            "scope": ["dpv:Treatment"],
            "prohibitions": [],
            "obligations": [],
        },
        "role_tags": {"scope": ["dpv:Treatment"], "prohibitions": []},
        "invariants": {"non_authorizing": True},
    }
    return python_said_generate(raw)


def _build_persona_card(template_ref: str = "BENCH_TEMPLATE_REF") -> Dict[str, Any]:
    """Build a minimal valid Persona Card (instance) and embed its SAID."""
    raw = {
        "type": "cca/agency/concept/1.0",
        "card_type": "persona",
        "status": "active",
        "d": "",
        "card_template_ref": template_ref,
        "persona": {
            "name": "bench_attending",
            "label": "Benchmark Attending Physician",
            "description": "Benchmark persona for RBC metrics extraction.",
        },
        "duty_frame": {
            "ref": "duty:benchmark",
            "label": "Benchmark Duty Frame",
            "resolution_scope": "private",
        },
        "constraint_grammar": {"grammar_ref": "rbc_constraint_grammar_dpv_minimal_v1"},
        "constraints": {
            "scope": ["dpv:Treatment"],
            "prohibitions": [],
            "obligations": [],
        },
        "persona_tags": {"scope": ["dpv:Treatment"]},
        "composition": {},
        "invariants": {"non_authorizing": True},
    }
    return python_said_generate(raw)


def _build_operation_card(template_ref: str = "BENCH_TEMPLATE_REF") -> Dict[str, Any]:
    """Build a minimal valid Operation Card (instance) and embed its SAID."""
    raw = {
        "type": "cca/execution/action/1.0",
        "card_type": "operation",
        "status": "active",
        "d": "",
        "card_template_ref": template_ref,
        # Legacy top-level field kept so warrant_mint registry scan can find it.
        "operation_name": "bench_read_patient_record",
        "operation": {
            "name": "bench_read_patient_record",
            "label": "Benchmark Read Patient Record",
            "description": "Read a single patient record — benchmark fixture.",
        },
        "constraint_grammar": {"grammar_ref": "rbc_constraint_grammar_ocv_minimal_v1"},
        "constraints": {
            "scope": ["read"],
            "prohibitions": [],
            "obligations": [],
        },
    }
    return python_said_generate(raw)


def _make_action_intent_raw(action_intent_id: str, operation_said: str) -> Dict[str, Any]:
    return build_action_intent_v1(
        action_intent_id=action_intent_id,
        operation_digest=operation_said,
        targets=[{
            "target_kind": "patient_record",
            "target_ref": "patient:bench-001",
            "selector": None,
        }],
        context_binding={
            "zone_ref": "zone:hipaa_covered_entity",
            "overlay_refs": [],
            "jurisdiction_ref": "jurisdiction:us_hipaa",
            "effective_time": utc_now_iso(),
        },
        parameters={"read_depth": "summary", "include_notes": False},
        scope={
            "max_records": 1,
            "time_window_seconds": 3600,
            "field_allowlist": ["id", "name", "dob", "mrn"],
            "data_categories": ["dpv:MedicalHealth"],
        },
        created_at=utc_now_iso(),
        created_by="ward:benchmark",
        status="proposed",
    )


def _build_authority_policy_simple(
    role_id: str, persona_id: str, operation_said: str
) -> Dict[str, Any]:
    """Minimal Authority Policy — no complex predicates (1.4 simple case)."""
    raw = {
        "type": "cca/governance/concept/1.0",
        "card_type": "authority_policy",
        "status": "active",
        "d": "",
        "card_template_ref": "BENCH_TEMPLATE_REF",
        "policy": {
            "name": "bench_policy_simple",
            "label": "Benchmark Simple Policy",
            "description": "Simple policy, no complex predicates.",
            "profile": "default",
            "fail_closed": True,
            "ttl_seconds_default": 600,
            "ttl_seconds_max": 3600,
        },
        "constraint_grammars": {
            "privacy_grammar_ref": "rbc_constraint_grammar_dpv_minimal_v1",
            "execution_grammar_ref": "rbc_constraint_grammar_ocv_minimal_v1",
        },
        "scope": {
            "role_refs": [role_id],
            "persona_refs": [persona_id],
            "operation_refs": [operation_said],
            "target_kind_allowlist": ["patient_record"],
            "max_targets": 10,
        },
        "privacy_constraints": {
            "scope": ["dpv:Treatment"],
            "prohibitions": [],
        },
        "execution_constraints": {
            "scope": ["read"],
            "prohibitions": [],
        },
        "scope_defaults": {},
        "scope_max": {},
        "trigger": {"mode": "on_warrant_mint", "conditions": []},
        "termination": {"mode": "on_burn"},
        "explicit_non_goals": [],
        "alignment_notes": {},
    }
    return python_said_generate(raw)


def _build_authority_policy_complex(
    role_id: str, persona_id: str, operation_said: str
) -> Dict[str, Any]:
    """Authority Policy with 2–3 predicate conditions (1.4 moderate-complexity case)."""
    raw = {
        "type": "cca/governance/concept/1.0",
        "card_type": "authority_policy",
        "status": "active",
        "d": "",
        "card_template_ref": "BENCH_TEMPLATE_REF",
        "policy": {
            "name": "bench_policy_complex",
            "label": "Benchmark Complex Policy",
            "description": "Moderately complex policy, 3 predicate conditions.",
            "profile": "default",
            "fail_closed": True,
            "ttl_seconds_default": 300,
            "ttl_seconds_max": 1800,
        },
        "constraint_grammars": {
            "privacy_grammar_ref": "rbc_constraint_grammar_dpv_minimal_v1",
            "execution_grammar_ref": "rbc_constraint_grammar_ocv_minimal_v1",
        },
        "scope": {
            "role_refs": [role_id],
            "persona_refs": [persona_id],
            "operation_refs": [operation_said],
            "target_kind_allowlist": ["patient_record", "encounter_note"],
            "max_targets": 5,
            "zone_refs": ["zone:hipaa_covered_entity"],
            "jurisdiction_refs": ["jurisdiction:us_hipaa"],
        },
        "privacy_constraints": {
            "scope": ["dpv:Treatment", "dpv:MedicalHealth"],
            "prohibitions": ["dpv:CommercialResearch", "dpv:Advertising"],
        },
        "execution_constraints": {
            "scope": ["read"],
            "prohibitions": ["write", "delete", "export"],
        },
        "scope_defaults": {"time_window_seconds": 3600, "max_records": 50},
        "scope_max":      {"time_window_seconds": 7200, "max_records": 100},
        "trigger": {
            "mode": "on_warrant_mint",
            "conditions": [
                {"field": "context_binding.zone_ref",
                 "op": "eq",
                 "value": "zone:hipaa_covered_entity"},
                {"field": "context_binding.jurisdiction_ref",
                 "op": "in",
                 "value": ["jurisdiction:us_hipaa", "jurisdiction:us_hitech"]},
                {"field": "scope.max_records",
                 "op": "lte",
                 "value": 100},
            ],
        },
        "termination": {"mode": "on_burn"},
        "explicit_non_goals": [
            "Does not grant access to research databases.",
            "Does not permit bulk data export.",
        ],
        "alignment_notes": {
            "hipaa_basis": "45 CFR §164.506 Treatment exception.",
            "dpv_mapping": "Treatment purpose under DPV v2.",
        },
    }
    return python_said_generate(raw)


# ──────────────────────────────────────────────────────────────────
# BenchmarkFixtures
# ──────────────────────────────────────────────────────────────────

class BenchmarkFixtures:
    """
    Creates a fully-wired temporary Ward suitable for all benchmark groups.

    On construction:
      • Creates a Ward (keys, manifest, directory layout).
      • Writes Role Card, Persona Card, and Operation Card to disk.
      • Creates an Action Intent and a Correspondence Form.
      • Builds two Authority Policy objects (simple / complex) — held in memory
        for 1.4 evaluation timing; not written to disk unless needed.

    Thread safety: each BenchmarkFixtures instance owns its own temp directory.
    For Group 2 concurrency tests, create one shared instance.

    Usage:
        fx = BenchmarkFixtures()
        warrant_id, warrant_path = fx.fresh_warrant()
        ...
        fx.cleanup()

    Or as a context manager:
        with BenchmarkFixtures() as fx:
            ...
    """

    def __init__(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)

        wards_root = self.root / "wards"
        ward = create_ward(
            root=wards_root,
            ward_type="benchmark",
            label="RBC Benchmark Ward",
        )
        self.ward_path: Path = ward.ward_path
        self.ward_ref: str  = ward.ward_ref

        # Ensure all sub-directories exist
        for d in [
            "roles", "personas", "operations",
            "action_intents", "correspondences",
            "warrants", "warrants_burned", "receipts",
            "authority_policies",
        ]:
            (self.ward_path / d).mkdir(parents=True, exist_ok=True)

        # ── Role Card ──────────────────────────────────────────────
        self.role_raw: Dict[str, Any] = _build_role_card()
        self.role_said: str = self.role_raw["d"]
        write_json(
            self.ward_path / "roles" / f"{self.role_said}.json",
            self.role_raw,
        )

        # ── Persona Card ───────────────────────────────────────────
        self.persona_raw: Dict[str, Any] = _build_persona_card()
        self.persona_said: str = self.persona_raw["d"]
        write_json(
            self.ward_path / "personas" / f"{self.persona_said}.json",
            self.persona_raw,
        )

        # ── Operation Card ─────────────────────────────────────────
        self.operation_raw: Dict[str, Any] = _build_operation_card()
        self.operation_said: str = self.operation_raw["d"]
        write_json(
            self.ward_path / "operations" / f"{self.operation_said}.json",
            self.operation_raw,
        )

        # ── Action Intent ──────────────────────────────────────────
        self.action_intent_id: str = str(uuid.uuid4())
        self.action_intent_raw: Dict[str, Any] = _make_action_intent_raw(
            self.action_intent_id, self.operation_said
        )
        self.action_intent_digest: str = self.action_intent_raw["action_intent_digest"]
        write_json(
            self.ward_path / "action_intents" / f"{self.action_intent_id}.json",
            self.action_intent_raw,
        )
        self.action_intent_obj: ActionIntentV1 = ActionIntentV1(self.action_intent_raw)

        # ── Correspondence Form ────────────────────────────────────
        manifest = read_json(self.ward_path / "manifest.json")
        ward_ref_manifest = manifest["ward_ref"]
        self.correspondence_id: str = str(uuid.uuid4())
        cf_raw = build_correspondence_form_v1(
            correspondence_id=self.correspondence_id,
            ward_ref=ward_ref_manifest,
            role_id=self.role_said,
            persona_id=self.persona_said,
            action_intent_id=self.action_intent_id,
            created_at=utc_now_iso(),
            created_by="ward:benchmark",
            status="active",
        )
        write_json(
            self.ward_path / "correspondences" / f"{self.correspondence_id}.json",
            cf_raw,
        )

        # ── Authority Policies (in-memory for 1.4) ─────────────────
        self.policy_simple_raw: Dict[str, Any] = _build_authority_policy_simple(
            self.role_said, self.persona_said, self.operation_said
        )
        self.policy_complex_raw: Dict[str, Any] = _build_authority_policy_complex(
            self.role_said, self.persona_said, self.operation_said
        )

    # ── Warrant helpers ────────────────────────────────────────────

    def fresh_warrant(self, ttl: int = 600) -> Tuple[str, Path]:
        """
        Mint a fresh single-use warrant and return (warrant_id, warrant_path).

        NOTE: validate_operation_registry is disabled here.  The operation card
        in the fixture uses the nested `operation.name` layout, but warrant_mint's
        registry scan only recognises top-level `operation_name` (known MVP gap).
        Both layouts are present in the fixture card; the scan would still find it
        via `operation_name`, but we disable it to keep timing clean for 1.5.
        Enable for a more realistic end-to-end figure if desired.
        """
        w = mint_warrant(
            ward_path=self.ward_path,
            correspondence_id=self.correspondence_id,
            ttl_seconds=ttl,
            validate_operation_registry=False,
        )
        return w.warrant_id, w.warrant_path

    def premint_warrants(self, n: int, ttl: int = 600) -> List[Tuple[str, Path]]:
        """Pre-mint n warrants for use in admission timing loops."""
        return [self.fresh_warrant(ttl=ttl) for _ in range(n)]

    # ── Context manager ────────────────────────────────────────────

    def __enter__(self) -> "BenchmarkFixtures":
        return self

    def __exit__(self, *_: Any) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        try:
            self._tmpdir.cleanup()
        except Exception:
            pass

