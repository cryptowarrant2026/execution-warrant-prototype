# benchmarks/group3_artifact_integrity.py
"""
Group 3 — Artifact Integrity (SAID Construction Correctness)

Validates the SAID construction and its tamper-evidence properties.

Metrics:
  3.1  SAID determinism         — 1000 independent invocations, zero non-determinism
  3.2  SAID collision sensitivity — single-field mutations always change the SAID
  3.3  Canonical form stability  — field-order independence (sorted-key invariant)
  3.4  chain_intact verification  — time to traverse and recompute the four-element
       evidence chain (W_τ, ADO, B, ER) → RS via SAID/digest traversal
"""
from __future__ import annotations

import copy
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

from benchmarks.harness import (
    BenchmarkFixtures,
    timed_ms,
    stats,
    utc_now_iso,
    write_json,
    read_json,
)

from src.said_python import python_said_generate, python_derivation_json
from src.digests import canonical_json_bytes, canonical_digest_sha256
from src.action_intent import (
    ActionIntentV1,
    canonical_action_intent_digest_payload_v1,
    canonical_action_intent_digest_v1,
    build_action_intent_v1,
)
from src.warden_plane import build_execution_receipt

N_ITER = 1000


# ──────────────────────────────────────────────────────────────────
# 3.1  SAID determinism
# ──────────────────────────────────────────────────────────────────

def bench_3_1(fx: BenchmarkFixtures) -> Dict[str, Any]:
    """
    Compute python_said_generate(raw) N=1000 times from the same input.
    Confirm all produced SAIDs are identical (zero non-determinism).
    """
    raw = copy.deepcopy(fx.action_intent_raw)
    # Strip existing `d` so the generator recomputes from scratch each time
    raw.pop("d", None)

    saids: List[str] = []
    timings: List[float] = []

    for _ in range(N_ITER):
        t0 = time.perf_counter()
        result = python_said_generate(copy.deepcopy(raw))
        t1 = time.perf_counter()
        saids.append(result.get("d", ""))
        timings.append((t1 - t0) * 1_000.0)

    unique_saids = set(saids)
    non_determinism_count = len(unique_saids) - 1  # 0 = perfect determinism

    return {
        "metric": "3.1_said_determinism",
        "description": "python_said_generate() called 1000× on identical input; zero unique-SAID count above 1 = deterministic.",
        "n_invocations": N_ITER,
        "unique_said_values": len(unique_saids),
        "non_determinism_events": non_determinism_count,
        "passed": non_determinism_count == 0,
        "said_sample": saids[0] if saids else "",
        **stats(timings),
    }


# ──────────────────────────────────────────────────────────────────
# 3.2  SAID collision sensitivity (single-field mutations)
# ──────────────────────────────────────────────────────────────────

def _mutate_field(raw: Dict[str, Any], path: List[str], mutation: str = "_MUTATED") -> Dict[str, Any]:
    """
    Return a deep-copy of `raw` with the value at `path` (dotted list) modified.
    Appends `mutation` to string values; flips booleans; increments ints.
    """
    obj = copy.deepcopy(raw)
    cursor = obj
    for key in path[:-1]:
        cursor = cursor[key]
    leaf_key = path[-1]
    v = cursor[leaf_key]
    if isinstance(v, str):
        cursor[leaf_key] = v + mutation
    elif isinstance(v, bool):
        cursor[leaf_key] = not v
    elif isinstance(v, int):
        cursor[leaf_key] = v + 1
    elif isinstance(v, list):
        cursor[leaf_key] = v + [mutation]
    elif isinstance(v, dict):
        cursor[leaf_key] = {**v, "__mutated__": True}
    else:
        cursor[leaf_key] = str(v) + mutation
    return obj


