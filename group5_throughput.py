# benchmarks/group5_throughput.py
"""
Group 5 — Throughput and Scalability

Characterises the system's throughput ceiling for the agentic deployment
scenario described in §9.

Metrics:
  5.1  Sustained admission throughput (single WEP instance)
  5.2  Throughput degradation under concurrent load (N = 1..32 simultaneous)
  5.3  Memory footprint per admission cycle (tracemalloc)

Implementation notes
────────────────────
• 5.1 / 5.2 use actual warden_admit() with pre-minted warrants so that the
  measured throughput reflects the real authorising admission path, including
  the atomic burn syscall.

• The Burn Store (O_CREAT|O_EXCL) is the scaling bottleneck as expected.
  Each admission writes one file; filesystem I/O dominates at high concurrency.

• For 5.2, threads share a single ward directory and burn-store directory.
  Each thread receives its own pre-minted warrant so there is no token-level
  contention (only filesystem-level I/O contention).

• Python GIL limitation: for CPU-bound work the GIL caps parallelism at ~1
  effective core.  For I/O-bound warden_admit() the GIL is frequently released
  (file I/O, os.open syscall) so thread-level concurrency is partially real.

• 5.3 uses tracemalloc to capture peak memory allocated during one cycle.
  The measurement includes all allocations during the cycle and may reflect
  Python runtime overhead in addition to protocol-specific allocations.
"""
from __future__ import annotations

import gc
import time
import threading
import tracemalloc
from pathlib import Path
from typing import Any, Dict, List, Tuple

from benchmarks.harness import BenchmarkFixtures, stats, read_json
from src.warden_plane import warden_admit

CONCURRENCY_LEVELS_5_2 = [1, 2, 4, 8, 16, 32]
ADMISSION_WINDOW_SECONDS = 5.0   # wall-clock window for 5.1 throughput measurement
N_PREWARRANTS_PER_LEVEL  = 500   # warrants pre-minted per concurrency level in 5.2
N_CYCLES_MEMORY          = 100   # cycles for 5.3 memory measurement


# ──────────────────────────────────────────────────────────────────
# 5.1  Sustained admission throughput (single WEP instance)
# ──────────────────────────────────────────────────────────────────

def bench_5_1(fx: BenchmarkFixtures) -> Dict[str, Any]:
    """
    Pre-mint a large batch of warrants, then admit them as fast as possible
    in a single thread for ADMISSION_WINDOW_SECONDS.

    T_max ≈ admissions / elapsed_seconds
    """
    N_PRE = 2000
    print(f"    Pre-minting {N_PRE} warrants …")
    warrants = fx.premint_warrants(N_PRE)

    admitted = 0
    latencies: List[float] = []
    t_start = time.perf_counter()

    for (_wid, wpath) in warrants:
        t0 = time.perf_counter()
        ok, _r = warden_admit(fx.ward_path, wpath)
        t1 = time.perf_counter()
        elapsed_total = t1 - t_start

        if ok:
            admitted += 1
            latencies.append((t1 - t0) * 1_000.0)

        if elapsed_total >= ADMISSION_WINDOW_SECONDS:
            break

    elapsed = time.perf_counter() - t_start
    throughput_rps = round(admitted / elapsed, 2) if elapsed > 0 else 0.0

    return {
        "metric": "5.1_sustained_admission_throughput",
        "description": (
            f"Max admissions/second over {ADMISSION_WINDOW_SECONDS}s window, single thread. "
            "Approximation of T_max(CS) = 1/L_burn(CS). "
            "Burn Store (O_CREAT|O_EXCL filesystem) is the expected bottleneck."
        ),
        "admitted_count": admitted,
        "elapsed_seconds": round(elapsed, 3),
        "throughput_admissions_per_sec": throughput_rps,
        "per_admission_latency": stats(latencies) if latencies else {},
    }


# ──────────────────────────────────────────────────────────────────
# 5.2  Throughput degradation under concurrent load
# ──────────────────────────────────────────────────────────────────

def _concurrent_admit_batch(
    ward_path: Path,
    warrant_list: List[Tuple[str, Path]],
    n_threads: int,
) -> Tuple[int, float, List[float]]:
    """
    Divide warrant_list evenly across n_threads.  Each thread admits its slice.
    All threads start simultaneously via a Barrier.

    Returns (total_admitted, elapsed_seconds, per_admission_latency_ms_list).
    """
    # Divide warrants across threads
    chunks: List[List[Tuple[str, Path]]] = [[] for _ in range(n_threads)]
    for i, w in enumerate(warrant_list):
        chunks[i % n_threads].append(w)

    barrier    = threading.Barrier(n_threads)
    results    = [0] * n_threads        # admitted count per thread
    latencies: List[List[float]] = [[] for _ in range(n_threads)]
    t_global   = [0.0, 0.0]            # [start, end]

    def worker(idx: int) -> None:
        barrier.wait()
        if idx == 0:
            t_global[0] = time.perf_counter()

        for (_wid, wpath) in chunks[idx]:
            t0 = time.perf_counter()
            ok, _r = warden_admit(ward_path, wpath)
            t1 = time.perf_counter()
            if ok:
                results[idx]  += 1
                latencies[idx].append((t1 - t0) * 1_000.0)

        if idx == 0:
            t_global[1] = time.perf_counter()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    elapsed  = (time.perf_counter() - t_global[0]) if t_global[0] > 0 else 0.0
    admitted = sum(results)
    flat_lat = [l for sub in latencies for l in sub]

    return admitted, elapsed, flat_lat


