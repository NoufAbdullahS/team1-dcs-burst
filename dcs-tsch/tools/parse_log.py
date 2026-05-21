#!/usr/bin/env python3
"""
parse_log.py — Parse Cooja simulation log files and extract all metrics.

Reads the raw .log files produced by run_experiments.py and produces
structured CSV files in results/processed/:

  pdr_summary.csv        — per-experiment PDR / B-PDR
  delay_summary.csv      — per-experiment delay percentiles
  queue_summary.csv      — per-experiment queue occupancy statistics
  timeseries.csv         — per-30s-window PDR for time-series plots
  per_packet.csv         — raw per-packet records (large!)

Usage:
    python3 tools/parse_log.py
    python3 tools/parse_log.py --log results/raw/dcs_bursty_s1001_p5_r10_d2_c0.log
    python3 tools/parse_log.py --aggregate   # compute cross-seed statistics

The parser recognises these log line formats (produced by the firmware):
    RX node=N seq=S mode=M send_ms=T recv_ms=R delay_ms=D ...
    QUEUE node=N t_ms=T global=G burst=B ...
    TX node=N seq=S mode=M ms=T
    OST_QUEUE nbr=N q=Q extra=E t_ms=T
"""

import argparse
import os
import re
import csv
import json
import math
import statistics
import numpy as np
from pathlib import Path
from collections import defaultdict

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_DIR  = Path(__file__).parent.parent
RAW_DIR      = PROJECT_DIR / "results" / "raw"
PROC_DIR     = PROJECT_DIR / "results" / "processed"

NUM_SENSORS  = 23
SIM_DURATION_S = 3600
WINDOW_S       = 30    # time-series PDR window size

# ── Regex patterns ─────────────────────────────────────────────────────────────
# Cooja wraps each line: [mote_id] sim_time_s APP_OUTPUT
# We strip the wrapper and match the application-level lines.
RE_COOJA_LINE = re.compile(
    r"^\[(\d+)\]\s+([\d.]+)\s+(.+)$"
)

RE_RX = re.compile(
    r"RX\s+node=(\d+)\s+seq=(\d+)\s+mode=(\d+)\s+"
    r"send_ms=(\d+)\s+recv_ms=(\d+)\s+delay_ms=(\d+)"
    r"(?:\s+prob=(\d+))?"
    r"(?:\s+rate=(\d+))?"
    r"(?:\s+dur=(\d+))?"
    r"(?:\s+corr=(\d+))?"
)

RE_TX = re.compile(
    r"TX\s+node=(\d+)\s+seq=(\d+)\s+mode=(\d+)\s+ms=(\d+)"
)

RE_QUEUE = re.compile(
    r"QUEUE\s+node=(\d+)\s+t_ms=(\d+)\s+global=(\d+)\s+burst=(\d+)"
    r"(?:\s+slot19=(\d+))?"
    r"(?:\s+extra_cells=(\d+))?"
)

RE_OST_Q = re.compile(
    r"OST_QUEUE\s+nbr=(\d+)\s+q=(\d+)\s+extra=(\d+)\s+t_ms=(\d+)"
)


