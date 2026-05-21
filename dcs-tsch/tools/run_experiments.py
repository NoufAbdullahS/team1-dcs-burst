#!/usr/bin/env python3
"""
run_experiments.py — Generate and execute the full DCS evaluation experiment suite.

This script:
  1. Defines all experiment configurations (schedulers × seeds × parameters)
  2. Generates .csc files via generate_csc.py
  3. Runs each simulation headlessly via Cooja's command-line interface
  4. Saves raw log output to results/raw/<experiment_id>.log
  5. Prints a progress summary

PREREQUISITE: Contiki-NG v5.1 must be available. Set CONTIKI_PATH below or
export CONTIKI=/path/to/contiki-ng

Run from the dcs-tsch/ project directory:
    python3 tools/run_experiments.py

To run only a subset (e.g. DCS + OST, base bursty only):
    python3 tools/run_experiments.py --scheduler dcs ost --mode bursty

To run the stress sweep only:
    python3 tools/run_experiments.py --mode sweep

To run correlated-burst experiments only:
    python3 tools/run_experiments.py --mode correlated

Parallelism (run N simulations simultaneously):
    python3 tools/run_experiments.py --parallel 4
"""

import argparse
import os
import sys
import subprocess
import time
import json
from pathlib import Path
from itertools import product
from concurrent.futures import ProcessPoolExecutor, as_completed
from generate_csc import generate_csc

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).parent
PROJECT_DIR  = SCRIPT_DIR.parent
RESULTS_DIR  = PROJECT_DIR / "results" / "raw"
RUNS_DIR     = PROJECT_DIR / "runs"
BUILD_DIR    = PROJECT_DIR / "build"

CONTIKI_PATH = os.environ.get("CONTIKI", str(Path.home() / "contiki-ng"))
COOJA_JAR    = Path(CONTIKI_PATH) / "tools" / "cooja" / "dist" / "cooja.jar"

SIMULATION_DURATION_S = 3600
TIMEOUT_MARGIN_S      = 120   # extra wall-clock seconds before kill

# ── Experiment definitions ────────────────────────────────────────────────────
SCHEDULERS = ["dcs", "ost", "orchestra", "minconf"]
SLOW_TRAFFIC_SCHEDULERS = {"orchestra", "minconf"}

SEEDS_BURSTY = [1001, 2002, 3003, 4004, 5005, 6006, 7007, 8008, 9009, 1010]
SEEDS_STEADY = [1001, 2002, 3003, 4004, 5005]

# Default bursty parameters
DEFAULT_PROB = 5
DEFAULT_RATE = 10
DEFAULT_DUR  = 2
DEFAULT_CORR = 0

# Stress sweep values
SWEEP_PROBS  = [1, 2, 5, 10, 20, 35, 50]
SWEEP_RATES  = [2, 5, 10, 20, 50, 100]
SWEEP_DURS   = [1, 2, 5, 10, 20]          # seconds (0.5 s skipped — Cooja timer res)
SWEEP_CORRS  = [0, 35, 50, 100]           # metres


def experiment_id(scheduler, seed, prob, rate, dur, corr, mode="bursty"):
    return (f"{scheduler}_{mode}_s{seed}_p{prob}_r{rate}_d{dur}_c{corr}")


