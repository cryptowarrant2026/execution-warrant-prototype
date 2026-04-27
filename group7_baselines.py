# benchmarks/group7_baselines.py
"""
Group 7 — Comparison Baselines (Table 5 additional rows)

Measures mint and verify latency for two baseline credential schemes:

  7.1  HMAC-SHA256 Bearer Token
       The minimal single-use credential: a JSON payload HMAC'd with a
       shared secret.  No revocation, no binding, no single-use enforcement
       in the token itself.  Represents the lower-bound cost of a token that
       makes no containment guarantees.

  7.2  Macaroon (pymacaroons)
       A bearer token with first-party caveats (contextual restrictions) and
       third-party caveats (delegated attenuation).  Supports offline
       verification and attenuation but does not natively provide:
         - cryptographic binding to a specific operation/persona/role
         - atomic single-use enforcement
         - post-quantum signing
       Represents the state-of-the-art in attenuable bearer tokens.

  7.3  RBC Warrant (reference figures from Group 1)
       The full RBC admission cycle with dual-algorithm signing.
       Figures are taken directly from Group 1 measurements rather than
       re-measured, so that Table 5 rows are internally consistent.

Fairness notes (report these alongside results)
────────────────────────────────────────────────
  - HMAC and Macaroon mint/verify costs are cryptographic only.
    They do not include the equivalent of RBC's Correspondence Form
    resolution, Action Intent validation, or EvR commit — operations
    that the RBC protocol requires for its containment guarantees but
    that have no analogue in a plain bearer-token architecture.

  - A fair like-for-like comparison therefore has two components:
      a) Cryptographic-only: compare HMAC/Macaroon sign+verify against
         the RBC Ed25519+ML-DSA sign+verify figures (0.205 ms mint,
         0.188 ms verify) — this isolates the cryptographic overhead
         of the containment guarantee.
      b) Full admission cycle: compare HMAC/Macaroon against RBC
         L_total (2.207 ms) — this shows the total protocol cost
         including the containment machinery.

  - Both comparisons are reported in the output so Table 5 authors
    can choose the framing appropriate to the paper's claims.

Installation
────────────
  pip install pymacaroons

  pymacaroons is optional — if absent, metric 7.2 is reported as a
  gap with an install note, consistent with Group 4 metric 4.4.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from benchmarks.harness import timed_ms, stats

# ── RBC Group 1 reference figures (from run 20260407T073745Z) ─────
RBC_REFERENCE = {
    "run_id":           "20260407T073745Z",
    "platform":         "Apple Silicon arm64, macOS 26.3, Python 3.14.2",
    "ed25519_sign_ms":  0.087,
    "mldsa_sign_ms":    0.118,
    "combined_sign_ms": 0.205,   # Ed25519 + ML-DSA at mint
    "combined_verify_ms": 0.188, # Ed25519 + ML-DSA at verify (estimated)
    "mint_full_mean_ms":  0.935,
    "mint_full_p99_ms":   2.301,
    "l_total_mean_ms":    2.207,
    "l_total_p99_ms":     2.627,
    "l_total_projected_with_sigs_ms": 2.540,
    "note": (
        "Figures taken directly from Group 1 (run 20260407T073745Z). "
        "l_total includes Correspondence Form resolution, Action Intent "
        "validation, warden_check, warden_admit, and EvR compound commit. "
        "combined_verify_ms is estimated from isolated Ed25519 verify "
        "benchmark plus ML-DSA verify (Group 6 measured)."
    ),
}

N_ITER = 1000


# ──────────────────────────────────────────────────────────────────
# 7.1  HMAC-SHA256 Bearer Token
# ──────────────────────────────────────────────────────────────────

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _hmac_sign(payload: bytes, secret: bytes) -> str:
    """HMAC-SHA256 over canonical payload bytes, returned as hex."""
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def _hmac_verify(payload: bytes, secret: bytes, tag: str) -> bool:
    """Constant-time HMAC verification."""
    expected = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, tag)


def _build_hmac_token(secret: bytes, subject: str, ttl_seconds: int = 600) -> Dict[str, Any]:
    """
    Minimal HMAC-SHA256 bearer token.
    Payload: {jti, sub, iat, exp} serialised as canonical JSON.
    """
    now = int(time.time())
    payload_obj = {
        "jti": str(uuid.uuid4()),
        "sub": subject,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    payload_bytes = json.dumps(payload_obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    tag = _hmac_sign(payload_bytes, secret)
    return {"payload": payload_obj, "hmac_sha256": tag}


def _verify_hmac_token(token: Dict[str, Any], secret: bytes) -> bool:
    payload_obj  = token["payload"]
    tag          = token["hmac_sha256"]
    payload_bytes = json.dumps(payload_obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if not _hmac_verify(payload_bytes, secret, tag):
        return False
    now = int(time.time())
    return payload_obj.get("exp", 0) > now


def bench_7_1() -> List[Dict[str, Any]]:
    """
    7.1a  HMAC-SHA256 token mint  (sign only)
    7.1b  HMAC-SHA256 token verify
    7.1c  HMAC-SHA256 mint + verify round-trip
    """
    secret  = os.urandom(32)
    subject = "benchmark:subject"

    # Pre-build a token for the verify-only timing
    pre_built = _build_hmac_token(secret, subject)

    def fn_mint():
        _build_hmac_token(secret, subject)

    def fn_verify():
        _verify_hmac_token(pre_built, secret)

    def fn_roundtrip():
        tok = _build_hmac_token(secret, subject)
        _verify_hmac_token(tok, secret)

    t_mint      = timed_ms(fn_mint,      N_ITER)
    t_verify    = timed_ms(fn_verify,    N_ITER)
    t_roundtrip = timed_ms(fn_roundtrip, N_ITER)

    return [
        {
            "metric":      "7.1a_hmac_sha256_mint",
            "description": "HMAC-SHA256 bearer token mint (payload construction + HMAC sign). No binding, no single-use enforcement.",
            "scheme":      "HMAC-SHA256 Bearer Token",
            **stats(t_mint),
        },
        {
            "metric":      "7.1b_hmac_sha256_verify",
            "description": "HMAC-SHA256 bearer token verify (HMAC recompute + constant-time compare + expiry check).",
            "scheme":      "HMAC-SHA256 Bearer Token",
            **stats(t_verify),
        },
        {
            "metric":      "7.1c_hmac_sha256_roundtrip",
            "description": "HMAC-SHA256 mint + verify round-trip.",
            "scheme":      "HMAC-SHA256 Bearer Token",
            **stats(t_roundtrip),
        },
    ]


# ──────────────────────────────────────────────────────────────────
# 7.2  Macaroon (pymacaroons)
# ──────────────────────────────────────────────────────────────────

def _check_pymacaroons() -> Optional[str]:
    """Return None if pymacaroons is available, else a gap note."""
    try:
        import pymacaroons  # type: ignore  # noqa: F401
        return None
    except ImportError:
        return (
            "pymacaroons not installed. "
            "Run: pip install pymacaroons"
        )


def bench_7_2() -> List[Dict[str, Any]]:
    """
    7.2a  Macaroon mint   — create + add 2 first-party caveats (representative use)
    7.2b  Macaroon verify — verify + discharge caveats
    7.2c  Macaroon mint + verify round-trip

    Two first-party caveats are added to represent a realistic policy
    restriction (e.g. time window + purpose binding), making the
    comparison meaningful against the RBC Action Intent context binding.
    """
    gap = _check_pymacaroons()
    if gap:
        return [{
            "metric":      "7.2_macaroon",
            "description": "Macaroon mint + verify.",
            "scheme":      "Macaroon (pymacaroons)",
            "gap":         gap,
            "passed":      None,
        }]

    import pymacaroons  # type: ignore

    location   = "https://example.com/warrants"
    identifier = "benchmark-macaroon"
    secret_key = os.urandom(32).hex()

    def _mint_macaroon():
        m = pymacaroons.Macaroon(
            location=location,
            identifier=identifier,
            key=secret_key,
        )
        m = m.add_first_party_caveat("purpose = treatment")
        m = m.add_first_party_caveat(f"time < {int(time.time()) + 600}")
        return m

    def _verify_macaroon(m):
        v = pymacaroons.Verifier()
        v.satisfy_exact("purpose = treatment")
        v.satisfy_general(lambda c: c.startswith("time < "))
        return v.verify(m, secret_key)

    # Pre-mint for verify-only timing
    pre_minted = _mint_macaroon()

    def fn_mint():
        _mint_macaroon()

    def fn_verify():
        _verify_macaroon(pre_minted)

    def fn_roundtrip():
        m = _mint_macaroon()
        _verify_macaroon(m)

    t_mint      = timed_ms(fn_mint,      N_ITER)
    t_verify    = timed_ms(fn_verify,    N_ITER)
    t_roundtrip = timed_ms(fn_roundtrip, N_ITER)

    return [
        {
            "metric":      "7.2a_macaroon_mint",
            "description": "Macaroon mint with 2 first-party caveats (purpose + time window). No single-use enforcement, no post-quantum signing.",
            "scheme":      "Macaroon (pymacaroons)",
            **stats(t_mint),
        },
        {
            "metric":      "7.2b_macaroon_verify",
            "description": "Macaroon verify + caveat discharge (2 first-party caveats).",
            "scheme":      "Macaroon (pymacaroons)",
            **stats(t_verify),
        },
        {
            "metric":      "7.2c_macaroon_roundtrip",
            "description": "Macaroon mint + verify round-trip (2 first-party caveats).",
            "scheme":      "Macaroon (pymacaroons)",
            **stats(t_roundtrip),
        },
    ]


# ──────────────────────────────────────────────────────────────────
# 7.3  RBC reference figures (from Group 1)
# ──────────────────────────────────────────────────────────────────

def bench_7_3() -> List[Dict[str, Any]]:
    """
    Reference rows for Table 5.  Not re-measured here — taken directly
    from Group 1 run 20260407T073745Z for internal consistency.
    """
    ref = RBC_REFERENCE
    return [
        {
            "metric":       "7.3a_rbc_sign_only",
            "description":  "RBC warrant: Ed25519 + ML-DSA-65 combined sign cost at mint. Directly measured in Group 1.",
            "scheme":       "RBC Warrant (Ed25519 + ML-DSA-65)",
            "mean_ms":      ref["combined_sign_ms"],
            "source":       f"Group 1, run {ref['run_id']}",
            "note":         "Cryptographic cost only. Does not include Correspondence Form resolution, AI validation, or EvR commit.",
        },
        {
            "metric":       "7.3b_rbc_verify_only",
            "description":  "RBC warrant: Ed25519 + ML-DSA-65 combined verify cost at admission. From Group 1 + Group 6 measured figures.",
            "scheme":       "RBC Warrant (Ed25519 + ML-DSA-65)",
            "mean_ms":      ref["combined_verify_ms"],
            "source":       f"Group 1 + Group 6, run {ref['run_id']}",
            "note":         "Cryptographic cost only. Does not include burn, receipt, or EvR commit.",
        },
        {
            "metric":       "7.3c_rbc_mint_full",
            "description":  "RBC warrant mint (full): Correspondence Form resolution + AI validation + dual signing + file write.",
            "scheme":       "RBC Warrant (Ed25519 + ML-DSA-65)",
            "mean_ms":      ref["mint_full_mean_ms"],
            "p99_ms":       ref["mint_full_p99_ms"],
            "source":       f"Group 1, run {ref['run_id']}",
        },
        {
            "metric":       "7.3d_rbc_l_total",
            "description":  "RBC end-to-end admission cycle: mint + preflight + admit + EvR commit.",
            "scheme":       "RBC Warrant (Ed25519 + ML-DSA-65)",
            "mean_ms":      ref["l_total_mean_ms"],
            "p99_ms":       ref["l_total_p99_ms"],
            "source":       f"Group 1, run {ref['run_id']}",
            "note":         "Full protocol cost including all containment-specific operations.",
        },
    ]


# ──────────────────────────────────────────────────────────────────
# Table 5 comparison summary
# ──────────────────────────────────────────────────────────────────

def _build_table5_summary(
    hmac_results:     List[Dict[str, Any]],
    macaroon_results: List[Dict[str, Any]],
    rbc_results:      List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Produce a structured comparison aligned to Table 5 in the paper.

    Two comparison frames are provided:
      A) Cryptographic-only: HMAC sign vs Macaroon mint vs RBC sign
      B) Full admission cycle: best-effort equivalent for each scheme
    """
    def _get(results, metric_prefix, key="mean_ms"):
        for r in results:
            if r["metric"].startswith(metric_prefix):
                return r.get(key)
        return None

    hmac_mint_ms     = _get(hmac_results,     "7.1a", "mean_ms")
    hmac_verify_ms   = _get(hmac_results,     "7.1b", "mean_ms")
    hmac_rt_ms       = _get(hmac_results,     "7.1c", "mean_ms")

    mac_mint_ms      = _get(macaroon_results, "7.2a", "mean_ms")
    mac_verify_ms    = _get(macaroon_results, "7.2b", "mean_ms")
    mac_rt_ms        = _get(macaroon_results, "7.2c", "mean_ms")

    rbc_sign_ms      = _get(rbc_results,      "7.3a", "mean_ms")
    rbc_verify_ms    = _get(rbc_results,      "7.3b", "mean_ms")
    rbc_mint_ms      = _get(rbc_results,      "7.3c", "mean_ms")
    rbc_ltotal_ms    = _get(rbc_results,      "7.3d", "mean_ms")

    def _overhead(rbc_val, baseline_val):
        if rbc_val is None or baseline_val is None:
            return None
        return round(rbc_val - baseline_val, 4)

    def _multiplier(rbc_val, baseline_val):
        if rbc_val is None or baseline_val is None:
            return None
        return round(rbc_val / baseline_val, 1)

    return {
        "title": "Table 5 — Credential Scheme Comparison",
        "platform": RBC_REFERENCE["platform"],
        "run_id":   RBC_REFERENCE["run_id"],
        "fairness_note": (
            "Frame A (cryptographic-only) isolates the signing cost and is the "
            "fairest comparison of cryptographic overhead. "
            "Frame B (full admission cycle) includes all protocol-specific "
            "operations required for RBC's containment guarantees — operations "
            "with no equivalent in plain bearer-token schemes. "
            "Both frames are reported so readers can assess the cost of the "
            "containment guarantee independently from the base credential cost."
        ),
        "frame_a_cryptographic_only": {
            "description": "Sign cost (mint) and verify cost only. No protocol overhead.",
            "rows": [
                {
                    "scheme":       "HMAC-SHA256 Bearer Token",
                    "sign_ms":      round(hmac_mint_ms,   4) if hmac_mint_ms   else None,
                    "verify_ms":    round(hmac_verify_ms, 4) if hmac_verify_ms else None,
                    "guarantees":   "Integrity only. No binding, no single-use, no PQ security.",
                },
                {
                    "scheme":       "Macaroon (pymacaroons, 2 caveats)",
                    "sign_ms":      round(mac_mint_ms,    4) if mac_mint_ms    else None,
                    "verify_ms":    round(mac_verify_ms,  4) if mac_verify_ms  else None,
                    "guarantees":   "Integrity + attenuation. No single-use, no PQ security.",
                },
                {
                    "scheme":       "RBC Warrant (Ed25519 + ML-DSA-65)",
                    "sign_ms":      rbc_sign_ms,
                    "verify_ms":    rbc_verify_ms,
                    "guarantees":   "Integrity + binding + single-use + PQ security (ML-DSA-65).",
                    "overhead_vs_hmac_ms":      _overhead(rbc_sign_ms, hmac_mint_ms),
                    "overhead_vs_macaroon_ms":  _overhead(rbc_sign_ms, mac_mint_ms),
                    "multiplier_vs_hmac":       _multiplier(rbc_sign_ms, hmac_mint_ms),
                    "multiplier_vs_macaroon":   _multiplier(rbc_sign_ms, mac_mint_ms),
                },
            ],
        },
        "frame_b_full_admission_cycle": {
            "description": (
                "Full protocol cost per admission. "
                "HMAC and Macaroon figures use the round-trip (mint + verify) as the "
                "closest equivalent to RBC's L_total, noting that they do not include "
                "Correspondence Form resolution, AI validation, atomic burn, or EvR commit."
            ),
            "rows": [
                {
                    "scheme":       "HMAC-SHA256 Bearer Token",
                    "cycle_ms":     round(hmac_rt_ms,  4) if hmac_rt_ms  else None,
                    "includes":     "mint + verify only",
                    "excludes":     "binding resolution, single-use enforcement, EvR commit",
                },
                {
                    "scheme":       "Macaroon (pymacaroons, 2 caveats)",
                    "cycle_ms":     round(mac_rt_ms,   4) if mac_rt_ms   else None,
                    "includes":     "mint + verify only",
                    "excludes":     "binding resolution, single-use enforcement, EvR commit",
                },
                {
                    "scheme":       "RBC Warrant (Ed25519 + ML-DSA-65)",
                    "cycle_ms":     rbc_ltotal_ms,
                    "includes":     "full L_total: mint + preflight + admit + EvR commit",
                    "excludes":     "nothing — this is the complete admission cycle",
                    "overhead_vs_hmac_ms":     _overhead(rbc_ltotal_ms, hmac_rt_ms),
                    "overhead_vs_macaroon_ms": _overhead(rbc_ltotal_ms, mac_rt_ms),
                },
            ],
        },
    }


