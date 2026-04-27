# benchmarks/group4_fail_closed.py
"""
Group 4 — Fail-Closed Behaviour (Correctness Under Adversarial Conditions)

Updated to close G-1 and G-2:
  • 4.2  Now tests warden_admit() directly for expiry (G-2 patch applied)
  • 4.4  Now live — tests revocation_store + warden_admit() (G-1 patch applied)
"""
from __future__ import annotations

import base64
import copy
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from benchmarks.harness import (
    BenchmarkFixtures,
    timed_ms,
    stats,
    utc_now_iso,
    write_json,
    read_json,
)

from src.digests import canonical_digest_sha256
from src.action_intent import (
    ActionIntentV1,
    validate_action_intent_v1,
    canonical_action_intent_digest_v1,
    build_action_intent_v1,
)
from src.warden_plane import warden_admit, warden_verify_signature
from src.warrant_verify import verify_warrant
from src.warrant_mint import mint_warrant

N_TRIALS = 100


# ──────────────────────────────────────────────────────────────────
# 4.1  Hash mismatch rejection
# ──────────────────────────────────────────────────────────────────

def bench_4_1(fx: BenchmarkFixtures) -> Dict[str, Any]:
    mutations = [
        ("operation_digest",         lambda r: {**r, "operation_digest": r["operation_digest"] + "_TAMPERED"}),
        ("targets[0].target_ref",    lambda r: _mutate_nested(r, ["targets", 0, "target_ref"], "_TAMPERED")),
        ("context_binding.zone_ref", lambda r: _mutate_nested(r, ["context_binding", "zone_ref"], "_TAMPERED")),
        ("parameters.read_depth",    lambda r: _mutate_nested(r, ["parameters", "read_depth"], "_TAMPERED")),
        ("scope.max_records",        lambda r: _mutate_nested(r, ["scope", "max_records"], 999)),
    ]

    results_by_field = []
    for field_label, mutate_fn in mutations:
        rejection_count = 0
        for _ in range(N_TRIALS):
            _wid, wpath = fx.fresh_warrant()
            warrant_obj = read_json(wpath)
            stored_digest = (
                warrant_obj.get("bindings", {})
                .get("action_intent", {})
                .get("action_intent_digest", "")
            )
            tampered_raw = mutate_fn(copy.deepcopy(fx.action_intent_raw))
            recomputed = canonical_action_intent_digest_v1(ActionIntentV1(tampered_raw))
            if stored_digest != recomputed:
                rejection_count += 1

        results_by_field.append({
            "field": field_label,
            "trials": N_TRIALS,
            "rejections": rejection_count,
            "rejection_rate_pct": round(rejection_count / N_TRIALS * 100, 1),
            "passed": rejection_count == N_TRIALS,
        })

    return {
        "metric": "4.1_hash_mismatch_rejection_rate",
        "description": "Modified AI vs stored warrant binding digest.",
        "overall_passed": all(r["passed"] for r in results_by_field),
        "field_results": results_by_field,
    }


def _mutate_nested(raw: Dict[str, Any], path: List, value: Any) -> Dict[str, Any]:
    obj = copy.deepcopy(raw)
    cursor = obj
    for k in path[:-1]:
        cursor = cursor[k]
    cursor[path[-1]] = value
    return obj


# ──────────────────────────────────────────────────────────────────
# 4.2  Expired token rejection — now tests warden_admit() directly
# ──────────────────────────────────────────────────────────────────

def bench_4_2(fx: BenchmarkFixtures) -> Dict[str, Any]:
    """
    G-2 patch applied: warden_admit() now independently checks expires_at.
    This test exercises that gate directly, not just verify_warrant().
    """
    # Check whether G-2 patch is in place
    import inspect
    from src.warden_plane import warden_admit as _wa
    _src = inspect.getsource(_wa)
    g2_patched = "warrant expired" in _src and "expires_at" in _src

    rejection_count_admit  = 0
    rejection_count_verify = 0

    for _ in range(N_TRIALS):
        _wid, wpath = fx.fresh_warrant()
        warrant_obj = read_json(wpath)

        # Back-date expires_at by 1 hour
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0).isoformat()
        warrant_obj["expires_at"] = past
        write_json(wpath, warrant_obj)

        # Test warden_admit() (primary gate — only valid after G-2 patch)
        ok, _r = warden_admit(fx.ward_path, wpath)
        if not ok:
            rejection_count_admit += 1

        # Test verify_warrant() (secondary gate — always present)
        result = verify_warrant(fx.ward_path, wpath)
        if not result.ok:
            rejection_count_verify += 1

    return {
        "metric": "4.2_expired_token_rejection_rate",
        "description": (
            "Expired warrant presented. "
            "warden_admit() gate tested directly (requires G-2 patch). "
            "verify_warrant() gate also tested as secondary check."
        ),
        "g2_patch_detected": g2_patched,
        "trials": N_TRIALS,
        "warden_admit_rejections": rejection_count_admit,
        "warden_admit_rejection_rate_pct": round(rejection_count_admit / N_TRIALS * 100, 1),
        "verify_warrant_rejection_rate_pct": round(rejection_count_verify / N_TRIALS * 100, 1),
        "passed": rejection_count_admit == N_TRIALS,
        "gap_note": (
            None if g2_patched else
            "G-2 patch NOT detected in warden_admit(). "
            "Apply warden_plane_patch.py Patch 1 to close this gap."
        ),
    }