def build_experiment_list(mode_filter):
    """Return list of experiment dicts to run."""
    expts = []

    def add(sched, seed, prob, rate, dur, corr, mode, steady=False):
        eid = experiment_id(sched, seed, prob, rate, dur, corr, mode)
        slow = sched in SLOW_TRAFFIC_SCHEDULERS
        expts.append(dict(
            id=eid, scheduler=sched, seed=seed,
            burst_prob=prob, burst_rate=rate, burst_dur=dur,
            corr_radius=corr, mode=mode, steady=steady, slow=slow,
        ))

    # ── Steady-traffic validation (no bursts) ─────────────────────────────
    if mode_filter in ("all", "steady"):
        for sched in SCHEDULERS:
            for seed in SEEDS_STEADY:
                add(sched, seed, 0, 0, 0, 0, "steady", steady=True)

    # ── Base bursty (default parameters, 10 seeds) ────────────────────────
    if mode_filter in ("all", "bursty"):
        for sched in SCHEDULERS:
            for seed in SEEDS_BURSTY:
                add(sched, seed, DEFAULT_PROB, DEFAULT_RATE,
                    DEFAULT_DUR, DEFAULT_CORR, "bursty")

    # ── Stress sweep — burst probability ──────────────────────────────────
    if mode_filter in ("all", "sweep"):
        for sched in SCHEDULERS:
            for prob in SWEEP_PROBS:
                if prob == DEFAULT_PROB:
                    continue  # already covered by base bursty
                for seed in SEEDS_BURSTY:
                    add(sched, seed, prob, DEFAULT_RATE,
                        DEFAULT_DUR, DEFAULT_CORR, "sweep_prob")

        # Burst rate sweep
        for sched in SCHEDULERS:
            for rate in SWEEP_RATES:
                if rate == DEFAULT_RATE:
                    continue
                for seed in SEEDS_BURSTY:
                    add(sched, seed, DEFAULT_PROB, rate,
                        DEFAULT_DUR, DEFAULT_CORR, "sweep_rate")

        # Burst duration sweep
        for sched in SCHEDULERS:
            for dur in SWEEP_DURS:
                if dur == DEFAULT_DUR:
                    continue
                for seed in SEEDS_BURSTY:
                    add(sched, seed, DEFAULT_PROB, DEFAULT_RATE,
                        dur, DEFAULT_CORR, "sweep_dur")

    # ── Spatially correlated bursts ───────────────────────────────────────
    if mode_filter in ("all", "correlated"):
        for sched in SCHEDULERS:
            for corr in SWEEP_CORRS:
                if corr == DEFAULT_CORR:
                    continue  # already in base bursty
                for seed in SEEDS_BURSTY:
                    add(sched, seed, DEFAULT_PROB, DEFAULT_RATE,
                        DEFAULT_DUR, corr, "correlated")

    return expts


def run_one(expt):
    """Run a single Cooja simulation. Returns (expt_id, success, elapsed_s)."""
    eid      = expt["id"]
    csc_path = RUNS_DIR / f"{eid}.csc"
    log_path = RESULTS_DIR / f"{eid}.log"

    # Skip if already done
    if log_path.exists() and log_path.stat().st_size > 1000:
        return eid, True, 0, "skipped (already done)"

    # Generate .csc
    try:
        generate_csc(
            scheduler   = expt["scheduler"],
            seed        = expt["seed"],
            burst_prob  = expt["burst_prob"] if not expt["steady"] else 0,
            burst_rate  = expt["burst_rate"] if not expt["steady"] else 0,
            burst_dur   = expt["burst_dur"]  if not expt["steady"] else 0,
            corr_radius = expt["corr_radius"],
            output_path = str(csc_path),
            build_dir   = str(BUILD_DIR),
            slow_traffic= expt["slow"],
        )
    except Exception as e:
        return eid, False, 0, f"CSC generation failed: {e}"

    # Run Cooja headlessly
    cmd = [
        "java", "-Xmx1g",
        "-jar", str(COOJA_JAR),
        "--no-gui",
        f"--random-seed={expt['seed']}",
        str(csc_path),
    ]
    timeout_s = SIMULATION_DURATION_S + TIMEOUT_MARGIN_S

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        with open(log_path, "w") as logf:
            proc = subprocess.run(
                cmd,
                stdout=logf,
                stderr=subprocess.STDOUT,
                timeout=timeout_s * 5,  # wall-clock timeout (Cooja runs ~5x slower)
                cwd=str(PROJECT_DIR),
            )
        elapsed = time.time() - t0

        # Check for success marker
        with open(log_path) as f:
            content = f.read()
        if "TEST OK" in content:
            return eid, True, elapsed, "ok"
        else:
            return eid, False, elapsed, "no TEST OK in log"
    except subprocess.TimeoutExpired:
        return eid, False, time.time() - t0, "wall-clock timeout"
    except FileNotFoundError:
        return eid, False, 0, f"Cooja JAR not found: {COOJA_JAR}"
    except Exception as e:
        return eid, False, time.time() - t0, str(e)