def bench_5_2(fx: BenchmarkFixtures) -> Dict[str, Any]:
    """
    Measure sustained throughput at concurrency levels N = {1, 2, 4, 8, 16, 32}.
    Each level pre-mints N_PREWARRANTS_PER_LEVEL warrants and admits them.
    """
    level_results = []

    for n_threads in CONCURRENCY_LEVELS_5_2:
        n_warrants = N_PREWARRANTS_PER_LEVEL * n_threads
        print(f"    Concurrency N={n_threads}, pre-minting {n_warrants} warrants …")
        warrants = fx.premint_warrants(n_warrants)

        admitted, elapsed, latencies = _concurrent_admit_batch(
            fx.ward_path, warrants, n_threads
        )

        throughput = round(admitted / elapsed, 2) if elapsed > 0 else 0.0

        level_results.append({
            "concurrency": n_threads,
            "warrants_presented": n_warrants,
            "admitted": admitted,
            "elapsed_seconds": round(elapsed, 3),
            "throughput_admissions_per_sec": throughput,
            "per_admission_latency": stats(latencies) if latencies else {},
        })

    # Identify scaling bottleneck: compare throughput at each level
    if len(level_results) >= 2:
        t1 = level_results[0]["throughput_admissions_per_sec"]
        tn = level_results[-1]["throughput_admissions_per_sec"]
        degradation = round((1 - tn / t1) * 100, 1) if t1 > 0 else None
    else:
        degradation = None

    return {
        "metric": "5.2_throughput_under_concurrent_load",
        "description": (
            "Throughput at concurrency N = {1,2,4,8,16,32}. "
            "Burn Store (filesystem) expected to be the scaling bottleneck. "
            "Python GIL partially released during I/O syscalls."
        ),
        "throughput_degradation_pct_n1_to_nmax": degradation,
        "bottleneck_note": (
            "O_CREAT|O_EXCL on the local filesystem is the contended resource. "
            "A production linearizable store (e.g. Redis SETNX) would change this profile."
        ),
        "level_results": level_results,
    }


# ──────────────────────────────────────────────────────────────────
# 5.3  Memory footprint per admission cycle
# ──────────────────────────────────────────────────────────────────

def bench_5_3(fx: BenchmarkFixtures) -> Dict[str, Any]:
    """
    Use tracemalloc to measure peak memory allocated during one complete
    admission cycle (mint → check → admit → receipt state write).

    Reports peak_kb (peak over cycle) and net_kb (net allocation after cycle),
    averaged over N_CYCLES_MEMORY cycles.
    """
    from benchmarks.harness import write_json, utc_now_iso
    from src.warden_plane import warden_check, build_execution_receipt
    from src.digests import canonical_digest_sha256

    receipts_dir = fx.ward_path / "receipts"

    peak_kbs: List[float] = []
    net_kbs:  List[float] = []

    for _ in range(N_CYCLES_MEMORY):
        gc.collect()
        tracemalloc.start()

        # -- Full admission cycle --
        import uuid as _uuid

        # Mint
        from src.warrant_mint import mint_warrant
        w = mint_warrant(
            ward_path=fx.ward_path,
            correspondence_id=fx.correspondence_id,
            ttl_seconds=600,
            validate_operation_registry=False,
        )

        # Preflight
        wobj = read_json(w.warrant_path)
        warden_check(fx.ward_path, wobj)

        # Admit
        ok, result = warden_admit(fx.ward_path, w.warrant_path)

        # Receipt State
        if ok and result.get("receipt_id"):
            rid = result["receipt_id"]
            state = {
                "receipt_id": rid,
                "state": "committed",
                "committed_at": utc_now_iso(),
                "chain": {
                    "warrant_hash": result.get("warrant_hash", ""),
                    "receipt_hash": canonical_digest_sha256(result.get("execution_receipt") or {}),
                },
            }
            write_json(receipts_dir / f"{rid}.state.json", state)
        # -- End cycle --

        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_kbs.append(peak / 1024.0)
        net_kbs.append(_current / 1024.0)

    return {
        "metric": "5.3_memory_footprint_per_admission_cycle",
        "description": (
            "Peak and net memory allocated per complete admission cycle "
            "(mint → preflight → admit → RS write) via tracemalloc. "
            "Includes Python runtime overhead."
        ),
        "n_cycles": N_CYCLES_MEMORY,
        "peak_kb": stats(peak_kbs),
        "net_kb_at_cycle_end": stats(net_kbs),
    }


# ──────────────────────────────────────────────────────────────────
# Group runner
# ──────────────────────────────────────────────────────────────────

def run() -> Dict[str, Any]:
    results = []

    with BenchmarkFixtures() as fx:
        print("  5.1  Sustained admission throughput …")
        results.append(bench_5_1(fx))

        print("  5.2  Throughput degradation under concurrent load …")
        results.append(bench_5_2(fx))

        print("  5.3  Memory footprint per admission cycle …")
        results.append(bench_5_3(fx))

    return {
        "group": "5",
        "title": "Throughput and Scalability",
        "metrics": results,
    }


if __name__ == "__main__":
    import json as _json
    print("Running Group 5 — Throughput and Scalability …")
    out = run()
    print(_json.dumps(out, indent=2))

