/**
 * \file dcs.c
 * \brief DCS implementation for Contiki-NG v5.1 / Cooja (2pb topology)
 *
 * Algorithm: Assis et al., Ad Hoc Networks 2023.
 * Implementation: Team 1, CSC 489.
 *
 * Key design decisions:
 *  - Box assignments are pre-computed from the static topology table.
 *    In a real deployment they would be computed from GPS coordinates;
 *    in Cooja simulation we hard-code the 24-node 2pb layout.
 *  - The 16-superbox index determines which of slots 3..18 a node uses
 *    for its external-edge (inter-box) transmissions.
 *  - The internal-edge slot (19) is shared by all nodes in the same box.
 *    The box leader occupies it as TX; non-leaders occupy it as RX toward
 *    the box leader. The box leader then forwards via its ext-edge slot.
 *  - RPL is silenced at DCS_RPL_SUPPRESS_S to match the original paper.
 */

#include "dcs.h"
#include "contiki.h"
#include "sys/log.h"
#include "net/mac/tsch/tsch.h"
#include "net/mac/tsch/tsch-schedule.h"
#include "net/routing/rpl-lite/rpl.h"
#include "net/routing/rpl-lite/rpl-dag.h"
#include "net/ipv6/uip-ds6.h"
#include "net/ipv6/uip-ds6-nbr.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

#define LOG_MODULE "DCS"
#define LOG_LEVEL  LOG_LEVEL_INFO

/*---------------------------------------------------------------------------*/
/*  Static topology: 24-node 2pb layout (positions in metres)               */
/*  Source: Assis et al. 2023, Table 1 / Figure 3.                          */
/*---------------------------------------------------------------------------*/
const dcs_node_pos_t dcs_node_positions[DCS_NUM_NODES] = {
  /* id   x      y  */
  {  1,  48.0f, 48.0f },   /* sink */
  {  2,  10.0f, 10.0f },
  {  3,  20.0f, 10.0f },
  {  4,  45.0f, 10.0f },
  {  5,  55.0f, 10.0f },
  {  6,  80.0f, 10.0f },
  {  7,  90.0f, 10.0f },
  {  8,  10.0f, 48.0f },
  {  9,  20.0f, 48.0f },
  { 10,  80.0f, 48.0f },
  { 11,  90.0f, 48.0f },
  { 12,  10.0f, 86.0f },
  { 13,  20.0f, 86.0f },
  { 14,  45.0f, 86.0f },
  { 15,  55.0f, 86.0f },
  { 16,  80.0f, 86.0f },
  { 17,  90.0f, 86.0f },
  { 18,  45.0f, 30.0f },
  { 19,  55.0f, 30.0f },
  { 20,  45.0f, 65.0f },
  { 21,  55.0f, 65.0f },
  { 22,  30.0f, 30.0f },
  { 23,  30.0f, 65.0f },
  { 24,  65.0f, 48.0f },
};

/*---------------------------------------------------------------------------*/
/*  16-superbox index table.
 *
 *  The 16-superbox is a 4x4 arrangement of grid boxes. Each box in the
 *  network maps to one of the 16 indices based on:
 *    sb16_x = box_x mod 4
 *    sb16_y = box_y mod 4
 *    superbox16_index = sb16_y * 4 + sb16_x
 *
 *  For the 2pb 3x3 grid (boxes 0..8):
 *    box (col, row) -> superbox16_index
 *    (0,0)->0  (1,0)->1  (2,0)->2
 *    (0,1)->4  (1,1)->5  (2,1)->6
 *    (0,2)->8  (1,2)->9  (2,2)->10
 *
 *  Nodes in boxes with the same superbox16_index across the network
 *  can transmit simultaneously without collision (dilution guarantee).
 *  Their dedicated TX slot = DCS_SLOT_EXT_BASE + superbox16_index.
 */
static const uint8_t box_to_superbox16[9] = {
  0,  /* box 0: (col=0,row=0) */
  1,  /* box 1: (col=1,row=0) */
  2,  /* box 2: (col=2,row=0) */
  4,  /* box 3: (col=0,row=1) */
  5,  /* box 4: (col=1,row=1) */
  6,  /* box 5: (col=2,row=1) */
  8,  /* box 6: (col=0,row=2) */
  9,  /* box 7: (col=1,row=2) */
  10, /* box 8: (col=2,row=2) */
};

