# benchmarks/group1_admission_latency.py  (updated — 1.5 measures ML-DSA)
"""
Group 1 — Admission Latency  (updated for G-3)

Change from previous version:
  1.5  Now measures ML-DSA sign time separately (when dilithium-py is installed)
       so the three-way split is: mint_full / ed25519_sign / mldsa_sign /
       mint_approx_without_either_sig.
       Falls back to the original two-way split if dilithium-py is absent.
"""
from __future__ import annotations

import copy
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from benchmarks.harness import (
    BenchmarkFixtures, timed_ms, stats, utc_now_iso, write_json, read_json,
)
from src.digests import canonical_json_bytes, sha256_b64url_nopad, canonical_digest_sha256
from src.action_intent import (
    canonical_action_intent_digest_payload_v1,
    canonical_action_intent_digest_v1,
    validate_action_intent_v1, ActionIntentV1,
)
from src.correspondence_form import (
    build_correspondence_form_v1, validate_correspondence_form_v1, CorrespondenceFormV1,
    canonical_correspondence_digest_v1,
)
from src.authority_policy import AuthorityPolicyV1, validate_authority_policy_v1
from src.warrant_mint import mint_warrant
from src.warden_plane import warden_check, warden_admit, build_execution_receipt

N_ITER = 1000


# ─── 1.1  Action Intent canonicalization ────────────────────────

def bench_1_1(fx):
    raw = fx.action_intent_raw
    def fn():
        payload = canonical_action_intent_digest_payload_v1(raw)
        canonical_json_bytes(payload)
    timings = timed_ms(fn, N_ITER)
    return {
        "metric": "1.1_action_intent_canonicalization",
        "description": "canonical_action_intent_digest_payload_v1() + canonical_json_bytes() — pre-image only.",
        **stats(timings),
    }


# ─── 1.2  SHA-256 digest ─────────────────────────────────────────

def bench_1_2(fx):
    payload = canonical_action_intent_digest_payload_v1(fx.action_intent_raw)
    canonical_bytes = canonical_json_bytes(payload)
    def fn():
        sha256_b64url_nopad(canonical_bytes)
    timings = timed_ms(fn, N_ITER)
    return {
        "metric": "1.2_sha256_digest_computation",
        "description": "sha256_b64url_nopad(pre-computed canonical bytes) — hash cost only.",
        **stats(timings),
    }


# ─── 1.3  Correspondence Form build + validate ───────────────────

def bench_1_3(fx):
    manifest = read_json(fx.ward_path / "manifest.json")
    ward_ref = manifest["ward_ref"]
    def fn():
        cid = str(uuid.uuid4())
        built = build_correspondence_form_v1(
            correspondence_id=cid, ward_ref=ward_ref,
            role_id=fx.role_said, persona_id=fx.persona_said,
            action_intent_id=fx.action_intent_id,
            created_at=utc_now_iso(), created_by="ward:benchmark", status="active",
        )
        validate_correspondence_form_v1(CorrespondenceFormV1(built))
    timings = timed_ms(fn, N_ITER)
    return {
        "metric": "1.3_correspondence_form_build_and_validate",
        "description": "build_correspondence_form_v1() + validate_correspondence_form_v1().",
        **stats(timings),
    }


# ─── 1.4  Authority Policy evaluation ───────────────────────────

def _eval_policy(policy_raw, intent):
    policy = AuthorityPolicyV1(policy_raw)
    vr = validate_authority_policy_v1(policy, strict_said=False)
    if not vr.ok:
        return False
    scope = policy.raw.get("scope") or {}
    role_refs = scope.get("role_refs") or []
    persona_refs = scope.get("persona_refs") or []
    op_refs = scope.get("operation_refs") or []
    return (
        (not role_refs or intent.raw.get("role_id", "") in role_refs) and
        (not persona_refs or intent.raw.get("persona_id", "") in persona_refs) and
        (not op_refs or intent.operation_digest in op_refs)
    )

