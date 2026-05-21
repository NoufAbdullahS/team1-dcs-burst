#!/usr/bin/env python3
"""
analyse.py — Statistical analysis and figure generation for the DCS evaluation.

Reads results/processed/aggregated.csv (produced by parse_log.py --aggregate)
and produces:
  - All figures (PDF + PNG) in results/figures/
  - Statistical test results in results/stats.json
  - Console summary table

Usage:
    python3 tools/analyse.py
    python3 tools/analyse.py --figures-only
    python3 tools/analyse.py --stats-only
"""

import argparse
import json
import csv
import math
import statistics
import sys
from pathlib import Path
from collections import defaultdict

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from scipy import stats as scipy_stats

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_DIR  = Path(__file__).parent.parent
PROC_DIR     = PROJECT_DIR / "results" / "processed"
FIG_DIR      = PROJECT_DIR / "results" / "figures"

# ── Style ─────────────────────────────────────────────────────────────────────
COLORS = {
    "dcs":       "#1A6FBF",
    "ost":       "#E87722",
    "minconf":   "#2BA84A",
    "orchestra": "#C0392B",
}
MARKERS = {"dcs": "o", "ost": "s", "minconf": "^", "orchestra": "D"}
LABELS  = {"dcs": "DCS", "ost": "OST", "minconf": "MinConf", "orchestra": "Orchestra"}

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size":   10,
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "axes.grid":   True,
    "grid.alpha":  0.3,
    "grid.linestyle": "--",
    "figure.dpi":  150,
})

SCHEDULERS = ["dcs", "ost", "minconf", "orchestra"]


# ── Data loading ───────────────────────────────────────────────────────────────
def load_pdr_data():
    """Load per-seed PDR data. Returns dict keyed by scheduler."""
    pdr_path = PROC_DIR / "pdr_summary.csv"
    if not pdr_path.exists():
        print(f"ERROR: {pdr_path} not found. Run parse_log.py first.")
        sys.exit(1)

    data = defaultdict(lambda: defaultdict(list))  # [sched][mode] -> [pdr values]
    with open(pdr_path) as f:
        for row in csv.DictReader(f):
            sched = row["scheduler"]
            mode  = row["mode"]
            pdr   = float(row["pdr_all"]) if row["pdr_all"] else None
            bpdr  = float(row["pdr_burst"]) if row["pdr_burst"] else None
            if pdr  is not None: data[sched][mode + "_pdr"].append(pdr)
            if bpdr is not None: data[sched][mode + "_bpdr"].append(bpdr)
    return data


def load_agg_data():
    """Load aggregated (cross-seed mean/std) data."""
    agg_path = PROC_DIR / "aggregated.csv"
    if not agg_path.exists():
        return []
    rows = []
    with open(agg_path) as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def load_delay_data():
    """Load delay percentile data."""
    delay_path = PROC_DIR / "delay_summary.csv"
    if not delay_path.exists():
        return []
    rows = []
    with open(delay_path) as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def load_timeseries():
    """Load time-series PDR data."""
    ts_path = PROC_DIR / "timeseries.csv"
    if not ts_path.exists():
        return []
    rows = []
    with open(ts_path) as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


# ── Statistical tests ──────────────────────────────────────────────────────────
def mann_whitney_u(a, b):
    """Two-sided Mann-Whitney U test. Returns (statistic, p_value)."""
    if len(a) < 2 or len(b) < 2:
        return None, None
    stat, p = scipy_stats.mannwhitneyu(a, b, alternative="two-sided")
    return float(stat), float(p)


def bootstrap_ci(data, n_boot=1000, alpha=0.05):
    """Bootstrap 95% confidence interval for the mean."""
    if not data:
        return None, None
    data = np.array(data)
    boot_means = [np.mean(np.random.choice(data, len(data), replace=True))
                  for _ in range(n_boot)]
    lo = np.percentile(boot_means, 100 * alpha / 2)
    hi = np.percentile(boot_means, 100 * (1 - alpha / 2))
    return lo, hi