def main():
    parser = argparse.ArgumentParser(
        description="Run the full DCS bursty-traffic evaluation experiment suite")
    parser.add_argument("--scheduler", nargs="+",
                        choices=["dcs","ost","orchestra","minconf"],
                        default=None,
                        help="Restrict to these schedulers (default: all)")
    parser.add_argument("--mode", choices=["all","steady","bursty","sweep","correlated"],
                        default="all",
                        help="Which experiment set to run (default: all)")
    parser.add_argument("--parallel", type=int, default=1,
                        help="Number of parallel Cooja instances (default: 1)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate CSC files but don't run Cooja")
    parser.add_argument("--list", action="store_true",
                        help="List experiments that would run and exit")
    args = parser.parse_args()

    expts = build_experiment_list(args.mode)

    if args.scheduler:
        expts = [e for e in expts if e["scheduler"] in args.scheduler]

    print(f"\n{'='*60}")
    print(f"DCS Evaluation Experiment Runner")
    print(f"{'='*60}")
    print(f"Total experiments : {len(expts)}")
    print(f"Mode              : {args.mode}")
    print(f"Schedulers        : {args.scheduler or 'all'}")
    print(f"Parallel          : {args.parallel}")
    print(f"Cooja JAR         : {COOJA_JAR}")
    print(f"Results dir       : {RESULTS_DIR}")
    print(f"{'='*60}\n")

    if args.list:
        for e in expts:
            print(f"  {e['id']}")
        print(f"\nTotal: {len(expts)}")
        return

    if args.dry_run:
        print("DRY RUN — generating CSC files only\n")
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        for expt in expts:
            csc_path = RUNS_DIR / f"{expt['id']}.csc"
            try:
                generate_csc(
                    scheduler   = expt["scheduler"],
                    seed        = expt["seed"],
                    burst_prob  = expt["burst_prob"] if not expt["steady"] else 0,
                    burst_rate  = expt["burst_rate"] if not expt["steady"] else 0,
                    burst_dur   = expt["burst_dur"]  if not expt["steady"] else 0,
                    corr_radius = expt["corr_radius"],
                    output_path = str(csc_path),
                    build_dir   = str(BUILD_DIR),
                    slow_traffic= expt["slow"],
                )
            except Exception as ex:
                print(f"  ERROR {expt['id']}: {ex}")
        print(f"\nGenerated {len(expts)} CSC files in {RUNS_DIR}")
        return

    # ── Run experiments ────────────────────────────────────────────────────
    completed = 0
    failed    = 0
    skipped   = 0
    t_total   = time.time()

    results_summary = []

    if args.parallel <= 1:
        for expt in expts:
            eid, ok, elapsed, msg = run_one(expt)
            if msg == "skipped (already done)":
                skipped += 1
                status = "SKIP"
            elif ok:
                completed += 1
                status = "OK "
            else:
                failed += 1
                status = "ERR"
            print(f"  [{status}] {eid:70s}  {elapsed:6.0f}s  {msg}")
            results_summary.append({"id": eid, "ok": ok, "msg": msg, "elapsed": elapsed})
    else:
        with ProcessPoolExecutor(max_workers=args.parallel) as ex:
            futures = {ex.submit(run_one, e): e for e in expts}
            for fut in as_completed(futures):
                eid, ok, elapsed, msg = fut.result()
                if msg.startswith("skipped"):
                    skipped += 1; status = "SKIP"
                elif ok:
                    completed += 1; status = "OK "
                else:
                    failed += 1; status = "ERR"
                print(f"  [{status}] {eid:70s}  {elapsed:6.0f}s  {msg}")
                results_summary.append({"id": eid, "ok": ok, "msg": msg, "elapsed": elapsed})

    wall = time.time() - t_total
    print(f"\n{'='*60}")
    print(f"DONE  completed={completed}  failed={failed}  skipped={skipped}")
    print(f"Wall-clock time: {wall/60:.1f} min")
    print(f"{'='*60}")

    # Save summary
    summary_path = RESULTS_DIR.parent / "run_summary.json"
    with open(summary_path, "w") as f:
        json.dump(results_summary, f, indent=2)
    print(f"Summary saved to {summary_path}\n")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
