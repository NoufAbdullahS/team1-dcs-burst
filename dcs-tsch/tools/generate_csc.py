#!/usr/bin/env python3
"""
generate_csc.py — Generate Cooja .csc simulation configuration files
for the DCS / OST / Orchestra / MinConf bursty-traffic evaluation.

Usage:
    python3 generate_csc.py --scheduler dcs --seed 1001 --burst-prob 5
                            --burst-rate 10 --burst-dur 2 --corr 0
                            --output runs/dcs_s1001_p5_r10_d2_c0.csc

The generated .csc is a complete Cooja simulation file that:
  - Places 24 nodes at the exact 2pb topology positions
  - Configures the correct firmware binary for each mote
  - Sets the random seed
  - Attaches the ScriptRunner plugin with dcs-bursty.js
  - Uses UDGM radio model: TX range=50m, interference=100m, success=1.0

Schedulers:
  dcs        : dcs-sensor.cooja (node 2..24) + dcs-sink.cooja (node 1)
  ost        : ost-sensor.cooja (node 2..24) + ost-sink.cooja  (node 1)
  orchestra  : orchestra-sensor.cooja + orchestra-sink.cooja
  minconf    : minconf-sensor.cooja  + minconf-sink.cooja
"""

import argparse
import os
import sys
import math

# ── 24-node 2pb topology positions (metres) ─────────────────────────────────
NODES = [
    (1,  48.0, 48.0),   # sink
    (2,  10.0, 10.0),
    (3,  20.0, 10.0),
    (4,  45.0, 10.0),
    (5,  55.0, 10.0),
    (6,  80.0, 10.0),
    (7,  90.0, 10.0),
    (8,  10.0, 48.0),
    (9,  20.0, 48.0),
    (10, 80.0, 48.0),
    (11, 90.0, 48.0),
    (12, 10.0, 86.0),
    (13, 20.0, 86.0),
    (14, 45.0, 86.0),
    (15, 55.0, 86.0),
    (16, 80.0, 86.0),
    (17, 90.0, 86.0),
    (18, 45.0, 30.0),
    (19, 55.0, 30.0),
    (20, 45.0, 65.0),
    (21, 55.0, 65.0),
    (22, 30.0, 30.0),
    (23, 30.0, 65.0),
    (24, 65.0, 48.0),
]

# Firmware binary names (relative to build/ directory)
SENSOR_BINS = {
    "dcs":       "dcs/dcs-sensor.cooja",
    "ost":       "ost/ost-sensor.cooja",
    "orchestra": "orchestra/orchestra-sensor.cooja",
    "minconf":   "minconf/minconf-sensor.cooja",
}
SINK_BINS = {
    "dcs":       "dcs/dcs-sink.cooja",
    "ost":       "ost/ost-sink.cooja",
    "orchestra": "orchestra/orchestra-sink.cooja",
    "minconf":   "minconf/minconf-sink.cooja",
}

SIMULATION_DURATION_S = 3600
TX_RANGE_M   = 50.0
INT_RANGE_M  = 100.0


def mote_xml(node_id, x_m, y_m, firmware, mote_type_id, is_sink=False):
    """Generate <mote> XML block for a single node."""
    # Cooja uses pixels; 1 pixel = 1 metre in ContikiMoteType
    interface_configs = ""
    if is_sink:
        interface_configs = """
      <interface_config>
        org.contikios.cooja.interfaces.Position
        <x>{x}</x><y>{y}</y><z>0.0</z>
      </interface_config>
      <interface_config>
        org.contikios.cooja.mspmote.interfaces.MspMoteID
        <id>{nid}</id>
      </interface_config>""".format(x=x_m, y=y_m, nid=node_id)
    else:
        interface_configs = """
      <interface_config>
        org.contikios.cooja.interfaces.Position
        <x>{x}</x><y>{y}</y><z>0.0</z>
      </interface_config>
      <interface_config>
        org.contikios.cooja.mspmote.interfaces.MspMoteID
        <id>{nid}</id>
      </interface_config>""".format(x=x_m, y=y_m, nid=node_id)

    return """  <mote>
    <breakpoints />
    <interface_config>
      org.contikios.cooja.interfaces.Position
      <x>{x}</x>
      <y>{y}</y>
      <z>0.0</z>
    </interface_config>
    <interface_config>
      org.contikios.cooja.contikimote.interfaces.ContikiMoteID
      <id>{nid}</id>
    </interface_config>
    <motetype_identifier>{mtype}</motetype_identifier>
  </mote>""".format(x=x_m, y=y_m, nid=node_id, mtype=mote_type_id)