def run_statistical_tests(pdr_data):
    """Run all pairwise Mann-Whitney U tests and return results dict."""
    results = {}

    modes_to_test = [
        ("bursty", "pdr",  "Overall PDR — independent bursts"),
        ("bursty", "bpdr", "Burst-mode PDR — independent bursts"),
    ]
    for (mode, metric, label) in modes_to_test:
        key = mode + "_" + metric
        for s1 in SCHEDULERS:
            for s2 in SCHEDULERS:
                if s1 >= s2: continue
                a = pdr_data[s1].get(key, [])
                b = pdr_data[s2].get(key, [])
                stat, p = mann_whitney_u(a, b)
                test_key = f"{label}: {LABELS[s1]} vs {LABELS[s2]}"
                results[test_key] = {
                    "scheduler_a": s1, "scheduler_b": s2,
                    "mode": mode, "metric": metric,
                    "n_a": len(a), "n_b": len(b),
                    "mean_a": statistics.mean(a) if a else None,
                    "mean_b": statistics.mean(b) if b else None,
                    "U_stat": stat, "p_value": p,
                    "significant": (p < 0.05) if p is not None else None,
                }
    return results


# ── Figure generators ─────────────────────────────────────────────────────────

def fig_scatter_pdr(pdr_data, save_path):
    """Per-seed scatter plot of overall and burst PDR."""
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5))
    fig.patch.set_facecolor("white")
    fig.suptitle("Per-Seed PDR Scatter — 10 Seeds, Bursty Traffic\n"
                 "(dots = individual seeds; bar = mean; whisker = 95% CI)",
                 fontsize=10, fontweight="bold", color="#1A2340")

    for ax, (metric_key, title) in zip(axes, [
        ("bursty_pdr",  "Overall PDR"),
        ("bursty_bpdr", "Burst-mode PDR (B-PDR)"),
    ]):
        ax.set_facecolor("white")
        rng = np.random.default_rng(seed=42)
        for xi, sched in enumerate(SCHEDULERS):
            vals = pdr_data[sched].get(metric_key, [])
            if not vals: continue
            jitter = rng.uniform(-0.15, 0.15, len(vals))
            ax.scatter(xi + jitter, vals,
                       color=COLORS[sched], marker=MARKERS[sched],
                       s=55, zorder=4, alpha=0.85)
            mn = statistics.mean(vals)
            ax.plot([xi - 0.25, xi + 0.25], [mn, mn],
                    color=COLORS[sched], linewidth=2.5)
            lo, hi = bootstrap_ci(vals)
            if lo is not None:
                ax.plot([xi, xi], [lo, hi], color=COLORS[sched], linewidth=1.5)

        ax.set_xticks(range(len(SCHEDULERS)))
        ax.set_xticklabels([LABELS[s] for s in SCHEDULERS], fontsize=9)
        ax.set_ylabel("PDR (%)", fontsize=9)
        ax.set_title(title, fontsize=10, fontweight="bold")
        ax.set_ylim(40, 105)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    plt.tight_layout()
    plt.savefig(save_path, bbox_inches="tight", facecolor="white", dpi=150)
    plt.close()
    print(f"  Saved: {save_path}")


def fig_timeseries(ts_rows, save_path):
    """Instantaneous PDR time-series with burst bands."""
    # Use seed 1001 (or first available) for each scheduler
    seed_target = 1001

    fig, ax = plt.subplots(figsize=(9, 4))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    for sched in SCHEDULERS:
        # Filter to one seed, bursty mode
        rows = [r for r in ts_rows
                if r.get("scheduler") == sched
                and str(r.get("seed", "")) == str(seed_target)
                and "bursty" in r.get("experiment_id", "")]
        if not rows: continue
        rows.sort(key=lambda r: float(r["window_start_s"]))
        t   = [float(r["window_start_s"]) for r in rows]
        pdr = [float(r["pdr"]) if r["pdr"] else None for r in rows]
        ax.plot(t, pdr, color=COLORS[sched], linewidth=1.6,
                label=LABELS[sched], alpha=0.9)

    ax.set_xlabel("Simulation time (s)", fontsize=10)
    ax.set_ylabel("PDR (%)", fontsize=10)
    ax.set_title(f"Instantaneous PDR Over Time — Bursty Traffic (seed {seed_target})",
                 fontsize=11, fontweight="bold", color="#1A2340")
    ax.set_ylim(40, 105)
    ax.set_xlim(0, 3600)
    ax.legend(fontsize=9, loc="lower left", framealpha=0.9, ncol=2)
    plt.tight_layout()
    plt.savefig(save_path, bbox_inches="tight", facecolor="white", dpi=150)
    plt.close()
    print(f"  Saved: {save_path}")


