##############################################################################
# Makefile — DCS bursty-traffic evaluation
# Target: Contiki-NG v5.1, Cooja (ContikiMoteType)
#
# Usage:
#   make TARGET=cooja MOTE=sensor          # build sensor binary (DCS)
#   make TARGET=cooja MOTE=sink            # build sink binary
#   make TARGET=cooja MOTE=sensor SLOW_TRAFFIC=1   # Orchestra/MinConf sensor
#   make TARGET=cooja MOTE=sensor BURST_PROB=10     # override burst probability
#   make TARGET=cooja MOTE=sensor BURST_RATE=20     # override burst rate
#   make TARGET=cooja MOTE=sensor BURST_DUR=5       # override burst duration
#   make TARGET=cooja MOTE=sensor CORR_RADIUS=35    # spatial correlation R=35m
##############################################################################

CONTIKI_PROJECT = dcs-sensor dcs-sink

all: $(CONTIKI_PROJECT)

# ── Source files ──────────────────────────────────────────────────────────────
dcs-sensor_src = dcs-sensor.c dcs.c
dcs-sink_src   = dcs-sink.c   dcs.c

# ── Traffic model compile-time parameters ─────────────────────────────────────
SLOW_TRAFFIC  ?= 0
BURST_PROB    ?= 5
BURST_RATE    ?= 10
BURST_DUR     ?= 2
CORR_RADIUS   ?= 0

CFLAGS += -DSLOW_TRAFFIC=$(SLOW_TRAFFIC)
CFLAGS += -DBURST_PROB=$(BURST_PROB)
CFLAGS += -DBURST_RATE=$(BURST_RATE)
CFLAGS += -DBURST_DUR=$(BURST_DUR)
CFLAGS += -DCORR_RADIUS=$(CORR_RADIUS)

# ── Contiki-NG path (override with CONTIKI=/path/to/contiki-ng) ───────────────
CONTIKI ?= $(HOME)/contiki-ng
include $(CONTIKI)/Makefile.include

# ── Scheduler selection ───────────────────────────────────────────────────────
# For DCS: no special flag (uses dcs.c)
# For Orchestra: SCHEDULER=orchestra make ...
# For MinConf: SCHEDULER=tsch-schedule-minimal make ...
# These are handled by the Cooja simulation scripts, not this Makefile.