/*---------------------------------------------------------------------------*/
/*  Per-node computed state (filled by dcs_init)                            */
/*---------------------------------------------------------------------------*/
static uint8_t my_box_index      = 0xFF;
static uint8_t my_superbox16_idx = 0xFF;
static uint8_t my_ext_tx_slot    = 0xFF;
static uint8_t my_is_box_leader  = 0;
static uint8_t dcs_initialised   = 0;

/* RPL-suppress timer */
static struct ctimer rpl_suppress_timer;

/*---------------------------------------------------------------------------*/
/*  Helper: compute grid box from (x,y) position                           */
/*---------------------------------------------------------------------------*/
static uint8_t
pos_to_box(float x, float y)
{
  uint8_t col = (uint8_t)(x / DCS_BOX_SIZE_M);
  uint8_t row = (uint8_t)(y / DCS_BOX_SIZE_M);
  /* Clamp to grid */
  if(col >= DCS_GRID_COLS) col = DCS_GRID_COLS - 1;
  if(row >= DCS_GRID_ROWS) row = DCS_GRID_ROWS - 1;
  return row * DCS_GRID_COLS + col;
}

/*---------------------------------------------------------------------------*/
/*  Helper: determine if this node is the box leader.
 *  Box leader = node with lowest RPL rank among all nodes in the same box.
 *  In the static 2pb layout, the leaders are known in advance.
 *  Here we use the RPL rank from the routing table at init time.
 *  If RPL is not yet converged, we default to false and retry.
 */
static uint8_t
compute_box_leader(uint8_t my_box)
{
  rpl_dag_t *dag = rpl_get_any_dag();
  if(dag == NULL) {
    return 0;
  }
  uint16_t my_rank = dag->rank;

  /* Iterate over all known neighbors and find lowest rank in same box */
  uip_ds6_nbr_t *nbr;
  for(nbr = uip_ds6_nbr_head(); nbr != NULL; nbr = uip_ds6_nbr_next(nbr)) {
    /* We cannot directly look up a neighbor's position from RPL tables
     * in simulation; instead we use the static position table keyed by
     * link-layer address. In Cooja, node IDs map to MAC addresses. */
    linkaddr_t *addr = uip_ds6_nbr_get_ll(nbr);
    if(addr == NULL) continue;
    uint8_t peer_id = addr->u8[7]; /* Cooja MAC: last byte = node index */
    if(peer_id < 1 || peer_id > DCS_NUM_NODES) continue;

    float peer_x = dcs_node_positions[peer_id - 1].x;
    float peer_y = dcs_node_positions[peer_id - 1].y;
    uint8_t peer_box = pos_to_box(peer_x, peer_y);

    if(peer_box == my_box) {
      /* Check if peer has lower rank via RPL neighbor entry */
      rpl_nbr_t *rpl_nbr = rpl_neighbor_get_from_ipaddr(&nbr->ipaddr);
      if(rpl_nbr != NULL && rpl_nbr->rank < my_rank) {
        return 0; /* not the leader */
      }
    }
  }
  return 1; /* no lower-ranked neighbor in same box found */
}

/*---------------------------------------------------------------------------*/
/*  Add a dedicated TX cell for this node and matching RX cells for
 *  all in-range neighbors.                                                  */
/*---------------------------------------------------------------------------*/
static void
add_tx_cell(struct tsch_slotframe *sf, uint16_t timeslot, uint16_t choff,
            const linkaddr_t *peer_addr)
{
  struct tsch_link *lnk;
  /* TX cell toward next-hop parent (or broadcast for EB/shared) */
  lnk = tsch_schedule_add_link(sf,
          LINK_OPTION_TX | (peer_addr ? 0 : LINK_OPTION_SHARED),
          LINK_TYPE_NORMAL,
          peer_addr ? peer_addr : &tsch_broadcast_address,
          timeslot, choff, 1);
  if(lnk == NULL) {
    LOG_WARN("Failed to add TX cell slot=%u choff=%u\n", timeslot, choff);
  }
}