def fig_delay_cdf(delay_rows, save_path):
    """Delay CDFs for normal-mode and burst-mode packets."""
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5))
    fig.patch.set_facecolor("white")
    fig.suptitle("End-to-End Delay CDFs — Bursty Traffic (all seeds)",
                 fontsize=11, fontweight="bold", color="#1A2340")

    rng = np.random.default_rng(0)
    for ax, (col, title) in zip(axes, [
        ("n_p50", "Normal-mode packets"),
        ("b_p50", "Burst-mode packets"),
    ]):
        ax.set_facecolor("white")
        for sched in SCHEDULERS:
            rows = [r for r in delay_rows
                    if r.get("scheduler") == sched
                    and "bursty" in r.get("experiment_id", "")]
            if not rows: continue
            # Build synthetic CDF from per-seed median and p99
            p50_col = "n_p50" if col == "n_p50" else "b_p50"
            p99_col = "n_p99" if col == "n_p50" else "b_p99"
            p50_vals = [float(r[p50_col]) for r in rows if r.get(p50_col)]
            p99_vals = [float(r[p99_col]) for r in rows if r.get(p99_col)]
            if not p50_vals: continue
            p50_m = statistics.mean(p50_vals)
            p99_m = statistics.mean(p99_vals)
            # Log-normal approximation from p50 and p99
            # P50 = exp(mu), P99 = exp(mu + 2.326*sigma)
            if p50_m > 0 and p99_m > p50_m:
                mu    = math.log(p50_m)
                sigma = (math.log(p99_m) - mu) / 2.326
                samples = rng.lognormal(mu, sigma, 5000)
                sorted_s = np.sort(samples)
                cdf = np.arange(1, len(sorted_s) + 1) / len(sorted_s)
                ax.plot(sorted_s, cdf * 100, color=COLORS[sched],
                        linewidth=1.8, label=LABELS[sched])

        ax.set_xlabel("End-to-end delay (ms)", fontsize=9)
        ax.set_ylabel("CDF (%)", fontsize=9)
        ax.set_title(title, fontsize=10, fontweight="bold")
        ax.legend(fontsize=8.5, loc="lower right")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    plt.tight_layout()
    plt.savefig(save_path, bbox_inches="tight", facecolor="white", dpi=150)
    plt.close()
    print(f"  Saved: {save_path}")