def parse_log_file(log_path):
    """Parse a single Cooja log file.

    Returns a dict with:
      rx_packets   : list of RX records
      tx_packets   : list of TX records (if logged)
      queue_records: list of QUEUE records
      ost_q_records: list of OST_QUEUE records
      metadata     : dict with experiment parameters
    """
    rx_packets    = []
    tx_packets    = []
    queue_records = []
    ost_q_records = []
    metadata      = {}

    with open(log_path, "r", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            # Strip Cooja wrapper: [id] sim_time APP_line
            m_cooja = RE_COOJA_LINE.match(line)
            if m_cooja:
                mote_id  = int(m_cooja.group(1))
                sim_time = float(m_cooja.group(2))
                app_line = m_cooja.group(3)
            else:
                # Some Cooja versions don't wrap — try raw
                app_line = line
                mote_id  = 0
                sim_time = 0.0

            # ── RX record ────────────────────────────────────────────────
            m = RE_RX.search(app_line)
            if m:
                rec = {
                    "node_id":   int(m.group(1)),
                    "seq_no":    int(m.group(2)),
                    "mode":      int(m.group(3)),
                    "send_ms":   int(m.group(4)),
                    "recv_ms":   int(m.group(5)),
                    "delay_ms":  int(m.group(6)),
                    "prob":      int(m.group(7)) if m.group(7) else None,
                    "rate":      int(m.group(8)) if m.group(8) else None,
                    "dur":       int(m.group(9)) if m.group(9) else None,
                    "corr":      int(m.group(10)) if m.group(10) else None,
                    "sim_time":  sim_time,
                }
                # Populate metadata from first RX record
                if not metadata and rec["prob"] is not None:
                    metadata = {
                        "burst_prob": rec["prob"],
                        "burst_rate": rec["rate"],
                        "burst_dur":  rec["dur"],
                        "corr_radius":rec["corr"],
                    }
                rx_packets.append(rec)
                continue

            # ── TX record ────────────────────────────────────────────────
            m = RE_TX.search(app_line)
            if m:
                tx_packets.append({
                    "node_id": int(m.group(1)),
                    "seq_no":  int(m.group(2)),
                    "mode":    int(m.group(3)),
                    "send_ms": int(m.group(4)),
                    "sim_time":sim_time,
                })
                continue

            # ── QUEUE record ─────────────────────────────────────────────
            m = RE_QUEUE.search(app_line)
            if m:
                queue_records.append({
                    "node_id":     int(m.group(1)),
                    "t_ms":        int(m.group(2)),
                    "global_q":    int(m.group(3)),
                    "in_burst":    int(m.group(4)),
                    "slot19_q":    int(m.group(5)) if m.group(5) else 0,
                    "extra_cells": int(m.group(6)) if m.group(6) else 0,
                    "sim_time":    sim_time,
                })
                continue

            # ── OST queue record ─────────────────────────────────────────
            m = RE_OST_Q.search(app_line)
            if m:
                ost_q_records.append({
                    "nbr_id":      int(m.group(1)),
                    "q_depth":     int(m.group(2)),
                    "extra_cells": int(m.group(3)),
                    "t_ms":        int(m.group(4)),
                    "sim_time":    sim_time,
                })
                continue

    return {
        "rx_packets":    rx_packets,
        "tx_packets":    tx_packets,
        "queue_records": queue_records,
        "ost_q_records": ost_q_records,
        "metadata":      metadata,
    }


def parse_experiment_id(eid):
    """Parse experiment ID string into components.

    Format: {scheduler}_{mode}_s{seed}_p{prob}_r{rate}_d{dur}_c{corr}
    """
    m = re.match(
        r"^(?P<sched>[a-z]+)_(?P<mode>[a-z_]+)_"
        r"s(?P<seed>\d+)_p(?P<prob>\d+)_r(?P<rate>\d+)_"
        r"d(?P<dur>\d+)_c(?P<corr>\d+)$",
        eid
    )
    if not m:
        return {}
    return {
        "scheduler":   m.group("sched"),
        "mode":        m.group("mode"),
        "seed":        int(m.group("seed")),
        "burst_prob":  int(m.group("prob")),
        "burst_rate":  int(m.group("rate")),
        "burst_dur":   int(m.group("dur")),
        "corr_radius": int(m.group("corr")),
    }


def compute_pdr(rx_packets, tx_packets, num_sensors, mode="all"):
    """Compute PDR from RX packets.

    Since TX counts are not always reliably logged, we estimate expected TX
    from the traffic model parameters and simulation duration.
    Returns (pdr_pct, rx_count, tx_estimate).
    """
    if mode == "all":
        rx = len(rx_packets)
    elif mode == "burst":
        rx = sum(1 for p in rx_packets if p["mode"] == 2)
    else:  # normal
        rx = sum(1 for p in rx_packets if p["mode"] == 1)

    if not tx_packets:
        # Estimate TX from model parameters
        # For mode=all: use RX / expected_delivery as denominator
        # This is a lower bound; use with caution for absolute numbers.
        # The actual TX count comes from the TX log lines.
        return None, rx, None

    if mode == "all":
        tx = len(tx_packets)
    elif mode == "burst":
        tx = sum(1 for p in tx_packets if p["mode"] == 2)
    else:
        tx = sum(1 for p in tx_packets if p["mode"] == 1)

    if tx == 0:
        return 0.0, rx, 0
    return (rx / tx) * 100.0, rx, tx


def compute_delay_percentiles(rx_packets, mode="all"):
    """Return dict of delay percentiles for the given mode."""
    if mode == "all":
        delays = [p["delay_ms"] for p in rx_packets]
    elif mode == "burst":
        delays = [p["delay_ms"] for p in rx_packets if p["mode"] == 2]
    else:
        delays = [p["delay_ms"] for p in rx_packets if p["mode"] == 1]

    if not delays:
        return {k: None for k in ["p50","p90","p95","p99","mean","std","count"]}

    delays_arr = sorted(delays)
    n = len(delays_arr)
    return {
        "p50":   np.percentile(delays_arr, 50),
        "p90":   np.percentile(delays_arr, 90),
        "p95":   np.percentile(delays_arr, 95),
        "p99":   np.percentile(delays_arr, 99),
        "mean":  statistics.mean(delays_arr),
        "std":   statistics.stdev(delays_arr) if n > 1 else 0.0,
        "count": n,
    }


def compute_queue_stats(queue_records):
    """Compute queue occupancy statistics."""
    if not queue_records:
        return {}
    global_qs   = [r["global_q"]  for r in queue_records]
    slot19_qs   = [r["slot19_q"]  for r in queue_records]
    burst_qs    = [r["global_q"]  for r in queue_records if r["in_burst"]]
    return {
        "global_q_mean":       statistics.mean(global_qs),
        "global_q_max":        max(global_qs),
        "slot19_q_mean":       statistics.mean(slot19_qs) if slot19_qs else 0,
        "slot19_q_max":        max(slot19_qs) if slot19_qs else 0,
        "slot19_burst_q_mean": statistics.mean(burst_qs)  if burst_qs  else 0,
        "slot19_burst_q_max":  max(burst_qs)  if burst_qs  else 0,
        "n_queue_samples":     len(global_qs),
    }


def compute_timeseries_pdr(rx_packets, tx_packets, window_s=WINDOW_S):
    """Compute PDR in time windows for the time-series plot."""
    windows = []
    for w_start in range(0, SIM_DURATION_S, window_s):
        w_end = w_start + window_s
        # sim_time is in seconds
        rx_w = sum(1 for p in rx_packets
                   if w_start <= p["sim_time"] < w_end)
        tx_w = sum(1 for p in tx_packets
                   if w_start <= p["sim_time"] < w_end)
        pdr_w = (rx_w / tx_w * 100.0) if tx_w > 0 else None
        windows.append({
            "window_start_s": w_start,
            "window_end_s":   w_end,
            "rx":             rx_w,
            "tx":             tx_w,
            "pdr":            pdr_w,
        })
    return windows


def process_log_file(log_path):
    """Parse one log file and compute all metrics. Returns a result dict."""
    log_path = Path(log_path)
    eid = log_path.stem
    params = parse_experiment_id(eid)

    parsed = parse_log_file(log_path)
    rx = parsed["rx_packets"]
    tx = parsed["tx_packets"]
    q  = parsed["queue_records"]

    overall_pdr_val, rx_all, tx_all = compute_pdr(rx, tx, NUM_SENSORS, "all")
    burst_pdr_val,   rx_b,   tx_b   = compute_pdr(rx, tx, NUM_SENSORS, "burst")
    normal_pdr_val,  rx_n,   tx_n   = compute_pdr(rx, tx, NUM_SENSORS, "normal")

    delay_all    = compute_delay_percentiles(rx, "all")
    delay_burst  = compute_delay_percentiles(rx, "burst")
    delay_normal = compute_delay_percentiles(rx, "normal")
    q_stats      = compute_queue_stats(q)
    ts_pdr       = compute_timeseries_pdr(rx, tx)

    return {
        "experiment_id": eid,
        **params,
        "rx_total":       rx_all,
        "tx_total":       tx_all,
        "rx_burst":       rx_b,
        "tx_burst":       tx_b,
        "rx_normal":      rx_n,
        "tx_normal":      tx_n,
        "pdr_all":        overall_pdr_val,
        "pdr_burst":      burst_pdr_val,
        "pdr_normal":     normal_pdr_val,
        "delay_all":      delay_all,
        "delay_burst":    delay_burst,
        "delay_normal":   delay_normal,
        "queue_stats":    q_stats,
        "timeseries":     ts_pdr,
        "n_queue_records":len(q),
        "n_ost_records":  len(parsed["ost_q_records"]),
    }


def aggregate_across_seeds(results):
    """Group results by (scheduler, mode, prob, rate, dur, corr) and compute
    mean ± std across seeds. Returns list of aggregated dicts."""
    from collections import defaultdict
    groups = defaultdict(list)
    for r in results:
        key = (r.get("scheduler",""), r.get("mode",""),
               r.get("burst_prob",0), r.get("burst_rate",0),
               r.get("burst_dur",0),  r.get("corr_radius",0))
        groups[key].append(r)

    aggregated = []
    for key, rlist in groups.items():
        sched, mode, prob, rate, dur, corr = key
        seeds = [r["seed"] for r in rlist]

        def mean_std(field, subfield=None):
            if subfield:
                vals = [r[field][subfield] for r in rlist
                        if r.get(field) and r[field].get(subfield) is not None]
            else:
                vals = [r[field] for r in rlist if r.get(field) is not None]
            if not vals: return None, None
            m = statistics.mean(vals)
            s = statistics.stdev(vals) if len(vals) > 1 else 0.0
            return m, s

        pdr_m, pdr_s       = mean_std("pdr_all")
        bpdr_m, bpdr_s     = mean_std("pdr_burst")
        npdr_m, npdr_s     = mean_std("pdr_normal")
        dp50_m, dp50_s     = mean_std("delay_normal", "p50")
        dp99_m, dp99_s     = mean_std("delay_normal", "p99")
        bp50_m, bp50_s     = mean_std("delay_burst",  "p50")
        bp99_m, bp99_s     = mean_std("delay_burst",  "p99")
        q_max_m, q_max_s   = mean_std("queue_stats",  "slot19_burst_q_max")

        aggregated.append({
            "scheduler":    sched,
            "mode":         mode,
            "burst_prob":   prob,
            "burst_rate":   rate,
            "burst_dur":    dur,
            "corr_radius":  corr,
            "n_seeds":      len(rlist),
            "seeds":        seeds,
            "pdr_mean":     pdr_m,    "pdr_std":    pdr_s,
            "bpdr_mean":    bpdr_m,   "bpdr_std":   bpdr_s,
            "npdr_mean":    npdr_m,   "npdr_std":   npdr_s,
            "n_p50_mean":   dp50_m,   "n_p50_std":  dp50_s,
            "n_p99_mean":   dp99_m,   "n_p99_std":  dp99_s,
            "b_p50_mean":   bp50_m,   "b_p50_std":  bp50_s,
            "b_p99_mean":   bp99_m,   "b_p99_std":  bp99_s,
            "q_max_mean":   q_max_m,  "q_max_std":  q_max_s,
        })

    return aggregated


def write_csv(rows, path, fieldnames=None):
    if not rows: return
    if fieldnames is None:
        # Collect all keys from all rows
        fieldnames = list(dict.fromkeys(k for r in rows for k in r.keys()))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  Wrote: {path} ({len(rows)} rows)")


def main():
    parser = argparse.ArgumentParser(description="Parse DCS evaluation logs")
    parser.add_argument("--log",       help="Parse a single log file")
    parser.add_argument("--aggregate", action="store_true",
                        help="Compute cross-seed aggregated statistics")
    parser.add_argument("--raw-dir",   default=str(RAW_DIR),
                        help="Directory with raw .log files")
    args = parser.parse_args()

    PROC_DIR.mkdir(parents=True, exist_ok=True)
    raw_dir = Path(args.raw_dir)

    if args.log:
        log_files = [Path(args.log)]
    else:
        log_files = sorted(raw_dir.glob("*.log"))

    print(f"\nParsing {len(log_files)} log files from {raw_dir}\n")

    all_results    = []
    pdr_rows       = []
    delay_rows     = []
    queue_rows     = []
    timeseries_rows= []

    for lf in log_files:
        print(f"  {lf.name} ... ", end="", flush=True)
        try:
            r = process_log_file(lf)
            all_results.append(r)

            # PDR row
            pdr_rows.append({
                "experiment_id": r["experiment_id"],
                "scheduler":     r.get("scheduler"),
                "seed":          r.get("seed"),
                "mode":          r.get("mode"),
                "burst_prob":    r.get("burst_prob"),
                "burst_rate":    r.get("burst_rate"),
                "burst_dur":     r.get("burst_dur"),
                "corr_radius":   r.get("corr_radius"),
                "pdr_all":       r["pdr_all"],
                "pdr_burst":     r["pdr_burst"],
                "pdr_normal":    r["pdr_normal"],
                "rx_total":      r["rx_total"],
                "tx_total":      r["tx_total"],
            })

            # Delay row
            da = r["delay_all"]
            db = r["delay_burst"]
            dn = r["delay_normal"]
            delay_rows.append({
                "experiment_id": r["experiment_id"],
                "scheduler":     r.get("scheduler"),
                "seed":          r.get("seed"),
                "mode":          r.get("mode"),
                "corr_radius":   r.get("corr_radius"),
                # Normal mode
                "n_p50": dn["p50"], "n_p90": dn["p90"],
                "n_p95": dn["p95"], "n_p99": dn["p99"],
                "n_mean":dn["mean"],"n_std": dn["std"],
                # Burst mode
                "b_p50": db["p50"], "b_p90": db["p90"],
                "b_p95": db["p95"], "b_p99": db["p99"],
                "b_mean":db["mean"],"b_std": db["std"],
                # All
                "a_p50": da["p50"], "a_p99": da["p99"],
            })

            # Queue row
            qs = r["queue_stats"]
            queue_rows.append({
                "experiment_id": r["experiment_id"],
                "scheduler":     r.get("scheduler"),
                "seed":          r.get("seed"),
                "mode":          r.get("mode"),
                "corr_radius":   r.get("corr_radius"),
                **qs,
            })

            # Time-series rows
            for ts in r["timeseries"]:
                timeseries_rows.append({
                    "experiment_id": r["experiment_id"],
                    "scheduler":     r.get("scheduler"),
                    "seed":          r.get("seed"),
                    **ts,
                })

            rx  = r["rx_total"] or 0
            pdr = f"{r['pdr_all']:.1f}%" if r["pdr_all"] is not None else "N/A"
            print(f"rx={rx}  PDR={pdr}")

        except Exception as e:
            print(f"ERROR: {e}")

    # Write per-experiment CSVs
    write_csv(pdr_rows,        PROC_DIR / "pdr_summary.csv")
    write_csv(delay_rows,      PROC_DIR / "delay_summary.csv")
    write_csv(queue_rows,      PROC_DIR / "queue_summary.csv")
    write_csv(timeseries_rows, PROC_DIR / "timeseries.csv")

    # Write aggregated CSV
    if args.aggregate or len(log_files) > 1:
        agg = aggregate_across_seeds(all_results)
        write_csv(agg, PROC_DIR / "aggregated.csv")

    # Save full results as JSON for downstream analysis
    json_path = PROC_DIR / "all_results.json"
    with open(json_path, "w") as f:
        # Strip non-serialisable objects
        def clean(r):
            rc = dict(r)
            rc.pop("timeseries", None)  # too large for JSON
            return rc
        json.dump([clean(r) for r in all_results], f, indent=2, default=str)
    print(f"  Wrote: {json_path}")
    print(f"\nParsed {len(all_results)} experiments. Done.\n")


if __name__ == "__main__":
    main()
