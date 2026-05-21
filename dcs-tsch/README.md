# DCS Bursty-Traffic Evaluation — Complete Implementation
## CSC 489 Final Project, Team 1

This repository contains the complete implementation for reproducing DCS
and evaluating its performance under bursty industrial IoT traffic, compared
against OST, Orchestra, and MinConf.

---

## Directory Structure

```
dcs-tsch/
├── dcs/                        # DCS scheduler + sensor/sink firmware
│   ├── dcs.h                   # DCS public API
│   ├── dcs.c                   # DCS slotframe builder, box assignment
│   ├── dcs-sensor.c            # Sensor app: two-state bursty traffic
│   ├── dcs-sink.c              # Sink app: packet collector, RPL root
│   ├── project-conf.h          # Contiki-NG configuration
│   └── Makefile
│
├── ost/                        # OST scheduler + sensor/sink firmware
│   ├── ost.h
│   ├── ost.c                   # Adaptive cell allocation
│   └── ost-sensor.c            # Same traffic model as DCS sensor
│
├── dcs-bursty.js               # Cooja ScriptRunner: controls simulation end
│
└── tools/
    ├── generate_csc.py         # Generate Cooja .csc config files
    ├── run_experiments.py      # Run all experiments (headless Cooja)
    ├── parse_log.py            # Parse raw logs → CSV metrics
    └── analyse.py              # Statistical tests + figure generation
```

---

## Prerequisites

### 1. Contiki-NG v5.1
```bash
git clone https://github.com/contiki-ng/contiki-ng.git ~/contiki-ng
cd ~/contiki-ng
git checkout v5.1
git submodule update --init --recursive
```

### 2. Docker (recommended)
```bash
docker pull contiker/contiki-ng
```
Or set up the native toolchain per the Contiki-NG wiki.

### 3. Python 3.9+ with dependencies
```bash
pip install numpy scipy matplotlib
```

### 4. Java 17 (for Cooja)
```bash
sudo apt install openjdk-17-jdk
```

---

## Step 1 — Copy Source Files into Contiki-NG

```bash
export CONTIKI=~/contiki-ng

# DCS application
cp -r dcs/  $CONTIKI/examples/dcs-eval/dcs/
cp -r ost/  $CONTIKI/examples/dcs-eval/ost/

# Orchestra and MinConf use Contiki-NG's built-in implementations.
# We only need sensor/sink apps with SLOW_TRAFFIC=1 for those schedulers.
# Copy the DCS sensor/sink as templates:
mkdir -p $CONTIKI/examples/dcs-eval/orchestra
cp dcs/dcs-sensor.c  $CONTIKI/examples/dcs-eval/orchestra/orchestra-sensor.c
cp dcs/dcs-sink.c    $CONTIKI/examples/dcs-eval/orchestra/orchestra-sink.c
cp dcs/project-conf.h $CONTIKI/examples/dcs-eval/orchestra/

# MinConf (same traffic model, scheduler loaded via MAKE_WITH_TSCH flag)
mkdir -p $CONTIKI/examples/dcs-eval/minconf
cp dcs/dcs-sensor.c  $CONTIKI/examples/dcs-eval/minconf/minconf-sensor.c
cp dcs/dcs-sink.c    $CONTIKI/examples/dcs-eval/minconf/minconf-sink.c
cp dcs/project-conf.h $CONTIKI/examples/dcs-eval/minconf/
```

### Orchestra Makefile (create in examples/dcs-eval/orchestra/)
```makefile
CONTIKI_PROJECT = orchestra-sensor orchestra-sink
all: $(CONTIKI_PROJECT)
MODULES += os/services/orchestra
SLOW_TRAFFIC ?= 1
BURST_PROB ?= 5
BURST_RATE ?= 10
BURST_DUR  ?= 2
CORR_RADIUS?= 0
CFLAGS += -DSLOW_TRAFFIC=$(SLOW_TRAFFIC) -DBURST_PROB=$(BURST_PROB)
CFLAGS += -DBURST_RATE=$(BURST_RATE) -DBURST_DUR=$(BURST_DUR)
CFLAGS += -DCORR_RADIUS=$(CORR_RADIUS)
CFLAGS += -DORCHESTRA_CONF_RULES='"orchestra-rules-default.h"'
CONTIKI ?= ../../..
include $(CONTIKI)/Makefile.include
```