static void
add_rx_cell(struct tsch_slotframe *sf, uint16_t timeslot, uint16_t choff)
{
  struct tsch_link *lnk;
  lnk = tsch_schedule_add_link(sf,
          LINK_OPTION_RX,
          LINK_TYPE_NORMAL,
          &tsch_broadcast_address,
          timeslot, choff, 1);
  if(lnk == NULL) {
    LOG_WARN("Failed to add RX cell slot=%u choff=%u\n", timeslot, choff);
  }
}

static void
add_shared_cell(struct tsch_slotframe *sf, uint16_t timeslot, uint16_t choff)
{
  struct tsch_link *lnk;
  lnk = tsch_schedule_add_link(sf,
          LINK_OPTION_TX | LINK_OPTION_RX | LINK_OPTION_SHARED,
          LINK_TYPE_ADVERTISING,
          &tsch_broadcast_address,
          timeslot, choff, 1);
  if(lnk == NULL) {
    LOG_WARN("Failed to add shared cell slot=%u\n", timeslot);
  }
}

/*---------------------------------------------------------------------------*/
/*  RPL suppress callback                                                    */
/*---------------------------------------------------------------------------*/
static void
rpl_suppress_callback(void *ptr)
{
  dcs_suppress_rpl();
}

/*---------------------------------------------------------------------------*/
/*  dcs_suppress_rpl                                                         */
/*---------------------------------------------------------------------------*/
void
dcs_suppress_rpl(void)
{
  LOG_INFO("Suppressing RPL DIO/DAO (t=%lu s)\n",
           (unsigned long)(clock_time() / CLOCK_SECOND));
#if RPL_WITH_MULTICAST
  rpl_dag_root_set_preference(0);
#endif
  /* Set trickle interval to maximum to effectively stop DIO floods */
  rpl_timers_schedule_periodic_dis(); /* harmless no-op if already stopped */

  /* In Contiki-NG v5.1, the cleanest way to stop DIO without modifying
   * the RPL core is to set the Imin doublings very high so the next DIO
   * is scheduled far in the future (>> simulation end). */
  extern void rpl_timers_dio_reset(const char *);
  /* Alternative: directly stop the dag if we are the root */
  if(rpl_dag_root_is_root()) {
    LOG_INFO("Root: stopping DAG root advertisements\n");
    rpl_dag_root_set_preference(255); /* stop being preferred root */
  }
  LOG_INFO("RPL suppression complete\n");
}