def fig_correlated(agg_rows, save_path):
    """PDR vs. correlation radius."""
    corr_vals = [0, 35, 50, 100]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    for sched in SCHEDULERS:
        rows = [r for r in agg_rows
                if r.get("scheduler") == sched
                and r.get("mode","").startswith("corr")]
        rows_d = {int(r["corr_radius"]): r for r in rows}
        # Include R=0 from bursty baseline
        base = next((r for r in agg_rows
                     if r.get("scheduler") == sched
                     and r.get("mode") == "bursty"
                     and int(r.get("corr_radius", 0)) == 0), None)
        if base:
            rows_d[0] = base

        xs   = [c for c in corr_vals if c in rows_d]
        pdrs = [float(rows_d[c]["pdr_mean"]) for c in xs if rows_d[c].get("pdr_mean")]
        stds = [float(rows_d[c]["pdr_std"])  for c in xs if rows_d[c].get("pdr_std")]
        if not xs: continue

        ax.errorbar(xs, pdrs, yerr=stds, color=COLORS[sched],
                    marker=MARKERS[sched], linewidth=2, markersize=7,
                    capsize=4, label=LABELS[sched])

    ax.set_xlabel("Spatial correlation radius R (m)", fontsize=10)
    ax.set_ylabel("PDR (%)", fontsize=10)
    ax.set_title("PDR vs. Spatial Correlation Radius",
                 fontsize=11, fontweight="bold", color="#1A2340")
    ax.set_xticks(corr_vals)
    ax.set_xticklabels(["0\n(indep.)", "35\n(1 box)", "50\n(TX range)", "100\n(interfere)"])
    ax.set_ylim(30, 105)
    ax.legend(fontsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()
    plt.savefig(save_path, bbox_inches="tight", facecolor="white", dpi=150)
    plt.close()
    print(f"  Saved: {save_path}")


def fig_sweep(agg_rows, save_path):
    """3-panel stress sweep figure."""
    fig, axes = plt.subplots(1, 3, figsize=(11, 4.2))
    fig.patch.set_facecolor("white")
    fig.suptitle("DCS Burst-Handling Envelope — Parameter Stress Sweep",
                 fontsize=11, fontweight="bold", color="#1A2340")

    sweeps = [
        ("sweep_prob",  "burst_prob",  "Burst probability (% per 10 s)",   [1,2,5,10,20,35,50]),
        ("sweep_rate",  "burst_rate",  "Burst rate (pkt/s)",                [2,5,10,20,50,100]),
        ("sweep_dur",   "burst_dur",   "Burst duration (s)",                [1,2,5,10,20]),
    ]

    for ax, (mode_prefix, x_col, x_label, x_vals) in zip(axes, sweeps):
        ax.set_facecolor("white")
        for sched in SCHEDULERS:
            rows = [r for r in agg_rows
                    if r.get("scheduler") == sched
                    and r.get("mode","").startswith(mode_prefix.split("_")[0]+"_"+mode_prefix.split("_")[1])]
            rows_d = {int(r[x_col]): r for r in rows}
            # Add default point from base bursty
            base = next((r for r in agg_rows
                         if r.get("scheduler") == sched
                         and r.get("mode") == "bursty"), None)
            if base and x_col == "burst_prob":
                rows_d.setdefault(5, base)
            elif base and x_col == "burst_rate":
                rows_d.setdefault(10, base)
            elif base and x_col == "burst_dur":
                rows_d.setdefault(2, base)

            xs   = sorted(c for c in x_vals if c in rows_d)
            pdrs = [float(rows_d[x]["pdr_mean"]) for x in xs if rows_d[x].get("pdr_mean")]
            if len(xs) > 1:
                ax.plot(xs, pdrs, color=COLORS[sched], marker=MARKERS[sched],
                        linewidth=1.8, markersize=5, label=LABELS[sched])

        ax.set_xlabel(x_label, fontsize=9)
        ax.set_ylabel("PDR (%)", fontsize=9)
        ax.set_title(f"Sweep: {x_label.split('(')[0].strip()}", fontsize=9.5, fontweight="bold")
        ax.set_ylim(20, 105)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        if "pkt/s" in x_label:
            ax.set_xscale("log")

    axes[0].legend(fontsize=7.5)
    plt.tight_layout()
    plt.savefig(save_path, bbox_inches="tight", facecolor="white", dpi=150)
    plt.close()
    print(f"  Saved: {save_path}")


def print_summary_table(pdr_data, mwu_results):
    """Print a summary table to the console."""
    print("\n" + "="*72)
    print("RESULTS SUMMARY")
    print("="*72)
    print(f"{'Scheduler':<12} {'N-PDR mean':>12} {'B-PDR mean':>12} "
          f"{'PDR std':>10} {'n_seeds':>8}")
    print("-"*72)
    for sched in SCHEDULERS:
        pdr_vals  = pdr_data[sched].get("bursty_pdr", [])
        bpdr_vals = pdr_data[sched].get("bursty_bpdr", [])
        if not pdr_vals: continue
        mn  = statistics.mean(pdr_vals)
        std = statistics.stdev(pdr_vals) if len(pdr_vals) > 1 else 0
        bmn = statistics.mean(bpdr_vals) if bpdr_vals else float("nan")
        print(f"  {LABELS[sched]:<10} {mn:>11.1f}% {bmn:>11.1f}% "
              f"{std:>9.2f}  {len(pdr_vals):>7}")
    print()
    print("Mann-Whitney U test results (p < 0.05 = significant):")
    print("-"*72)
    for label, res in mwu_results.items():
        sig = "**" if res["significant"] else "  "
        p   = res["p_value"]
        p_s = f"{p:.4f}" if p is not None else "N/A"
        print(f"  {sig} {label[:55]:<55}  p={p_s}")
    print("="*72 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Analyse DCS evaluation results")
    parser.add_argument("--figures-only", action="store_true")
    parser.add_argument("--stats-only",   action="store_true")
    args = parser.parse_args()

    FIG_DIR.mkdir(parents=True, exist_ok=True)

    pdr_data   = load_pdr_data()
    agg_data   = load_agg_data()
    delay_data = load_delay_data()
    ts_data    = load_timeseries()

    if not args.figures_only:
        mwu_results = run_statistical_tests(pdr_data)
        print_summary_table(pdr_data, mwu_results)

        # Save stats
        stats_path = PROJECT_DIR / "results" / "stats.json"
        with open(stats_path, "w") as f:
            json.dump(mwu_results, f, indent=2)
        print(f"Statistical test results saved to {stats_path}")

    if not args.stats_only:
        print("\nGenerating figures...")
        fig_scatter_pdr(pdr_data,          FIG_DIR / "fig_scatter_pdr.png")
        fig_timeseries(ts_data,            FIG_DIR / "fig_timeseries.png")
        fig_delay_cdf(delay_data,          FIG_DIR / "fig_delay_cdf.png")
        fig_correlated(agg_data,           FIG_DIR / "fig_correlated.png")
        fig_sweep(agg_data,                FIG_DIR / "fig_sweep.png")
        print("\nAll figures saved to results/figures/")


if __name__ == "__main__":
    main()
