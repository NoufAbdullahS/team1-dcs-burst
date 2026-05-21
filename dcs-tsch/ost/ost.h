/**
 * \file ost.h
 * \brief  OST: On-Demand TSCH Scheduling with Traffic-Awareness
 *
 * Implements the algorithm described in:
 *   S. Jeong et al., "OST: On-Demand TSCH Scheduling with Traffic-Awareness,"
 *   IEEE INFOCOM 2020, pp. 2195-2204.
 *
 * Mechanism:
 *   - Base schedule: 3 slots (EB, shared-TX, shared-RX)
 *   - Each node monitors per-link queue depth every slotframe.
 *   - When queue depth >= OST_ADD_THRESHOLD (50% of capacity),
 *     the node issues a 6P ADD request for OST_CELLS_PER_ADD dedicated cells.
 *   - When queue depth <= OST_DEL_THRESHOLD (25% of capacity),
 *     the node issues a 6P DELETE to release the extra cells.
 *   - Maximum OST_MAX_EXTRA_CELLS additional cells per link.
 *
 * Simplified implementation: we do not implement the full 6P negotiation
 * protocol (which requires a scheduling entity / SF0 running at each node).
 * Instead, we simulate the outcome of 6P negotiation by directly adding /
 * removing TSCH cells when the queue threshold is crossed. This matches the
 * behavior described in the OST paper and is appropriate for Cooja simulation
 * where all nodes share a coordinated time base.
 *
 * Team 1 — CSC 489 Final Project
 */

#ifndef OST_H
#define OST_H

#include "contiki.h"
#include "net/mac/tsch/tsch.h"
#include "net/mac/tsch/tsch-schedule.h"
#include "net/mac/tsch/tsch-queue.h"

/*---------------------------------------------------------------------------*/
/* OST schedule constants */
#define OST_SLOTFRAME_HANDLE    2
#define OST_BASE_SLOTFRAME_SIZE 3    /* EB + shared TX + shared RX          */

/* Adaptive cell pool: slots 3..8 (up to 6 extra cells per link)           */
#define OST_EXTRA_SLOT_BASE     3
#define OST_MAX_EXTRA_CELLS     6

/* Queue thresholds (fraction of TSCH_QUEUE_NUM_PER_NEIGHBOR = 8)          */
#define OST_QUEUE_CAPACITY      TSCH_QUEUE_NUM_PER_NEIGHBOR
#define OST_ADD_THRESHOLD       4    /* >= 50%: add cells                   */
#define OST_DEL_THRESHOLD       2    /* <= 25%: delete cells                */
#define OST_CELLS_PER_ADD       1    /* add one cell at a time              */

/* Monitor interval = 1 slotframe = 200 ms (DCS-compatible for fair comparison) */
#define OST_MONITOR_INTERVAL_MS 200

/* RPL suppression (same timing as DCS for fair comparison) */
#define OST_RPL_SUPPRESS_S      120

/*---------------------------------------------------------------------------*/
/* Public API */

/**
 * \brief  Initialise OST: build base slotframe, start queue monitor.
 *         Call after RPL convergence (at OST_RPL_SUPPRESS_S).
 */
void ost_init(void);

/**
 * \brief  Queue monitor callback (called every OST_MONITOR_INTERVAL_MS).
 *         Checks per-neighbor queue depth and adds/removes cells as needed.
 */
void ost_monitor_queues(void);

/**
 * \brief  Suppress RPL after convergence (same as DCS).
 */
void ost_suppress_rpl(void);

/**
 * \brief  Return the current total number of extra cells allocated.
 */
uint8_t ost_get_extra_cell_count(void);

#endif /* OST_H */