def bench_1_4(fx):
    intent = fx.action_intent_obj
    t_simple  = timed_ms(lambda: _eval_policy(fx.policy_simple_raw,  intent), N_ITER)
    t_complex = timed_ms(lambda: _eval_policy(fx.policy_complex_raw, intent), N_ITER)
    return [
        {"metric": "1.4a_authority_policy_eval_simple",  **stats(t_simple),
         "description": "validate_authority_policy_v1() + scope cross-check (simple policy)."},
        {"metric": "1.4b_authority_policy_eval_complex", **stats(t_complex),
         "description": "validate_authority_policy_v1() + scope cross-check (complex policy, 3 predicates)."},
    ]


# ─── 1.5  Warrant mint — now with ML-DSA split ──────────────────

def bench_1_5(fx: BenchmarkFixtures) -> List[Dict[str, Any]]:
    """
    Measures:
      a) mint_full        — full mint_warrant() including all implemented signatures
      b) ed25519_sign     — Ed25519 sign in isolation
      c) mldsa_sign       — ML-DSA sign in isolation (if dilithium-py installed)
      d) mint_approx_no_sigs — mean(mint_full) minus mean of all measured sig steps
    """
    # ── Full mint ────────────────────────────────────────────────
    def fn_mint():
        mint_warrant(
            ward_path=fx.ward_path,
            correspondence_id=fx.correspondence_id,
            ttl_seconds=600,
            validate_operation_registry=False,
        )
    timings_mint = timed_ms(fn_mint, N_ITER)

    # ── Ed25519 sign ─────────────────────────────────────────────
    from cryptography.hazmat.primitives import serialization
    key_path = fx.ward_path / "keys" / "ward_ed25519_private.pem"
    ed_private = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    sample_msg = canonical_json_bytes(fx.action_intent_raw)

    def fn_ed25519():
        ed_private.sign(sample_msg)
    timings_ed = timed_ms(fn_ed25519, N_ITER)

    # ── ML-DSA sign (conditional) ────────────────────────────────
    mldsa_available = False
    timings_mldsa: Optional[List[float]] = None
    mldsa_note = "No ML-DSA backend — install liboqs-python or dilithium-py"
    mldsa_backend = "none"

    try:
        from src.mldsa_signer import (
            _backend, _keygen, _sign, backend_name, MLDSANotAvailable, ensure_mldsa_keypair
        )
        backend = _backend()
        mldsa_backend = backend_name()

        # Use ward keypair if available (consistent with mint path), else generate fresh
        try:
            ml_pk, ml_sk = ensure_mldsa_keypair(fx.ward_path)
        except Exception:
            ml_pk, ml_sk = _keygen()

        def fn_mldsa():
            _sign(ml_sk, sample_msg)

        timings_mldsa = timed_ms(fn_mldsa, N_ITER)
        mldsa_available = True
        mldsa_note = f"Measured via {mldsa_backend}."
    except Exception:
        pass

    # ── Approx mint without signatures ───────────────────────────
    mean_mint = stats(timings_mint)["mean_ms"]
    mean_ed   = stats(timings_ed)["mean_ms"]
    mean_mldsa = stats(timings_mldsa)["mean_ms"] if timings_mldsa else 0.0
    approx_no_sigs = round(mean_mint - mean_ed - mean_mldsa, 4)

    results = [
        {
            "metric": "1.5_warrant_mint_full",
            "description": (
                "mint_warrant() end-to-end including all implemented signatures. "
                f"ML-DSA: {'included' if mldsa_available else 'absent — dilithium-py not installed'}."
            ),
            **stats(timings_mint),
        },
        {
            "metric": "1.5_ed25519_sign_only",
            "description": "Ed25519 private_key.sign(canonical_bytes) in isolation.",
            **stats(timings_ed),
        },
    ]

    if timings_mldsa:
        results.append({
            "metric": "1.5_mldsa_sign_only",
            "description": "ML-DSA (Dilithium3) sign in isolation — G-3 closed.",
            **stats(timings_mldsa),
        })
    else:
        results.append({
            "metric": "1.5_mldsa_sign_only",
            "description": "ML-DSA sign — NOT measured (dilithium-py absent).",
            "gap": mldsa_note,
        })

    results.append({
        "metric": "1.5_mint_approx_without_signatures",
        "description": (
            "Approximation: mean(mint_full) − mean(ed25519) − mean(mldsa). "
            f"ML-DSA {'measured' if mldsa_available else 'not available (treated as 0)'}."
        ),
        "mean_ms": approx_no_sigs,
        "mldsa_available": mldsa_available,
    })

    return results


