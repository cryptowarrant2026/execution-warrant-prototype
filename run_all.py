# benchmarks/run_all.py
"""
RBC Execution Workbench — Full Benchmark Suite Orchestrator

Runs all six metric groups in sequence, collects results into a single
JSON report, and prints a human-readable summary table.

Usage:
    # From the project root (rbc-mvp/):
    python -m benchmarks.run_all

    # Reduced iteration count for quick smoke-test:
    python -m benchmarks.run_all --quick

    # Skip expensive groups:
    python -m benchmarks.run_all --skip 2 5

Output:
    benchmarks/results/rbc_benchmark_<timestamp>.json   — full JSON report
    benchmarks/results/rbc_benchmark_<timestamp>.txt    — human-readable table
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is on sys.path when run as a script
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from benchmarks.harness import hardware_info

import benchmarks.group1_admission_latency   as g1
import benchmarks.group2_burn_store_concurrency as g2
import benchmarks.group3_artifact_integrity  as g3
import benchmarks.group4_fail_closed         as g4
import benchmarks.group5_throughput          as g5
import benchmarks.group6_analytical          as g6


RESULTS_DIR = Path(__file__).resolve().parent / "results"


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _fmt_ms(v: Any, decimals: int = 3) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{decimals}f}"
    except (TypeError, ValueError):
        return str(v)


def _section(title: str, width: int = 78) -> str:
    bar = "─" * width
    return f"\n{bar}\n  {title}\n{bar}"


# ──────────────────────────────────────────────────────────────────
# Text table renderers (one per group)
# ──────────────────────────────────────────────────────────────────

def _render_latency_row(m: Dict[str, Any]) -> str:
    return (
        f"  {m.get('metric', ''):<50s} "
        f"mean={_fmt_ms(m.get('mean_ms'))} "
        f"p50={_fmt_ms(m.get('p50_ms'))} "
        f"p95={_fmt_ms(m.get('p95_ms'))} "
        f"p99={_fmt_ms(m.get('p99_ms'))} "
        f"max={_fmt_ms(m.get('max_ms'))} ms"
    )


def render_group1(g: Dict[str, Any]) -> List[str]:
    lines = [_section(f"Group 1 — {g['title']}  (N={g['n_iter']})")]
    lines.append(f"  {'Metric':<50s} {'mean':>8} {'p50':>8} {'p95':>8} {'p99':>8} {'max':>8}  (ms)")
    lines.append("  " + "─" * 74)
    for m in g.get("metrics", []):
        if "mean_ms" in m:
            lines.append(_render_latency_row(m))
        elif "note" in m:
            lines.append(f"  {m.get('metric', ''):<50s}  {m.get('note', '')} [approx={_fmt_ms(m.get('mean_ms'))} ms]")
    # 1.9 overhead
    for m in g.get("metrics", []):
        if m.get("metric", "").startswith("1.9"):
            lines.append(
                f"\n  L_total measured mean:        {_fmt_ms(m.get('mean_ms'))} ms"
                f"\n  Sum of components mean:       {_fmt_ms(m.get('sum_of_components_mean_ms'))} ms"
                f"\n  Overhead (glue/stack):        {_fmt_ms(m.get('overhead_mean_ms'))} ms"
            )
    return lines


def render_group2(g: Dict[str, Any]) -> List[str]:
    lines = [_section(f"Group 2 — {g['title']}")]
    cs = g.get("correctness_summary", {})
    total_dual = cs.get("total_dual_success_events", "?")
    all_ok     = cs.get("all_levels_passed", False)
    lines.append(f"  Total dual-success events (target 0): {total_dual}")
    lines.append(f"  All levels passed:                    {'✅ YES' if all_ok else '❌ NO'}")
    lines.append("")
    lines.append(f"  {'Level':>8}  {'Dual-succ':>10}  {'1-succ rate':>12}  "
                 f"{'1st-success mean':>18}  {'Reject mean':>14}")
    lines.append("  " + "─" * 68)
    for r in g.get("metrics", []):
        lvl   = r.get("concurrency_level", "")
        dual  = r.get("dual_success_count", "")
        rate  = r.get("single_success_rate_pct", "")
        fs    = r.get("first_success_latency", {})
        rej   = r.get("rejection_latency", {})
        fs_ms = _fmt_ms(fs.get("mean_ms")) if fs else "—"
        rj_ms = _fmt_ms(rej.get("mean_ms")) if rej else "—"
        lines.append(
            f"  {lvl:>8,}  {dual:>10}  {rate:>11}%  {fs_ms:>18} ms  {rj_ms:>12} ms"
        )
    return lines


def render_group3(g: Dict[str, Any]) -> List[str]:
    lines = [_section(f"Group 3 — {g['title']}")]
    for m in g.get("metrics", []):
        metric = m.get("metric", "")
        passed = m.get("passed")
        status = "✅" if passed else ("⚠️ SKIP" if passed is None else "❌ FAIL")
        lines.append(f"  {status}  {metric}")
        if "non_determinism_events" in m:
            lines.append(f"        non_determinism_events: {m['non_determinism_events']}")
        if "mutations_tested" in m:
            lines.append(
                f"        mutations_tested: {m['mutations_tested']}  "
                f"caused_said_change: {m.get('mutations_caused_said_change')}"
            )
        if "mean_ms" in m:
            lines.append(
                f"        timing — mean={_fmt_ms(m.get('mean_ms'))} "
                f"p95={_fmt_ms(m.get('p95_ms'))} ms"
            )
        if m.get("failures"):
            for f in m["failures"]:
                lines.append(f"        ⚠  {f}")
    return lines


def render_group4(g: Dict[str, Any]) -> List[str]:
    lines = [_section(f"Group 4 — {g['title']}  (N={g['n_trials']})")]
    for m in g.get("metrics", []):
        metric  = m.get("metric", "")
        passed  = m.get("passed")
        overall = m.get("overall_passed", passed)
        status  = "✅" if overall else ("⚠️ GAP" if overall is None else "❌ FAIL")
        lines.append(f"  {status}  {metric}")
        if m.get("gap"):
            lines.append(f"        GAP: {m['gap'][:100]}")
        if "rejection_rate_pct" in m and m["rejection_rate_pct"] is not None:
            lines.append(
                f"        rejection_rate: {m['rejection_rate_pct']}%  "
                f"(trials={m.get('trials')})"
            )
        if "field_results" in m:
            for fr in m["field_results"]:
                ok = "✅" if fr["passed"] else "❌"
                lines.append(
                    f"          {ok} field={fr['field']:<40s} "
                    f"rate={fr['rejection_rate_pct']}%"
                )
        if "case_results" in m:
            for cr in m["case_results"]:
                ok = "✅" if cr.get("passed") or cr.get("validate_rejected") else "❌"
                lines.append(
                    f"          {ok} case={cr.get('case', cr.get('label','')):<40s} "
                    f"rate={cr.get('rejection_rate_pct', '—')}%"
                )
    return lines


def render_group5(g: Dict[str, Any]) -> List[str]:
    lines = [_section(f"Group 5 — {g['title']}")]
    for m in g.get("metrics", []):
        metric = m.get("metric", "")
        lines.append(f"\n  {metric}")
        if "throughput_admissions_per_sec" in m:
            lines.append(f"    Throughput:   {m['throughput_admissions_per_sec']} admissions/sec")
        if "level_results" in m:
            lines.append(f"    {'N':>4}  {'admitted':>10}  {'elapsed_s':>10}  {'throughput':>14}  {'mean_lat':>10}")
            lines.append("    " + "─" * 56)
            for lr in m["level_results"]:
                lat = lr.get("per_admission_latency", {})
                lines.append(
                    f"    {lr['concurrency']:>4}  "
                    f"{lr['admitted']:>10}  "
                    f"{lr['elapsed_seconds']:>10.3f}  "
                    f"{lr['throughput_admissions_per_sec']:>14.2f}  "
                    f"{_fmt_ms(lat.get('mean_ms')):>8} ms"
                )
            deg = m.get("throughput_degradation_pct_n1_to_nmax")
            if deg is not None:
                lines.append(f"    Throughput degradation N=1→N_max: {deg}%")
        if "peak_kb" in m:
            pk = m["peak_kb"]
            lines.append(
                f"    Peak memory:  mean={_fmt_ms(pk.get('mean_ms'),1)} KB  "
                f"p99={_fmt_ms(pk.get('p99_ms'),1)} KB  "
                f"max={_fmt_ms(pk.get('max_ms'),1)} KB"
            )
    return lines


def render_group6(g: Dict[str, Any]) -> List[str]:
    lines = [_section(f"Group 6 — {g['title']}")]
    lines.append(f"  NOTE: Analytical estimates — NOT measured.")
    for m in g.get("metrics", []):
        metric = m.get("metric", "")
        lines.append(f"\n  {metric}")
        if "sign_ms" in m:
            lines.append(f"    sign   ≈ {_fmt_ms(m['sign_ms'])} ms  ({m.get('algorithm','')})")
            lines.append(f"    verify ≈ {_fmt_ms(m['verify_ms'])} ms")
            lines.append(f"    source: {m.get('source','')}")
        if "at_mint" in m:
            am = m["at_mint"]
            av = m["at_verify"]
            # key is total_ms (measured) or total_overhead_ms (analytical)
            mint_total   = am.get("total_ms") or am.get("total_overhead_ms")
            verify_total = av.get("total_ms") or av.get("total_overhead_ms")
            lines.append(f"    at mint:   {_fmt_ms(mint_total)} ms total")
            lines.append(f"    at verify: {_fmt_ms(verify_total)} ms total")
            if m.get("both_measured"):
                lines.append(f"    source: both Ed25519 and ML-DSA measured on target hardware")
        if "l_total_with_sigs_ms" in m:
            lines.append(
                f"    L_total (measured):         {_fmt_ms(m.get('l_total_measured_ms'))} ms\n"
                f"    L_total (with sigs):        {_fmt_ms(m.get('l_total_with_sigs_ms'))} ms\n"
                f"    Additional sig overhead:    {_fmt_ms(m.get('additional_sig_overhead_ms'))} ms"
            )
        elif m.get("note"):
            lines.append(f"    {m['note']}")
    return lines


RENDERERS = {
    "1": render_group1,
    "2": render_group2,
    "3": render_group3,
    "4": render_group4,
    "5": render_group5,
    "6": render_group6,
}


# ──────────────────────────────────────────────────────────────────
# Main orchestrator
# ──────────────────────────────────────────────────────────────────

def run_all(
    skip: Optional[List[str]] = None,
    quick: bool = False,
) -> Dict[str, Any]:

    skip = set(skip or [])
    n_iter   = 100  if quick else 1000
    n_trials = 20   if quick else 100
    g2_trials = 5   if quick else 30

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = _ts()

    report: Dict[str, Any] = {
        "run_id":    ts,
        "generated": datetime.now(timezone.utc).isoformat(),
        "hardware":  hardware_info(),
        "quick_mode": quick,
        "groups": {},
    }

    t_suite_start = time.perf_counter()
    g1_result = None   # needed for group 6

    # ── Group 1 ──────────────────────────────────────────────────
    if "1" not in skip:
        print("\n[Group 1] Admission Latency …")
        t0 = time.perf_counter()
        g1_result = g1.run(n_iter=n_iter)
        g1_result["elapsed_seconds"] = round(time.perf_counter() - t0, 2)
        report["groups"]["1"] = g1_result
        print(f"  ✓  Group 1 done in {g1_result['elapsed_seconds']:.1f}s")
    else:
        print("[Group 1] SKIPPED")

    # ── Group 2 ──────────────────────────────────────────────────
    if "2" not in skip:
        print("\n[Group 2] Burn Store Concurrency …")
        t0 = time.perf_counter()
        g2_result = g2.run(trials=g2_trials)
        g2_result["elapsed_seconds"] = round(time.perf_counter() - t0, 2)
        report["groups"]["2"] = g2_result
        print(f"  ✓  Group 2 done in {g2_result['elapsed_seconds']:.1f}s")
    else:
        print("[Group 2] SKIPPED")

    # ── Group 3 ──────────────────────────────────────────────────
    if "3" not in skip:
        print("\n[Group 3] Artifact Integrity …")
        t0 = time.perf_counter()
        g3_result = g3.run(n_iter=n_iter)
        g3_result["elapsed_seconds"] = round(time.perf_counter() - t0, 2)
        report["groups"]["3"] = g3_result
        print(f"  ✓  Group 3 done in {g3_result['elapsed_seconds']:.1f}s")
    else:
        print("[Group 3] SKIPPED")

    # ── Group 4 ──────────────────────────────────────────────────
    if "4" not in skip:
        print("\n[Group 4] Fail-Closed Behaviour …")
        t0 = time.perf_counter()
        g4_result = g4.run(n_trials=n_trials)
        g4_result["elapsed_seconds"] = round(time.perf_counter() - t0, 2)
        report["groups"]["4"] = g4_result
        print(f"  ✓  Group 4 done in {g4_result['elapsed_seconds']:.1f}s")
    else:
        print("[Group 4] SKIPPED")

    # ── Group 5 ──────────────────────────────────────────────────
    if "5" not in skip:
        print("\n[Group 5] Throughput and Scalability …")
        t0 = time.perf_counter()
        g5_result = g5.run()
        g5_result["elapsed_seconds"] = round(time.perf_counter() - t0, 2)
        report["groups"]["5"] = g5_result
        print(f"  ✓  Group 5 done in {g5_result['elapsed_seconds']:.1f}s")
    else:
        print("[Group 5] SKIPPED")

    # ── Group 6 ──────────────────────────────────────────────────
    if "6" not in skip:
        print("\n[Group 6] Analytical Estimates …")
        # Extract Group 1 component means for 6.4 if available
        comp_means: Optional[Dict[str, float]] = None
        if g1_result:
            comp_means = {}
            for m in g1_result.get("metrics", []):
                mid = m.get("metric", "")
                if mid.startswith("1.1") and "mean_ms" in m:
                    comp_means["1.1_mean_ms"] = m["mean_ms"]
                elif mid == "1.5_warrant_mint_full" and "mean_ms" in m:
                    comp_means["1.5_mean_ms"] = m["mean_ms"]
                elif mid.startswith("1.7") and "mean_ms" in m:
                    comp_means["1.7_mean_ms"] = m["mean_ms"]
                elif mid == "1.8d_evr_compound_commit" and "mean_ms" in m:
                    comp_means["1.8d_mean_ms"] = m["mean_ms"]

        g6_result = g6.run(component_means_ms=comp_means)
        report["groups"]["6"] = g6_result
        print("  ✓  Group 6 done")
    else:
        print("[Group 6] SKIPPED")

    report["total_elapsed_seconds"] = round(time.perf_counter() - t_suite_start, 2)

    # ── Write JSON ────────────────────────────────────────────────
    json_path = RESULTS_DIR / f"rbc_benchmark_{ts}.json"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n✓  JSON report written: {json_path}")

    # ── Write text table ──────────────────────────────────────────
    txt_lines: List[str] = [
        "=" * 78,
        "  RBC Execution Workbench — Benchmark Report",
        f"  Run: {ts}",
        f"  Hardware: {report['hardware'].get('cpu', '?')}  "
        f"RAM: {report['hardware'].get('ram_gb', '?')} GB  "
        f"OS: {report['hardware'].get('os', '?')}",
        f"  Python: {report['hardware'].get('python_version', '?')}",
        f"  Quick mode: {quick}",
        "=" * 78,
    ]

    for gid, renderer in RENDERERS.items():
        if gid in report["groups"]:
            txt_lines.extend(renderer(report["groups"][gid]))

    txt_lines.append(f"\n{'─'*78}")
    txt_lines.append(f"  Total elapsed: {report['total_elapsed_seconds']:.1f}s")
    txt_lines.append("=" * 78)

    txt_path = RESULTS_DIR / f"rbc_benchmark_{ts}.txt"
    txt_path.write_text("\n".join(txt_lines) + "\n", encoding="utf-8")
    print(f"✓  Text report written: {txt_path}")

    # Print text report to stdout as well
    print()
    print("\n".join(txt_lines))

    return report


# ──────────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="RBC Execution Workbench — Full Benchmark Suite"
    )
    p.add_argument(
        "--quick", action="store_true",
        help="Reduced iteration counts for a fast smoke-test (n=100 instead of 1000).",
    )
    p.add_argument(
        "--skip", nargs="*", default=[],
        metavar="GROUP",
        help="Group numbers to skip, e.g. --skip 2 5",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_all(skip=args.skip, quick=args.quick)

