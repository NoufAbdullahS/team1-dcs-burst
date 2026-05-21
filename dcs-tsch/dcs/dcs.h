/**
 * \file dcs.h
 * \brief Dilution-based Convergecast Scheduling (DCS) for Contiki-NG v5.1
 *
 * Implements the algorithm described in:
 *   F. Assis et al., "DCS: Dilution-based Convergecast Scheduling in a
 *   TSCH network," Ad Hoc Networks, vol. 146, p. 103173, 2023.
 *
 * Topology: 2pb — 24 nodes, 3x3 grid of 12 boxes, 2 nodes per box.
 *   Box side length x = r/sqrt(2) = 35 m, transmission range r = 50 m.
 *   Slotframe: 20 slots (10 ms each = 200 ms cycle).
 *     Slots 0-2  : beacon / RPL control (shared)
 *     Slots 3-18 : external-edge dedicated TX (one per 16-superbox index)
 *     Slot 19    : internal-edge dedicated TX (shared within box)
 *
 * Team 1 — CSC 489 Final Project
 */

#ifndef DCS_H
#define DCS_H

#include "contiki.h"
#include "net/mac/tsch/tsch.h"
#include "net/mac/tsch/tsch-schedule.h"
#include "net/routing/rpl-lite/rpl.h"

/*---------------------------------------------------------------------------*/
/* Topology constants — 2pb, 24 nodes, r = 50 m */
#define DCS_TX_RANGE_M        50.0f   /* transmission range (m)             */
#define DCS_BOX_SIZE_M        35.36f  /* x = r / sqrt(2)                    */
#define DCS_GRID_COLS         3       /* grid columns                       */
#define DCS_GRID_ROWS         3       /* grid rows (3x3 = 9 boxes for 2pb) */
#define DCS_NUM_NODES         24      /* total nodes including sink          */
#define DCS_NUM_SENSORS       23      /* sensor nodes (nodes 2..24)         */
#define DCS_SINK_ID           1       /* RPL root / sink node ID            */

/*---------------------------------------------------------------------------*/
/* Slotframe parameters */
#define DCS_SLOTFRAME_HANDLE  1       /* TSCH slotframe handle              */
#define DCS_SLOTFRAME_SIZE    20      /* slots per slotframe                */
#define DCS_SLOT_DURATION_MS  10      /* ms per slot                        */

/* Slot assignments */
#define DCS_SLOT_EB           0       /* enhanced beacon                    */
#define DCS_SLOT_RPL_1        1       /* RPL DIO/DAO (shared)               */
#define DCS_SLOT_RPL_2        2       /* RPL DIO/DAO (shared)               */
#define DCS_SLOT_EXT_BASE     3       /* first external-edge slot           */
/* slots 3..18 = external edges, index = (slot - DCS_SLOT_EXT_BASE)        */
#define DCS_SLOT_INT          19      /* internal-edge (intra-box) slot     */

#define DCS_NUM_EXT_SLOTS     16      /* 16-superbox: 4x4 = 16 positions    */

/*---------------------------------------------------------------------------*/
/* Channel offsets */
#define DCS_CHOFF_EB          0
#define DCS_CHOFF_RPL         1
#define DCS_CHOFF_EXT_BASE    2       /* ext slot i uses choff (2 + i)      */
#define DCS_CHOFF_INT         18

/*---------------------------------------------------------------------------*/
/* RPL suppression: disable DIO/DAO after convergence */
#define DCS_RPL_SUPPRESS_S    120     /* seconds after boot to silence RPL  */

/*---------------------------------------------------------------------------*/
/* Node position table — indexed by (node_id - 1), metres                  */
typedef struct {
  uint8_t  node_id;
  float    x;
  float    y;
} dcs_node_pos_t;

extern const dcs_node_pos_t dcs_node_positions[DCS_NUM_NODES];

/*---------------------------------------------------------------------------*/
/* Box assignment — indexed by (node_id - 1)                               */
/* box_index = grid_y * DCS_GRID_COLS + grid_x  (0-based)                  */
typedef struct {
  uint8_t box_index;        /* 0..8 for 2pb 3x3 grid                       */
  uint8_t superbox16_index; /* 0..15 — determines external-edge TX slot    */
  uint8_t is_box_leader;    /* 1 if this node is the lowest-rank in box    */
} dcs_box_info_t;

/*---------------------------------------------------------------------------*/
/* Public API */

/**
 * \brief  Initialise DCS: compute box assignments, build TSCH slotframe.
 *         Call once after RPL has converged (typically from an event or timer).
 */
void dcs_init(void);

/**
 * \brief  Return this node's external-edge TX slot (3..18).
 */
uint8_t dcs_get_ext_tx_slot(void);

/**
 * \brief  Return the 16-superbox index for this node.
 */
uint8_t dcs_get_superbox_index(void);

/**
 * \brief  Return the box index (0..8) for this node.
 */
uint8_t dcs_get_box_index(void);

/**
 * \brief  Return 1 if this node is the box leader (lowest RPL rank in box).
 */
uint8_t dcs_is_box_leader(void);

/**
 * \brief  Suppress RPL DIO/DAO after convergence.
 *         Called by the RPL-suppress timer at DCS_RPL_SUPPRESS_S.
 */
void dcs_suppress_rpl(void);

/**
 * \brief  Print the slotframe layout to the serial log.
 */
void dcs_print_schedule(void);

#endif /* DCS_H */