# ──────────────────────────────────────────────────────────────────
# Group runner
# ──────────────────────────────────────────────────────────────────

def run(n_iter: int = N_ITER) -> Dict[str, Any]:
    global N_ITER
    N_ITER = n_iter

    print("  7.1  HMAC-SHA256 bearer token …")
    hmac_results     = bench_7_1()

    print("  7.2  Macaroon (pymacaroons) …")
    macaroon_results = bench_7_2()

    print("  7.3  RBC reference figures (Group 1) …")
    rbc_results      = bench_7_3()

    all_metrics = hmac_results + macaroon_results + rbc_results

    table5 = _build_table5_summary(hmac_results, macaroon_results, rbc_results)

    return {
        "group":   "7",
        "title":   "Comparison Baselines",
        "n_iter":  n_iter,
        "metrics": all_metrics,
        "table5":  table5,
        "fairness_note": (
            "HMAC-SHA256 and Macaroon figures measure cryptographic cost only. "
            "They do not include the operations required for RBC's containment "
            "guarantees (binding resolution, single-use enforcement, EvR commit). "
            "See table5.frame_a and table5.frame_b for structured comparison."
        ),
    }


# ──────────────────────────────────────────────────────────────────
# Text renderer (for run_all.py integration)
# ──────────────────────────────────────────────────────────────────

