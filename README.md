# Execution Warrant Prototype

Prototype implementation submitted for ACM CCS 2026 Artifact Evaluation.

This repository contains the source modules, benchmark harness, and benchmark results supporting the empirical evaluation in the submitted paper. All results in Section 8 of the paper were produced from Run ID `20260407T073745Z` using the harness and source modules in this repository.

---

## Artifact Summary

| Target badges | Artifacts Available, Artifacts Evaluated (Functional) |
|---|---|
| Language | Python 3.14.2 |
| Platform tested | Apple Silicon arm64, macOS 26.3 |
| Run ID | 20260407T073745Z |

---

## Repository Structure

```
execution-warrant-prototype/
├── README.md                          # This file
├── requirements.txt                   # Pinned dependencies
├── LICENSE                            # MIT
│
├── Source modules
├── said_python.py                     # Unified SAID construction (Blake3-256, CESR/QB64)
├── digests.py                         # Canonical digest utilities
├── action_intent.py                   # Action Intent canonicalization
├── correspondence_form.py             # Correspondence Form construction
├── correspondence_create.py           # Correspondence Form creation utilities
├── authority_policy.py                # Authority Policy evaluation
├── warden_plane.py                    # Warden Enforcement Plane (preflight + admission)
├── revocation_store.py                # Revocation Store
├── agent_operation.py                 # Agent operation integration
├── persona_card.py                    # Persona Card
├── operation_card.py                  # Operation Card
├── app.py                             # Streamlit demonstration interface
│
├── Benchmark harness
├── harness.py                         # Core benchmark harness
├── run_all.py                         # Run all benchmark groups
├── group1_admission_latency.py        # Group 1: admission latency breakdown
├── group2_burn_store_concurrency.py   # Group 2: concurrent burn store validation
├── group3_artifact_integrity.py       # Group 3: artifact integrity
├── group4_fail_closed.py              # Group 4: fail-closed adversarial rejection
├── group5_throughput.py               # Group 5: throughput and scalability
├── group6_analytical.py               # Group 6: analytical breakdown
├── group7_baselines.py                # Group 7: comparison baselines
│
└── Benchmark results
    ├── rbc_benchmark_20260407T073745Z.json   # Machine-readable results
    └── rbc_benchmark_20260407T073745Z.txt    # Human-readable results
```

---

## Requirements

Python 3.14.2 is required. The following dependencies must be installed:

```bash
pip install -r requirements.txt
```

Key dependencies:

| Package | Version | Purpose |
|---|---|---|
| blake3 | pinned | SAID construction (Blake3-256 hash) |
| liboqs-python | 0.14.1 | ML-DSA-65 signing and verification |
| cryptography | pinned | Ed25519 signing and verification |

The `liboqs-python` package requires the native `liboqs` C library. Installation instructions for macOS and Linux are available at [https://github.com/open-quantum-safe/liboqs-python](https://github.com/open-quantum-safe/liboqs-python).

---

## Reproducing the Benchmark Results

To reproduce the full benchmark suite:

```bash
python run_all.py
```

Results are written to the `results/` directory with a timestamp-based Run ID. The full suite takes approximately 15 to 25 minutes depending on platform.

To run a single benchmark group:

```bash
python group1_admission_latency.py
python group2_burn_store_concurrency.py
python group4_fail_closed.py
```

To run the harness directly with custom parameters:

```bash
python harness.py --help
```

---

## Platform Calibration Note

All results in the paper (Run ID `20260407T073745Z`) were produced on Apple Silicon arm64 (macOS 26.3, Python 3.14.2, liboqs 0.15.0 C native). ML-DSA-65 latency on Apple Silicon is approximately 4x lower than published NIST reference figures for AVX2-capable x86-64. Evaluators running on x86-64 should expect higher cryptographic latency; the non-cryptographic components of the admission cycle (Correspondence Form resolution, canonical JSON serialisation, SAID computation, filesystem writes) will also differ by platform. The structural results (zero dual-success burn events, 100% fail-closed rejection) are platform-independent.

For direct comparison with the paper's reported figures, Apple Silicon arm64 is the reference platform.

---

## Key Results (Run ID 20260407T073745Z)

| Metric | Result |
|---|---|
| End-to-end admission latency (mean) | 2.207 ms |
| End-to-end admission latency (p99) | 2.627 ms |
| Cryptographic premium over HMAC-SHA256 | 0.199 ms |
| Non-cryptographic mint base cost | 0.730 ms (78% of mint time) |
| Dual-success burn events (N=10 to N=10,000) | 0 across 30 trials per level |
| Fail-closed rejection rate | 100% across 2,700 adversarial trials |
| Sustained throughput | 1,203 admissions/second |
| Throughput degradation (N=1 to N=32 threads) | 7.0% |
| Peak memory per admission cycle | 193.3 KB mean |

Full per-component breakdown and all group results are in `rbc_benchmark_20260407T073745Z.txt` and `rbc_benchmark_20260407T073745Z.json`.

---

## Correspondence

This artifact is submitted anonymously for double-blind review. Author contact details will be provided at camera-ready.

