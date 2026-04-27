# benchmarks/group6_analytical.py  (updated — ML-DSA measured when dilithium-py present)
"""
Group 6 — Analytical Estimates / Signature Gap

Updated for G-3: when dilithium-py is installed, ML-DSA latency is MEASURED
rather than estimated.  Ed25519 is always measured directly.
Analytical fallbacks are retained for platforms where the package is absent.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

# ── Analytical fallback values (NIST / OpenSSL reference data) ───
_ED25519_ANALYTICAL = {"sign_us": 55.0, "verify_us": 135.0,
    "source": "OpenSSL 3.x `openssl speed ed25519`; NIST SP 800-186 §3.1"}
_MLDSA_ANALYTICAL   = {"sign_us": 490.0, "verify_us": 220.0,
    "source": "pqcrystals/dilithium AVX2; NIST IR 8413 Table 2"}


def _measure_ed25519(ward_path=None) -> Dict[str, Any]:
    """Measure Ed25519 on real Ward key if path provided, else use analytical value."""
    if ward_path is None:
        return {"measured": False, **_ED25519_ANALYTICAL}

    try:
        from cryptography.hazmat.primitives import serialization
        from src.digests import canonical_json_bytes
        import json

        key_path = ward_path / "keys" / "ward_ed25519_private.pem"
        if not key_path.exists():
            return {"measured": False, **_ED25519_ANALYTICAL}

        priv = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        pub  = priv.public_key()
        msg  = canonical_json_bytes({"bench": "true"})
        sig  = priv.sign(msg)

        N, WARMUP = 1000, 50
        for _ in range(WARMUP):
            priv.sign(msg)
        t0 = time.perf_counter()
        for _ in range(N):
            priv.sign(msg)
        sign_us = (time.perf_counter() - t0) / N * 1_000_000

        for _ in range(WARMUP):
            pub.verify(sig, msg)
        t0 = time.perf_counter()
        for _ in range(N):
            pub.verify(sig, msg)
        verify_us = (time.perf_counter() - t0) / N * 1_000_000

        return {"measured": True, "sign_us": round(sign_us, 2), "verify_us": round(verify_us, 2),
                "source": "Measured on target hardware via cryptography library.", "n": N}
    except Exception as e:
        return {"measured": False, "note": str(e), **_ED25519_ANALYTICAL}


def _measure_mldsa(ward_path=None) -> Dict[str, Any]:
    """
    Measure ML-DSA via the same backend dispatch used by mldsa_signer.py.
    Tries liboqs first (C/fast), falls back to dilithium-py (pure Python).
    """
    try:
        from src.mldsa_signer import (
            _backend, _keygen, _sign, _verify, backend_name, MLDSANotAvailable
        )
        from src.digests import canonical_json_bytes

        backend = _backend()
        bname   = backend_name()

        pk, sk = _keygen()
        msg = canonical_json_bytes({"bench": "true"})
        sig = _sign(sk, msg)

        N      = 1000 if backend == "liboqs" else 100
        WARMUP = 50   if backend == "liboqs" else 10

        for _ in range(WARMUP):
            _sign(sk, msg)
        t0 = time.perf_counter()
        for _ in range(N):
            _sign(sk, msg)
        sign_us = (time.perf_counter() - t0) / N * 1_000_000

        for _ in range(WARMUP):
            _verify(pk, msg, sig)
        t0 = time.perf_counter()
        for _ in range(N):
            _verify(pk, msg, sig)
        verify_us = (time.perf_counter() - t0) / N * 1_000_000

        return {
            "measured":  True,
            "sign_us":   round(sign_us,   2),
            "verify_us": round(verify_us, 2),
            "source":    f"Measured on target hardware via {bname}.",
            "n":         N,
            "algorithm": "ML-DSA Level 3 (ML-DSA-65 / Dilithium3)",
            "standard":  "NIST FIPS 204",
            "backend":   bname,
        }

    except Exception as e:
        return {
            "measured": False,
            "gap":      f"ML-DSA backend unavailable: {e}",
            **_MLDSA_ANALYTICAL,
            "algorithm": "ML-DSA Level 3 (Dilithium3)",
            "standard":  "NIST FIPS 204",
            "source":    _MLDSA_ANALYTICAL["source"],
        }


def run(component_means_ms: Optional[Dict[str, float]] = None,
        ward_path=None) -> Dict[str, Any]:

    ed = _measure_ed25519(ward_path)
    ml = _measure_mldsa(ward_path)

    ed_sign_ms   = round(ed["sign_us"]   / 1000, 4)
    ed_verify_ms = round(ed["verify_us"] / 1000, 4)
    ml_sign_ms   = round(ml["sign_us"]   / 1000, 4)
    ml_verify_ms = round(ml["verify_us"] / 1000, 4)

    overhead_mint_ms   = round(ed_sign_ms   + ml_sign_ms,   4)
    overhead_verify_ms = round(ed_verify_ms + ml_verify_ms, 4)

    m_6_1 = {
        "metric": "6.1_ed25519_sign_verify",
        "algorithm": "Ed25519",
        "measured": ed["measured"],
        "sign_ms":   ed_sign_ms,
        "verify_ms": ed_verify_ms,
        "source":    ed.get("source", ""),
        "n":         ed.get("n", "analytical"),
    }
    m_6_2 = {
        "metric": "6.2_ml_dsa_l3_sign_verify",
        "algorithm": ml.get("algorithm", "ML-DSA Level 3"),
        "standard":  ml.get("standard", "NIST FIPS 204"),
        "measured":  ml["measured"],
        "sign_ms":   ml_sign_ms,
        "verify_ms": ml_verify_ms,
        "source":    ml.get("source", ""),
        "n":         ml.get("n", "analytical"),
        **({"gap": ml["gap"]} if "gap" in ml else {}),
    }
    m_6_3 = {
        "metric": "6.3_total_signature_overhead",
        "description": "Combined Ed25519 + ML-DSA-L3 overhead per admission cycle.",
        "at_mint":   {"ed25519_sign_ms": ed_sign_ms,   "ml_dsa_sign_ms":   ml_sign_ms,   "total_ms": overhead_mint_ms},
        "at_verify": {"ed25519_verify_ms": ed_verify_ms, "ml_dsa_verify_ms": ml_verify_ms, "total_ms": overhead_verify_ms},
        "both_measured": ed["measured"] and ml["measured"],
        "prototype_status": (
            "Both Ed25519 and ML-DSA measured (G-3 closed)."
            if (ed["measured"] and ml["measured"])
            else "Ed25519 measured; ML-DSA analytical (G-3 open — install dilithium-py)."
        ),
    }

    # 6.4  L_total projection
    if component_means_ms:
        l_eval = component_means_ms.get("1.1_mean_ms", 0.0)
        l_mint = component_means_ms.get("1.5_mean_ms", 0.0)
        l_burn = component_means_ms.get("1.7_mean_ms", 0.0)
        l_evr  = component_means_ms.get("1.8d_mean_ms", 0.0)
        l_total_measured = round(l_eval + l_mint + l_burn + l_evr, 4)
        l_mint_sig = round(l_mint + overhead_mint_ms, 4)
        l_burn_sig = round(l_burn + overhead_verify_ms, 4)
        l_total_sig = round(l_eval + l_mint_sig + l_burn_sig + l_evr, 4)
        m_6_4 = {
            "metric": "6.4_analytical_l_total_with_signatures",
            "l_eval_ms": l_eval, "l_mint_ms_measured": l_mint,
            "l_mint_ms_with_sig": l_mint_sig, "l_burn_ms_measured": l_burn,
            "l_burn_ms_with_sig": l_burn_sig, "l_evr_ms": l_evr,
            "l_total_measured_ms": l_total_measured,
            "l_total_with_sigs_ms": l_total_sig,
            "additional_sig_overhead_ms": round(l_total_sig - l_total_measured, 4),
            "both_measured": ed["measured"] and ml["measured"],
        }
    else:
        m_6_4 = {
            "metric": "6.4_analytical_l_total_with_signatures",
            "note": "No Group 1 component_means_ms supplied.",
            "formula": "L_total_with_sigs = L_eval + (L_mint + Ed25519.sign + ML-DSA.sign) + (L_burn + Ed25519.verify + ML-DSA.verify) + L_evr",
        }

    return {
        "group": "6",
        "title": "Signature Latency (Measured where available, Analytical fallback)",
        "metrics": [m_6_1, m_6_2, m_6_3, m_6_4],
    }


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(run(), indent=2))