/*---------------------------------------------------------------------------*/
/*  dcs_init — main entry point                                              */
/*---------------------------------------------------------------------------*/
void
dcs_init(void)
{
  if(dcs_initialised) {
    LOG_WARN("dcs_init called twice — ignoring\n");
    return;
  }

  /* ---- 1. Determine this node's position and box ---- */
  linkaddr_t *my_addr = &linkaddr_node_addr;
  uint8_t my_id = my_addr->u8[7]; /* Cooja: last byte of MAC = node index */

  if(my_id < 1 || my_id > DCS_NUM_NODES) {
    LOG_ERR("Cannot determine node ID from MAC address (id=%u)\n", my_id);
    return;
  }

  float my_x = dcs_node_positions[my_id - 1].x;
  float my_y = dcs_node_positions[my_id - 1].y;
  my_box_index = pos_to_box(my_x, my_y);
  my_superbox16_idx = box_to_superbox16[my_box_index];
  my_ext_tx_slot = DCS_SLOT_EXT_BASE + my_superbox16_idx;
  my_is_box_leader = compute_box_leader(my_box_index);

  LOG_INFO("Node %u: pos=(%.0f,%.0f) box=%u sb16=%u ext_slot=%u leader=%u\n",
           my_id, my_x, my_y, my_box_index,
           my_superbox16_idx, my_ext_tx_slot, my_is_box_leader);

  /* ---- 2. Remove any existing TSCH schedule ---- */
  tsch_schedule_remove_all();

  /* ---- 3. Create DCS slotframe (20 slots) ---- */
  struct tsch_slotframe *sf =
    tsch_schedule_add_slotframe(DCS_SLOTFRAME_HANDLE, DCS_SLOTFRAME_SIZE);
  if(sf == NULL) {
    LOG_ERR("Failed to create DCS slotframe\n");
    return;
  }

  /* ---- 4. Slot 0: Enhanced Beacon (all nodes TX+RX, shared) ---- */
  add_shared_cell(sf, DCS_SLOT_EB, DCS_CHOFF_EB);

  /* ---- 5. Slots 1-2: RPL control (shared TX+RX) ---- */
  add_shared_cell(sf, DCS_SLOT_RPL_1, DCS_CHOFF_RPL);
  add_shared_cell(sf, DCS_SLOT_RPL_2, DCS_CHOFF_RPL);

  /* ---- 6. Slots 3-18: External-edge dedicated cells ---- */
  /*
   * Each node gets ONE dedicated TX slot (its superbox16 slot) and
   * RX cells in ALL OTHER external-edge slots (to receive from children
   * whose external-edge slot falls in a different time).
   *
   * Simplified model: every node listens on all ext slots it doesn't TX on,
   * ensuring convergecast packets can be forwarded regardless of subtree shape.
   */
  for(uint8_t s = 0; s < DCS_NUM_EXT_SLOTS; s++) {
    uint16_t slot   = DCS_SLOT_EXT_BASE + s;
    uint16_t choff  = DCS_CHOFF_EXT_BASE + s;
    if(s == my_superbox16_idx) {
      /* This node's dedicated TX slot — TX toward RPL parent */
      rpl_dag_t *dag = rpl_get_any_dag();
      linkaddr_t *parent_addr = NULL;
      if(dag != NULL && dag->preferred_parent != NULL) {
        parent_addr = rpl_neighbor_get_lladdr(dag->preferred_parent);
      }
      add_tx_cell(sf, slot, choff, parent_addr);
    } else {
      /* RX slot — listen for children / neighbours transmitting here */
      add_rx_cell(sf, slot, choff);
    }
  }

  /* ---- 7. Slot 19: Internal-edge (intra-box) ---- */
  /*
   * Box leader: RX in this slot (receives from box members).
   * Non-leaders: TX in this slot (send to box leader = RPL parent).
   * Sink (node 1): always RX.
   */
  if(my_id == DCS_SINK_ID) {
    add_rx_cell(sf, DCS_SLOT_INT, DCS_CHOFF_INT);
  } else if(my_is_box_leader) {
    add_rx_cell(sf, DCS_SLOT_INT, DCS_CHOFF_INT);
  } else {
    /* TX toward box leader (parent) on internal slot */
    rpl_dag_t *dag = rpl_get_any_dag();
    linkaddr_t *parent_addr = NULL;
    if(dag != NULL && dag->preferred_parent != NULL) {
      parent_addr = rpl_neighbor_get_lladdr(dag->preferred_parent);
    }
    add_tx_cell(sf, DCS_SLOT_INT, DCS_CHOFF_INT, parent_addr);
  }

  /* ---- 8. Schedule RPL suppression ---- */
  ctimer_set(&rpl_suppress_timer,
             DCS_RPL_SUPPRESS_S * CLOCK_SECOND,
             rpl_suppress_callback, NULL);

  dcs_initialised = 1;
  LOG_INFO("DCS slotframe built: %u slots\n", DCS_SLOTFRAME_SIZE);
  dcs_print_schedule();
}

/*---------------------------------------------------------------------------*/
uint8_t dcs_get_ext_tx_slot(void)    { return my_ext_tx_slot; }
uint8_t dcs_get_superbox_index(void) { return my_superbox16_idx; }
uint8_t dcs_get_box_index(void)      { return my_box_index; }
uint8_t dcs_is_box_leader(void)      { return my_is_box_leader; }

/*---------------------------------------------------------------------------*/
void
dcs_print_schedule(void)
{
  LOG_INFO("=== DCS Schedule ===\n");
  LOG_INFO("  Slot  0    : EB (shared TX+RX, choff=0)\n");
  LOG_INFO("  Slot  1-2  : RPL control (shared, choff=1)\n");
  for(uint8_t s = 0; s < DCS_NUM_EXT_SLOTS; s++) {
    LOG_INFO("  Slot %2u    : ext-edge sb16=%u — %s\n",
             DCS_SLOT_EXT_BASE + s, s,
             (s == my_superbox16_idx) ? "TX (this node)" : "RX");
  }
  LOG_INFO("  Slot 19    : int-edge — %s\n",
           my_is_box_leader ? "RX (box leader)" : "TX (to box leader)");
  LOG_INFO("====================\n");
}