### MinConf Makefile (create in examples/dcs-eval/minconf/)
```makefile
CONTIKI_PROJECT = minconf-sensor minconf-sink
all: $(CONTIKI_PROJECT)
# MinConf = tsch-schedule-minimal (no extra modules needed)
SLOW_TRAFFIC ?= 1
BURST_PROB ?= 5
BURST_RATE ?= 10
BURST_DUR  ?= 2
CORR_RADIUS?= 0
CFLAGS += -DSLOW_TRAFFIC=$(SLOW_TRAFFIC) -DBURST_PROB=$(BURST_PROB)
CFLAGS += -DBURST_RATE=$(BURST_RATE) -DBURST_DUR=$(BURST_DUR)
CFLAGS += -DCORR_RADIUS=$(CORR_RADIUS)
CFLAGS += -DTSCH_CONF_WITH_MINIMAL_SCHEDULE=1
CONTIKI ?= ../../..
include $(CONTIKI)/Makefile.include
```

---

## Step 2 — Compile All Firmware

```bash
cd $CONTIKI/examples/dcs-eval

# DCS (default traffic parameters)
make -C dcs     TARGET=cooja BURST_PROB=5 BURST_RATE=10 BURST_DUR=2

# OST
make -C ost     TARGET=cooja BURST_PROB=5 BURST_RATE=10 BURST_DUR=2

# Orchestra (slow traffic — 30s normal interval)
make -C orchestra TARGET=cooja SLOW_TRAFFIC=1

# MinConf (slow traffic)
make -C minconf   TARGET=cooja SLOW_TRAFFIC=1
```

For sweep experiments, recompile with different parameters:
```bash
# Burst probability sweep (example: p=20)
make -C dcs TARGET=cooja BURST_PROB=20

# Burst rate sweep (example: r=50)
make -C dcs TARGET=cooja BURST_RATE=50

# Correlated bursts (example: R=35m)
make -C dcs TARGET=cooja CORR_RADIUS=35
```

Or use Docker:
```bash
docker run --rm -v ~/contiki-ng:/home/user/contiki-ng contiker/contiki-ng \
  bash -c "cd /home/user/contiki-ng/examples/dcs-eval/dcs && \
           make TARGET=cooja BURST_PROB=5 BURST_RATE=10 BURST_DUR=2"
```

---

## Step 3 — Run Experiments

### Quick test (DCS only, 2 seeds):
```bash
cd /path/to/dcs-tsch
python3 tools/run_experiments.py --scheduler dcs --mode bursty --parallel 2

# This generates .csc files in runs/ and executes Cooja for each.
# Raw logs appear in results/raw/
```

### Full evaluation (all schedulers, all seeds, all sweeps):
```bash
python3 tools/run_experiments.py --mode all --parallel 4
# Estimated wall-clock time: ~40–80 hours on a 4-core machine
# (920 simulations × ~5–10 min each / 4 parallel)
```

### Dry run (generate CSC files only, don't run Cooja):
```bash
python3 tools/run_experiments.py --dry-run --mode bursty
```

### List all experiment IDs:
```bash
python3 tools/run_experiments.py --list
```

### Manual Cooja run (for debugging):
```bash
# Open the GUI
java -jar ~/contiki-ng/tools/cooja/dist/cooja.jar
# Load a generated .csc file from runs/

# Headless:
java -Xmx1g -jar ~/contiki-ng/tools/cooja/dist/cooja.jar \
     --no-gui runs/dcs_bursty_s1001_p5_r10_d2_c0.csc
```

---

## Step 4 — Parse Logs

```bash
# Parse all raw logs
python3 tools/parse_log.py

# Parse a single log file
python3 tools/parse_log.py --log results/raw/dcs_bursty_s1001_p5_r10_d2_c0.log

# Parse and aggregate across seeds
python3 tools/parse_log.py --aggregate
```

Output files in `results/processed/`:
| File | Contents |
|------|----------|
| `pdr_summary.csv`   | Per-experiment PDR and B-PDR |
| `delay_summary.csv` | Per-experiment delay percentiles (p50, p90, p95, p99) |
| `queue_summary.csv` | Queue occupancy statistics (global, slot-19, burst windows) |
| `timeseries.csv`    | Per-30s-window PDR for all experiments |
| `aggregated.csv`    | Cross-seed mean ± std for all metric/parameter combinations |

---

## Step 5 — Statistical Analysis and Figures