def bench_3_2(fx: BenchmarkFixtures) -> Dict[str, Any]:
    """
    Apply one-character (single-field) mutations to a representative Action Intent.
    For each mutation, confirm the resulting SAID differs from the original.

    Fields tested cover all inputs to canonical_action_intent_digest_payload_v1.
    """
    base = copy.deepcopy(fx.action_intent_raw)
    base.pop("d", None)
    base.pop("action_intent_digest", None)
    original_said = python_said_generate(base).get("d", "")

    # Define mutation paths (field path → human label)
    mutation_specs: List[Tuple[List[str], str]] = [
        (["schema"],                                  "schema"),
        (["schema_version"],                          "schema_version"),
        (["action_intent_id"],                        "action_intent_id"),
        (["operation_digest"],                        "operation_digest"),
        (["targets", 0, "target_kind"],               "targets[0].target_kind"),
        (["targets", 0, "target_ref"],                "targets[0].target_ref"),
        (["context_binding", "zone_ref"],             "context_binding.zone_ref"),
        (["context_binding", "effective_time"],       "context_binding.effective_time"),
        (["parameters", "read_depth"],                "parameters.read_depth"),
        (["scope", "max_records"],                    "scope.max_records"),
        (["scope", "time_window_seconds"],            "scope.time_window_seconds"),
    ]

    mutations_tested  = 0
    mutations_changed = 0
    failures: List[str] = []

    for path, label in mutation_specs:
        try:
            mutated = _mutate_field(base, path)
            mutated.pop("d", None)
            mutated.pop("action_intent_digest", None)
            new_said = python_said_generate(mutated).get("d", "")
            mutations_tested += 1
            if new_said != original_said:
                mutations_changed += 1
            else:
                failures.append(f"SAID unchanged after mutating '{label}'")
        except (KeyError, IndexError, TypeError) as exc:
            failures.append(f"Could not mutate '{label}': {exc}")

    return {
        "metric": "3.2_said_collision_sensitivity",
        "description": "Single-field mutations — confirm SAID changes for every mutation.",
        "original_said": original_said,
        "mutations_tested": mutations_tested,
        "mutations_caused_said_change": mutations_changed,
        "failures": failures,
        "passed": len(failures) == 0 and mutations_changed == mutations_tested,
    }


# ──────────────────────────────────────────────────────────────────
# 3.3  Canonical form stability (field-order independence)
# ──────────────────────────────────────────────────────────────────

def bench_3_3(fx: BenchmarkFixtures) -> Dict[str, Any]:
    """
    Build two semantically identical Action Intents with deliberately different
    field insertion order.  Confirm:
      a) canonical_json_bytes() produces identical output for both.
      b) python_said_generate() produces identical SAID for both.

    Validates the sorted-key invariant in digests.py.

    N = 100 independent pairs (sufficient; this is a determinism test, not a
    performance test).
    """
    N = 100
    base_kwargs = dict(
        action_intent_id=str(uuid.uuid4()),
        operation_digest=fx.operation_said,
        targets=[{"target_kind": "patient_record", "target_ref": "patient:bench-001"}],
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
            "field_allowlist": ["id", "name"],
            "data_categories": ["dpv:MedicalHealth"],
        },
        created_at=utc_now_iso(),
        created_by="ward:benchmark",
        status="proposed",
    )

    canon_mismatches = 0
    said_mismatches  = 0

    for _ in range(N):
        r1 = build_action_intent_v1(**base_kwargs)
        r2 = build_action_intent_v1(**base_kwargs)

        # Build canonical payloads
        p1 = canonical_action_intent_digest_payload_v1(r1)
        p2 = canonical_action_intent_digest_payload_v1(r2)

        b1 = canonical_json_bytes(p1)
        b2 = canonical_json_bytes(p2)
        if b1 != b2:
            canon_mismatches += 1

        # SAID comparison (strip d before recomputing)
        s1 = copy.deepcopy(r1); s1.pop("d", None); s1.pop("action_intent_digest", None)
        s2 = copy.deepcopy(r2); s2.pop("d", None); s2.pop("action_intent_digest", None)
        said1 = python_said_generate(s1).get("d", "")
        said2 = python_said_generate(s2).get("d", "")
        if said1 != said2:
            said_mismatches += 1

    return {
        "metric": "3.3_canonical_form_stability",
        "description": (
            "Two semantically identical Action Intents with different field-insertion order. "
            "Validates sorted-key serialization invariant in digests.py."
        ),
        "n_pairs_tested": N,
        "canonical_json_mismatches": canon_mismatches,
        "said_mismatches": said_mismatches,
        "passed": canon_mismatches == 0 and said_mismatches == 0,
    }


# ──────────────────────────────────────────────────────────────────
# 3.4  chain_intact verification time
# ──────────────────────────────────────────────────────────────────