def render_group7(g: Dict[str, Any]) -> List[str]:
    lines = []
    sep = "\u2500" * 78

    lines.append(f"\n{sep}")
    lines.append(f"  Group 7 \u2014 {g['title']}")
    lines.append(sep)

    def _fmt(v):
        if v is None: return "\u2014"
        try: return f"{float(v):.4f}"
        except: return str(v)

    lines.append(f"\n  {'Metric':<52} {'mean':>8} {'p50':>8} {'p95':>8} {'p99':>8}  (ms)")
    lines.append("  " + "\u2500" * 74)

    for m in g.get("metrics", []):
        if "mean_ms" in m:
            lines.append(
                f"  {m.get('metric',''):<52} "
                f"{_fmt(m.get('mean_ms')):>8} "
                f"{_fmt(m.get('p50_ms')):>8} "
                f"{_fmt(m.get('p95_ms')):>8} "
                f"{_fmt(m.get('p99_ms')):>8} ms"
                + (f"  [{m.get('scheme','')}]" if m.get('scheme') else "")
            )
        elif "gap" in m:
            lines.append(f"  \u26A0  {m.get('metric','')}:  GAP \u2014 {m.get('gap','')}")
        elif m.get("source"):
            lines.append(
                f"  {m.get('metric',''):<52} "
                f"{_fmt(m.get('mean_ms')):>8} ms "
                f"  [reference, not re-measured]"
            )

    # Table 5 summary
    t5 = g.get("table5", {})
    if t5:
        lines.append(f"\n  {t5.get('title','Table 5')}")
        lines.append(f"  Platform: {t5.get('platform','')}")

        for frame_key, frame_label in [
            ("frame_a_cryptographic_only", "Frame A \u2014 Cryptographic cost only"),
            ("frame_b_full_admission_cycle", "Frame B \u2014 Full admission cycle"),
        ]:
            frame = t5.get(frame_key, {})
            if not frame: continue
            lines.append(f"\n    {frame_label}")
            lines.append(f"    {frame.get('description','')}")
            for row in frame.get("rows", []):
                sign_v  = _fmt(row.get("sign_ms"))
                verify_v = _fmt(row.get("verify_ms"))
                cycle_v  = _fmt(row.get("cycle_ms"))
                val = cycle_v if cycle_v != "\u2014" else f"sign={sign_v} verify={verify_v}"
                ovhd_h = row.get("overhead_vs_hmac_ms")
                ovhd_m = row.get("overhead_vs_macaroon_ms")
                suffix = ""
                if ovhd_h is not None:
                    suffix += f"  [+{_fmt(ovhd_h)} vs HMAC]"
                if ovhd_m is not None:
                    suffix += f"  [+{_fmt(ovhd_m)} vs Macaroon]"
                lines.append(f"      {row.get('scheme',''):<45} {val} ms{suffix}")

    return lines


# ──────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json as _json
    print("Running Group 7 \u2014 Comparison Baselines \u2026")
    out = run()

    # Pretty-print Table 5 summary to stdout
    for line in render_group7(out):
        print(line)

    # Write full JSON for Table 5 construction
    import pathlib
    results_dir = pathlib.Path(__file__).resolve().parent / "results"
    results_dir.mkdir(exist_ok=True)
    out_path = results_dir / "group7_baselines.json"
    out_path.write_text(_json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n\u2713  Full JSON written: {out_path}")