# ─── 1.6  Preflight ──────────────────────────────────────────────

def bench_1_6(fx):
    _, warrant_path = fx.fresh_warrant()
    warrant_obj = read_json(warrant_path)
    def fn(): warden_check(fx.ward_path, warrant_obj)
    timings = timed_ms(fn, N_ITER)
    return {
        "metric": "1.6_preflight_warden_check",
        "description": "warden_check() — non-authorising preflight; same warrant dict reused.",
        **stats(timings),
    }


# ─── 1.7  Execution-boundary admission ──────────────────────────

def bench_1_7(fx):
    warrants = fx.premint_warrants(N_ITER)
    timings: List[float] = []
    for (_wid, wpath) in warrants:
        t0 = time.perf_counter()
        warden_admit(fx.ward_path, wpath)
        timings.append((time.perf_counter() - t0) * 1_000.0)
    return {
        "metric": "1.7_execution_boundary_admission",
        "description": "warden_admit() — signature verify + atomic burn + ADO production.",
        **stats(timings),
    }


# ─── 1.8  Evidence Registry commit ──────────────────────────────

def bench_1_8(fx):
    receipts_dir = fx.ward_path / "receipts"

    def _stub(): return build_execution_receipt("sha256:bench", "sha256:bench", "bench.burn")

    def fn_b():
        bid = str(uuid.uuid4())
        p = fx.ward_path / "warrants_burned" / f"{bid}.burn"
        p.write_text(json.dumps({"warrant_id": bid, "burned_at": utc_now_iso()}), encoding="utf-8")

    def fn_er():
        r = _stub(); write_json(receipts_dir / f"{r['receipt_id']}.json", r)

    def fn_rs():
        rid = str(uuid.uuid4())
        write_json(receipts_dir / f"{rid}.state.json",
                   {"receipt_id": rid, "state": "committed", "committed_at": utc_now_iso(),
                    "chain": {"warrant_hash": "bench", "receipt_hash": canonical_digest_sha256({"id": rid})}})

    def fn_compound():
        bid = str(uuid.uuid4())
        (fx.ward_path / "warrants_burned" / f"{bid}.burn").write_text(
            json.dumps({"warrant_id": bid, "burned_at": utc_now_iso()}), encoding="utf-8")
        r = _stub(); r["warrant_sha256"] = bid
        write_json(receipts_dir / f"{r['receipt_id']}.json", r)
        write_json(receipts_dir / f"{r['receipt_id']}.state.json",
                   {"receipt_id": r["receipt_id"], "state": "committed", "committed_at": utc_now_iso(),
                    "chain": {"warrant_hash": bid, "receipt_hash": canonical_digest_sha256(r)}})

    return [
        {"metric": "1.8a_evr_burn_record_commit",      **stats(timed_ms(fn_b,        N_ITER)),
         "description": "Write Burn Record (B)."},
        {"metric": "1.8b_evr_execution_receipt_commit", **stats(timed_ms(fn_er,       N_ITER)),
         "description": "Write Execution Receipt (ER)."},
        {"metric": "1.8c_evr_receipt_state_commit",     **stats(timed_ms(fn_rs,       N_ITER)),
         "description": "Write Receipt State (RS)."},
        {"metric": "1.8d_evr_compound_commit",          **stats(timed_ms(fn_compound, N_ITER)),
         "description": "Compound: B + ER + RS."},
    ]