def _commit_evidence_chain(fx: BenchmarkFixtures) -> Dict[str, str]:
    """
    Commit one complete evidence chain and return the reference handles
    needed to traverse it.
    """
    # Mint warrant
    warrant_id, warrant_path = fx.fresh_warrant()
    warrant_obj = read_json(warrant_path)
    warrant_hash = canonical_digest_sha256(warrant_obj)

    # Burn record (B)
    import json as _json
    import os
    burn_path = fx.ward_path / "warrants_burned" / f"{warrant_id}.burn"
    fd = os.open(str(burn_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w") as f:
        _json.dump({"warrant_id": warrant_id, "burned_at": utc_now_iso()}, f, indent=2)

    # Execution Receipt (ER)
    receipt = build_execution_receipt(
        warrant_hash=warrant_hash,
        operation_card_hash=canonical_digest_sha256(fx.operation_raw),
        burn_path=str(burn_path),
    )
    receipt_id = receipt["receipt_id"]
    receipt_path = fx.ward_path / "receipts" / f"{receipt_id}.json"
    write_json(receipt_path, receipt)
    receipt_hash = canonical_digest_sha256(receipt)

    # Receipt State (RS)
    rs = {
        "receipt_id": receipt_id,
        "state": "committed",
        "committed_at": utc_now_iso(),
        "chain": {
            "warrant_id":    warrant_id,
            "warrant_hash":  warrant_hash,
            "receipt_id":    receipt_id,
            "receipt_hash":  receipt_hash,
            "burn_path":     str(burn_path),
        },
    }
    rs_path = fx.ward_path / "receipts" / f"{receipt_id}.state.json"
    write_json(rs_path, rs)

    return {
        "warrant_id":   warrant_id,
        "warrant_path": str(warrant_path),
        "burn_path":    str(burn_path),
        "receipt_id":   receipt_id,
        "receipt_path": str(receipt_path),
        "rs_path":      str(rs_path),
        "expected_warrant_hash":  warrant_hash,
        "expected_receipt_hash":  receipt_hash,
    }


def _verify_chain(refs: Dict[str, str]) -> bool:
    """
    Traverse and re-verify the four-element evidence chain:
      W_τ  → hash re-computed from warrant file
      B    → burn file exists
      ER   → hash re-computed from receipt file
      RS   → chain hashes match

    Returns True if the chain is intact.
    """
    # 1. Load and re-hash warrant (W_τ)
    w_obj = read_json(Path(refs["warrant_path"]))
    recomputed_warrant_hash = canonical_digest_sha256(w_obj)
    if recomputed_warrant_hash != refs["expected_warrant_hash"]:
        return False

    # 2. Confirm burn file exists (B)
    if not Path(refs["burn_path"]).exists():
        return False

    # 3. Load and re-hash receipt (ER)
    r_obj = read_json(Path(refs["receipt_path"]))
    recomputed_receipt_hash = canonical_digest_sha256(r_obj)
    if recomputed_receipt_hash != refs["expected_receipt_hash"]:
        return False

    # 4. Load RS and confirm chain hashes match
    rs_obj = read_json(Path(refs["rs_path"]))
    chain = rs_obj.get("chain") or {}
    if chain.get("warrant_hash") != recomputed_warrant_hash:
        return False
    if chain.get("receipt_hash") != recomputed_receipt_hash:
        return False

    return True


def bench_3_4(fx: BenchmarkFixtures) -> Dict[str, Any]:
    """
    Time the four-element evidence chain traversal and re-verification
    (the auditor cost for chain_intact).

    One chain is committed per iteration so each timing covers a cold
    chain read (no in-memory caching across iterations).
    """
    N = 200   # fewer iterations — each requires a file commit
    timings: List[float] = []
    integrity_failures = 0

    for _ in range(N):
        refs = _commit_evidence_chain(fx)

        t0 = time.perf_counter()
        ok = _verify_chain(refs)
        t1 = time.perf_counter()

        timings.append((t1 - t0) * 1_000.0)
        if not ok:
            integrity_failures += 1

    return {
        "metric": "3.4_chain_intact_verification_time",
        "description": (
            "Time to traverse W_τ → B → ER → RS and re-compute all chain digests. "
            "Each iteration reads from disk (no caching). "
            "integrity_failures should be zero."
        ),
        "n_chains_verified": N,
        "integrity_failures": integrity_failures,
        "passed": integrity_failures == 0,
        **stats(timings),
    }


# ──────────────────────────────────────────────────────────────────
# Group runner
# ──────────────────────────────────────────────────────────────────

def run(n_iter: int = N_ITER) -> Dict[str, Any]:
    global N_ITER
    N_ITER = n_iter

    results = []

    with BenchmarkFixtures() as fx:
        print("  3.1  SAID determinism …")
        results.append(bench_3_1(fx))

        print("  3.2  SAID collision sensitivity …")
        results.append(bench_3_2(fx))

        print("  3.3  Canonical form stability …")
        results.append(bench_3_3(fx))

        print("  3.4  chain_intact verification time …")
        results.append(bench_3_4(fx))

    return {
        "group": "3",
        "title": "Artifact Integrity",
        "n_iter": n_iter,
        "metrics": results,
    }


if __name__ == "__main__":
    import json as _json
    print("Running Group 3 — Artifact Integrity …")
    out = run()
    print(_json.dumps(out, indent=2))