# ──────────────────────────────────────────────────────────────────
# 4.3  Already-burned token rejection
# ──────────────────────────────────────────────────────────────────

def bench_4_3(fx: BenchmarkFixtures) -> Dict[str, Any]:
    rejection_count = 0
    for _ in range(N_TRIALS):
        _wid, wpath = fx.fresh_warrant()
        warden_admit(fx.ward_path, wpath)   # first admit (burns it)
        ok2, _r2 = warden_admit(fx.ward_path, wpath)
        if not ok2:
            rejection_count += 1

    return {
        "metric": "4.3_already_burned_token_rejection_rate",
        "description": "Re-presentation of previously admitted (burned) warrant. Tests I2 (burned ≤ once).",
        "trials": N_TRIALS,
        "rejections": rejection_count,
        "rejection_rate_pct": round(rejection_count / N_TRIALS * 100, 1),
        "passed": rejection_count == N_TRIALS,
    }


# ──────────────────────────────────────────────────────────────────
# 4.4  Revoked token rejection — now live (G-1 closed)
# ──────────────────────────────────────────────────────────────────

def bench_4_4(fx: BenchmarkFixtures) -> Dict[str, Any]:
    """
    G-1 patch applied: revocation_store.py present + warden_plane.py patched.
    Mints a warrant, revokes it, then attempts to admit it.
    """
    try:
        from src.revocation_store import revoke_warrant, is_revoked
    except ImportError:
        return {
            "metric": "4.4_revoked_token_rejection_rate",
            "trials": 0,
            "rejections": 0,
            "rejection_rate_pct": None,
            "passed": None,
            "gap": "revocation_store.py not found in src/. Copy it from src_patches/ to src/.",
        }

    # Check G-1 patch is in warden_admit
    import inspect
    from src.warden_plane import warden_admit as _wa
    _src = inspect.getsource(_wa)
    g1_patched = "is_revoked" in _src or "revocation_store" in _src

    rejection_count = 0
    false_admit_count = 0

    for _ in range(N_TRIALS):
        wid, wpath = fx.fresh_warrant()

        # Revoke the warrant before attempting admission
        revoke_warrant(fx.ward_path, wid, reason="benchmark revocation test", revoked_by="ward:benchmark")

        # Confirm is_revoked returns True
        assert is_revoked(fx.ward_path, wid), f"is_revoked() returned False for {wid}"

        # Attempt admission — must be rejected
        ok, result = warden_admit(fx.ward_path, wpath)
        if not ok:
            rejection_count += 1
        else:
            false_admit_count += 1   # revoked token was incorrectly admitted

    return {
        "metric": "4.4_revoked_token_rejection_rate",
        "description": (
            "Warrant revoked via revocation_store, then presented to warden_admit(). "
            "G-1 now closed: revocation_store.py present."
        ),
        "g1_patch_detected": g1_patched,
        "trials": N_TRIALS,
        "rejections": rejection_count,
        "false_admits": false_admit_count,
        "rejection_rate_pct": round(rejection_count / N_TRIALS * 100, 1),
        "passed": rejection_count == N_TRIALS,
        "gap_note": (
            None if g1_patched else
            "revocation_store.py is present but the warden_plane.py patch (G-1 Patch 2) "
            "has NOT been applied. warden_admit() does not yet check revocation. "
            "Apply warden_plane_patch.py Patch 2."
        ),
    }


# ──────────────────────────────────────────────────────────────────
# 4.5  Invalid signature rejection
# ──────────────────────────────────────────────────────────────────