```bash
python3 tools/analyse.py
```

This produces:
- `results/figures/fig_scatter_pdr.png`   — per-seed PDR scatter
- `results/figures/fig_timeseries.png`    — time-series PDR
- `results/figures/fig_delay_cdf.png`     — delay CDFs
- `results/figures/fig_correlated.png`    — PDR vs. correlation radius
- `results/figures/fig_sweep.png`         — stress sweep (3 panels)
- `results/stats.json`                    — Mann-Whitney U test p-values

Console output includes a summary table with mean ± std across seeds and
significance flags for all pairwise comparisons.

---

## Traffic Model Parameters

| Parameter | Default | Flag | Range tested |
|-----------|---------|------|-------------|
| Normal interval (DCS) | 2 s | NORMAL_INTERVAL_S | — |
| Normal interval (Orch/MinConf) | 30 s | SLOW_TRAFFIC=1 | — |
| Burst probability | 5% | BURST_PROB | 1,2.5,5,10,20,35,50 |
| Burst rate | 10 pkt/s | BURST_RATE | 2,5,10,20,50,100 |
| Burst duration | 2 s | BURST_DUR | 1,2,5,10,20 |
| Correlation radius | 0 m | CORR_RADIUS | 0,35,50,100 |
| Seeds (bursty) | — | — | 1001,2002,...,1010 (10 seeds) |
| Seeds (steady) | — | — | 1001,...,5005 (5 seeds) |
| Simulation duration | 3600 s | — | fixed |

---

## Key Log Line Formats

The firmware produces machine-readable log lines parsed by `parse_log.py`:

```
# Received packet at sink:
RX node=5 seq=142 mode=2 send_ms=45210 recv_ms=46004 delay_ms=794 prob=5 rate=10 dur=2 corr=0

# Transmitted packet at sensor:
TX node=5 seq=142 mode=2 ms=45210

# Queue occupancy sample (every 200 ms):
QUEUE node=5 t_ms=45200 global=3 burst=1 slot19=2

# OST adaptive cell allocation:
OST_QUEUE nbr=1 q=5 extra=2 t_ms=45200
```

---

## Expected Results (from paper)

| Scheduler | Overall PDR | B-PDR | N-p50 | B-p50 | B-p99 |
|-----------|-------------|-------|-------|-------|-------|
| DCS       | 96.4% ± 1.8% | 94.8% ± 2.7% | 113 ms | 794 ms | 2,140 ms |
| OST       | 97.1% ± 1.4% | 95.8% ± 2.1% | 101 ms | 620 ms | 1,870 ms |
| MinConf   | 88.3% ± 6.5% | 84.7% ± 8.0% | 546 ms | 2,013 ms | 6,420 ms |
| Orchestra | 65.0% ± 4.1% | 66.9% ± 2.6% | 2,049 ms | 3,694 ms | 9,810 ms |

DCS vs. OST (independent bursts): p = 0.31 (not significant — DCS matches OST).
DCS vs. OST (R=50 m corr. bursts): p = 0.004 (OST significantly better).

---

## Troubleshooting

**Cooja JAR not found**
```
export CONTIKI=~/contiki-ng
# or set COOJA_JAR in run_experiments.py
```

**Compilation fails — `dcs.h` not found**
Make sure `dcs.h` and `dcs.c` are in the same directory as `dcs-sensor.c`.

**Very low PDR in all schedulers**
Check that RPL converged before TSCH data collection started.
Look for `DCS-Sink: ready` in the log before the first `RX` line.

**`tsch_queue_packet_count` undefined**
Contiki-NG v5.1 exposes this via `tsch-queue.h`. Ensure you are using v5.1.

**Cooja runs but no `TEST OK`**
The ScriptRunner script (`dcs-bursty.js`) has a timeout set to
`SIMULATION_DURATION_S + 60` seconds of simulated time.
If Cooja exits without `TEST OK`, check the log for errors.

---

## Citation

This implementation is based on:
- F. Assis et al., "DCS: Dilution-based Convergecast Scheduling in a TSCH
  network," Ad Hoc Networks, vol. 146, p. 103173, 2023.
- S. Jeong et al., "OST: On-Demand TSCH Scheduling with Traffic-Awareness,"
  IEEE INFOCOM, 2020, pp. 2195–2204.

Team 1 — CSC 489, Frontiers in Computer Science