def generate_csc(scheduler, seed, burst_prob, burst_rate, burst_dur,
                 corr_radius, output_path, build_dir, slow_traffic=False):
    """Generate a complete Cooja .csc file."""

    sensor_bin = os.path.join(build_dir, SENSOR_BINS[scheduler])
    sink_bin   = os.path.join(build_dir, SINK_BINS[scheduler])

    slow_flag = "1" if slow_traffic else "0"

    # Mote type definitions
    sink_type_def = """  <motetype>
    org.contikios.cooja.contikimote.ContikiMoteType
    <identifier>sink_type</identifier>
    <description>DCS Sink ({sched})</description>
    <source>[CONFIG_DIR]/{sink_bin}</source>
    <commands>make -C [CONFIG_DIR] {sink_target} TARGET=cooja
      BURST_PROB={bp} BURST_RATE={br} BURST_DUR={bd}
      CORR_RADIUS={cr} SLOW_TRAFFIC={st}</commands>
    <moteinterface>org.contikios.cooja.interfaces.Position</moteinterface>
    <moteinterface>org.contikios.cooja.interfaces.Battery</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiVib</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiMoteID</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiRS232</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiBeeper</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiIPAddress</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiRadio</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiButton</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiPIR</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiClock</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiLED</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiCFS</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiLog</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiUserdataLED</moteinterface>
  </motetype>""".format(
        sched=scheduler, sink_bin=sink_bin,
        sink_target="dcs-sink" if scheduler=="dcs" else scheduler+"-sink",
        bp=burst_prob, br=burst_rate, bd=burst_dur,
        cr=corr_radius, st=slow_flag)

    sensor_type_def = """  <motetype>
    org.contikios.cooja.contikimote.ContikiMoteType
    <identifier>sensor_type</identifier>
    <description>DCS Sensor ({sched})</description>
    <source>[CONFIG_DIR]/{sensor_bin}</source>
    <commands>make -C [CONFIG_DIR] {sensor_target} TARGET=cooja
      BURST_PROB={bp} BURST_RATE={br} BURST_DUR={bd}
      CORR_RADIUS={cr} SLOW_TRAFFIC={st}</commands>
    <moteinterface>org.contikios.cooja.interfaces.Position</moteinterface>
    <moteinterface>org.contikios.cooja.interfaces.Battery</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiVib</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiMoteID</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiRS232</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiBeeper</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiIPAddress</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiRadio</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiButton</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiPIR</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiClock</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiLED</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiCFS</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiLog</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiUserdataLED</moteinterface>
  </motetype>""".format(
        sched=scheduler, sensor_bin=sensor_bin,
        sensor_target="dcs-sensor" if scheduler=="dcs" else scheduler+"-sensor",
        bp=burst_prob, br=burst_rate, bd=burst_dur,
        cr=corr_radius, st=slow_flag)

    # Generate mote instances
    motes_xml = ""
    for (nid, x, y) in NODES:
        mtype = "sink_type" if nid == 1 else "sensor_type"
        motes_xml += mote_xml(nid, x, y, None, mtype, is_sink=(nid==1)) + "\n"

    script_path = os.path.abspath(
        os.path.join(os.path.dirname(output_path), "..", "dcs-bursty.js"))

    csc = """<?xml version="1.0" encoding="UTF-8"?>
<simconf version="2022090101">
  <simulation>
    <title>DCS {sched} seed={seed} prob={bp} rate={br} dur={bd} corr={cr}</title>
    <randomseed>{seed}</randomseed>
    <motedelay_us>1000000</motedelay_us>
    <radiomedium>
      org.contikios.cooja.radiomediums.UDGM
      <transmitting_range>{tx}</transmitting_range>
      <interference_range>{ir}</interference_range>
      <success_ratio_tx>1.0</success_ratio_tx>
      <success_ratio_rx>1.0</success_ratio_rx>
    </radiomedium>
    <events>
      <logoutput>40000</logoutput>
    </events>
    <motetype>
{sink_type}
    </motetype>
    <motetype>
{sensor_type}
    </motetype>
{motes}
  </simulation>
  <plugin>
    org.contikios.cooja.plugins.SimControl
    <width>280</width><height>160</height><x>400</x><y>0</y>
  </plugin>
  <plugin>
    org.contikios.cooja.plugins.Visualizer
    <width>400</width><height>400</height><x>0</x><y>0</y>
  </plugin>
  <plugin>
    org.contikios.cooja.plugins.LogListener
    <width>1200</width><height>300</height><x>0</x><y>400</y>
    <plugin_config>
      <filter />
      <formatted_time />
      <coloring />
    </plugin_config>
  </plugin>
  <plugin>
    org.contikios.cooja.plugins.ScriptRunner
    <plugin_config>
      <script>{script}</script>
      <active>true</active>
    </plugin_config>
    <width>600</width><height>700</height><x>700</x><y>0</y>
  </plugin>
</simconf>
""".format(
        sched=scheduler,
        seed=seed,
        bp=burst_prob, br=burst_rate, bd=burst_dur, cr=corr_radius,
        tx=TX_RANGE_M, ir=INT_RANGE_M,
        sink_type=sink_type_def,
        sensor_type=sensor_type_def,
        motes=motes_xml,
        script=script_path,
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        f.write(csc)
    print(f"Generated: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate Cooja .csc files")
    parser.add_argument("--scheduler", choices=["dcs","ost","orchestra","minconf"],
                        required=True)
    parser.add_argument("--seed",        type=int, default=1001)
    parser.add_argument("--burst-prob",  type=int, default=5)
    parser.add_argument("--burst-rate",  type=int, default=10)
    parser.add_argument("--burst-dur",   type=int, default=2)
    parser.add_argument("--corr",        type=int, default=0,
                        help="Spatial correlation radius in metres")
    parser.add_argument("--slow",        action="store_true",
                        help="Use slow (30s) normal interval for Orch/MinConf")
    parser.add_argument("--output",      required=True,
                        help="Output .csc path")
    parser.add_argument("--build-dir",   default="build",
                        help="Directory containing compiled .cooja binaries")
    args = parser.parse_args()

    generate_csc(
        scheduler   = args.scheduler,
        seed        = args.seed,
        burst_prob  = args.burst_prob,
        burst_rate  = args.burst_rate,
        burst_dur   = args.burst_dur,
        corr_radius = args.corr,
        output_path = args.output,
        build_dir   = args.build_dir,
        slow_traffic= args.slow,
    )


if __name__ == "__main__":
    main()