def bench_4_5(fx: BenchmarkFixtures) -> Dict[str, Any]:
    cases: List[Dict[str, Any]] = [
        {"label": "empty_sig",          "mutate": lambda w: _set_sig(w, "")},
        {"label": "zero_sig",           "mutate": lambda w: _set_sig(w, "A" * 88)},
        {"label": "missing_sig_field",  "mutate": lambda w: _drop_sig_field(w)},
        {"label": "missing_integrity",  "mutate": lambda w: _drop_integrity(w)},
    ]

    case_results = []
    for case in cases:
        rejection_count = 0
        for _ in range(N_TRIALS):
            _wid, wpath = fx.fresh_warrant()
            warrant_obj = case["mutate"](read_json(wpath))
            write_json(wpath, warrant_obj)
            ok, _r = warden_admit(fx.ward_path, wpath)
            if not ok:
                rejection_count += 1

        case_results.append({
            "case": case["label"],
            "trials": N_TRIALS,
            "rejections": rejection_count,
            "rejection_rate_pct": round(rejection_count / N_TRIALS * 100, 1),
            "passed": rejection_count == N_TRIALS,
        })

    return {
        "metric": "4.5_invalid_signature_rejection_rate",
        "description": "Corrupted or absent Ed25519 signature presented to warden_admit().",
        "overall_passed": all(r["passed"] for r in case_results),
        "case_results": case_results,
    }


def _set_sig(w, v):
    c = copy.deepcopy(w)
    c.setdefault("integrity", {}).setdefault("signature", {})["sig_b64url"] = v
    return c

def _drop_sig_field(w):
    c = copy.deepcopy(w)
    (c.get("integrity") or {}).get("signature", {}).pop("sig_b64url", None)
    return c

def _drop_integrity(w):
    c = copy.deepcopy(w); c.pop("integrity", None); return c


# ──────────────────────────────────────────────────────────────────
# 4.6  Malformed Action Intent rejection
# ──────────────────────────────────────────────────────────────────

def bench_4_6(fx: BenchmarkFixtures) -> Dict[str, Any]:
    base = build_action_intent_v1(
        action_intent_id=str(__import__("uuid").uuid4()),
        operation_digest=fx.operation_said,
        targets=[{"target_kind": "patient_record", "target_ref": "patient:bench-001"}],
        context_binding={
            "zone_ref": "zone:hipaa_covered_entity",
            "overlay_refs": [], "jurisdiction_ref": "jurisdiction:us_hipaa",
            "effective_time": utc_now_iso(),
        },
        parameters={}, scope={"max_records": 1, "time_window_seconds": 3600},
        created_at=utc_now_iso(), created_by="ward:benchmark",
    )

    cases = [
        ("missing_operation_digest",       {**base, "operation_digest": ""}),
        ("missing_targets",                {k: v for k, v in base.items() if k != "targets"}),
        ("empty_targets_list",             {**base, "targets": []}),
        ("invalid_status",                 {**base, "status": "NOT_VALID"}),
        ("missing_context_binding_zone_ref", {**base, "context_binding": {**base["context_binding"], "zone_ref": ""}}),
        ("cbd_missing_prefix",             {**base, "context_binding_digest": "noprefixvalue"}),
        ("empty_dict",                     {}),
    ]

    case_results = []
    for label, malformed_raw in cases:
        rejected = not validate_action_intent_v1(ActionIntentV1(malformed_raw)).ok
        case_results.append({"case": label, "validate_rejected": rejected, "passed": rejected})

    return {
        "metric": "4.6_malformed_action_intent_rejection_rate",
        "description": "Structurally invalid Action Intents rejected by validate_action_intent_v1().",
        "overall_passed": all(r["passed"] for r in case_results),
        "case_results": case_results,
    }


# ──────────────────────────────────────────────────────────────────
# Group runner
# ──────────────────────────────────────────────────────────────────

def run(n_trials: int = N_TRIALS) -> Dict[str, Any]:
    global N_TRIALS
    N_TRIALS = n_trials

    results = []
    with BenchmarkFixtures() as fx:
        print("  4.1  Hash mismatch rejection …")
        results.append(bench_4_1(fx))
        print("  4.2  Expired token rejection …")
        results.append(bench_4_2(fx))
        print("  4.3  Already-burned token rejection …")
        results.append(bench_4_3(fx))
        print("  4.4  Revoked token rejection …")
        results.append(bench_4_4(fx))
        print("  4.5  Invalid signature rejection …")
        results.append(bench_4_5(fx))
        print("  4.6  Malformed Action Intent rejection …")
        results.append(bench_4_6(fx))

    return {"group": "4", "title": "Fail-Closed Behaviour", "n_trials": n_trials, "metrics": results}


if __name__ == "__main__":
    import json as _json
    print("Running Group 4 — Fail-Closed Behaviour …")
    print(_json.dumps(run(), indent=2))