# ─── 1.9  End-to-end ────────────────────────────────────────────

def bench_1_9(fx, component_means):
    receipts_dir = fx.ward_path / "receipts"

    def fn_e2e():
        payload = canonical_action_intent_digest_payload_v1(fx.action_intent_raw)
        canonical_json_bytes(payload)
        w = mint_warrant(ward_path=fx.ward_path, correspondence_id=fx.correspondence_id,
                         ttl_seconds=600, validate_operation_registry=False)
        warden_check(fx.ward_path, read_json(w.warrant_path))
        ok, result = warden_admit(fx.ward_path, w.warrant_path)
        if ok and result.get("receipt_id"):
            rid = result["receipt_id"]
            write_json(receipts_dir / f"{rid}.state.json",
                       {"receipt_id": rid, "state": "committed", "committed_at": utc_now_iso(),
                        "chain": {"warrant_hash": result.get("warrant_hash", ""),
                                  "receipt_hash": canonical_digest_sha256(result.get("execution_receipt") or {})}})

    timings = timed_ms(fn_e2e, N_ITER)
    s = stats(timings)
    sum_comp = round(
        component_means.get("1.1_mean_ms", 0) + component_means.get("1.5_mean_ms", 0) +
        component_means.get("1.6_mean_ms", 0) + component_means.get("1.7_mean_ms", 0) +
        component_means.get("1.8d_mean_ms", 0), 4)
    return {
        "metric": "1.9_end_to_end_admission_cycle",
        "description": "Wall-clock L_total = L_eval + L_mint + L_burn + L_evr.",
        **s,
        "sum_of_components_mean_ms": sum_comp,
        "overhead_mean_ms": round(s["mean_ms"] - sum_comp, 4),
    }


# ─── Group runner ────────────────────────────────────────────────

def run(n_iter: int = N_ITER) -> Dict[str, Any]:
    global N_ITER
    N_ITER = n_iter
    results = []

    with BenchmarkFixtures() as fx:
        print("  1.1  Action Intent canonicalization …")
        r_1_1 = bench_1_1(fx);   results.append(r_1_1)
        print("  1.2  SHA-256 digest computation …")
        r_1_2 = bench_1_2(fx);   results.append(r_1_2)
        print("  1.3  Correspondence Form build + validate …")
        r_1_3 = bench_1_3(fx);   results.append(r_1_3)
        print("  1.4  Authority Policy evaluation …")
        r_1_4 = bench_1_4(fx);   results.extend(r_1_4)
        print("  1.5  Warrant mint (+ ML-DSA if available) …")
        r_1_5 = bench_1_5(fx);   results.extend(r_1_5)
        print("  1.6  Preflight warden_check …")
        r_1_6 = bench_1_6(fx);   results.append(r_1_6)
        print("  1.7  Execution-boundary admission …")
        r_1_7 = bench_1_7(fx);   results.append(r_1_7)
        print("  1.8  Evidence Registry commit …")
        r_1_8 = bench_1_8(fx);   results.extend(r_1_8)
        print("  1.9  End-to-end admission cycle …")
        comp = {
            "1.1_mean_ms":  r_1_1["mean_ms"],
            "1.5_mean_ms":  r_1_5[0]["mean_ms"],
            "1.6_mean_ms":  r_1_6["mean_ms"],
            "1.7_mean_ms":  r_1_7["mean_ms"],
            "1.8d_mean_ms": r_1_8[3]["mean_ms"],
        }
        results.append(bench_1_9(fx, comp))

    return {"group": "1", "title": "Admission Latency", "n_iter": n_iter, "metrics": results}


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(run(), indent=2))

