/**
 * dcs-bursty.js — Cooja ScriptRunner script for DCS bursty-traffic evaluation
 *
 * Usage: Load this file in Cooja → Tools → Simulation Scripts → Run
 * The simulation runs for SIMULATION_DURATION_S simulated seconds, then
 * writes all mote output to the log file and calls TEST OK.
 *
 * Designed for the 24-node 2pb topology (see dcs_node_positions[] in dcs.c).
 *
 * IMPORTANT: This script is loaded by run_experiments.py automatically.
 * You do not need to run it manually.
 *
 * Configuration variables (set by run_experiments.py before loading):
 *   SIMULATION_DURATION_S  — simulated seconds (default: 3600)
 *   SEED                   — random seed for Cooja (set in .csc file)
 */

/* ── Configuration ────────────────────────────────────────────────────────── */
var SIMULATION_DURATION_S = 3600;
var LOG_FILE = "cooja_output.log";

/* ── State ────────────────────────────────────────────────────────────────── */
var started   = false;
var startTime = 0;

/* ── Helper: ms to simulated seconds ─────────────────────────────────────── */
function sim_s() {
  return sim.getSimulationTimeMillis() / 1000000.0;  /* Cooja uses µs */
}

/* ── Main loop ────────────────────────────────────────────────────────────── */
TIMEOUT(((SIMULATION_DURATION_S + 60) * 1000000), function() {
  /* This runs if the simulation doesn't finish on time */
  log.log("TIMEOUT reached at " + sim_s().toFixed(1) + " s\n");
  log.testFailed();
});

/* Wait for the simulation to progress */
while(true) {
  YIELD();

  if(!started && sim_s() > 0) {
    started = true;
    startTime = sim_s();
    log.log("Simulation started at t=" + sim_s().toFixed(3) + " s\n");
  }

  /* Print mote output as it arrives */
  if(msg !== null && msg.length > 0) {
    /* msg is the current mote's output line */
    log.log("[" + id + "] " + sim_s().toFixed(3) + " " + msg + "\n");
  }

  /* Check for end condition */
  if(sim_s() >= SIMULATION_DURATION_S) {
    log.log("TEST OK at t=" + sim_s().toFixed(1) + " s\n");
    log.testOK();
    break;
  }
}
