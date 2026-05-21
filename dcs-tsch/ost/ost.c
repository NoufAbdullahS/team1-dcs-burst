/**
 * \file ost.c
 * \brief  OST implementation for Contiki-NG v5.1 / Cooja
 *
 * See ost.h for design notes.
 */

#include "ost.h"
#include "contiki.h"
#include "sys/log.h"
#include "sys/clock.h"
#include "net/mac/tsch/tsch.h"
#include "net/mac/tsch/tsch-schedule.h"
#include "net/mac/tsch/tsch-queue.h"
#include "net/ipv6/uip-ds6-nbr.h"
#include "net/routing/rpl-lite/rpl.h"
#include <string.h>
#include <stdio.h>

#define LOG_MODULE "OST"
#define LOG_LEVEL  LOG_LEVEL_INFO

/*---------------------------------------------------------------------------*/
/* Per-neighbor adaptive cell state */
/*---------------------------------------------------------------------------*/
#define OST_MAX_NEIGHBORS 8

typedef struct {
  linkaddr_t  addr;
  uint8_t     extra_cells;   /* currently allocated extra dedicated cells   */
  uint8_t     next_slot;     /* next slot index to use for a new extra cell  */
  uint8_t     valid;
} ost_nbr_state_t;

static ost_nbr_state_t ost_neighbors[OST_MAX_NEIGHBORS];
static uint8_t ost_initialised  = 0;
static uint8_t ost_total_extra  = 0;

static struct ctimer ost_monitor_timer;
static struct ctimer ost_rpl_timer;
static struct tsch_slotframe *ost_sf = NULL;

/* Dedicated channel offsets for extra cells (avoid collision with base) */
#define OST_CHOFF_BASE  5
#define OST_CHOFF_EXTRA(slot_idx) (OST_CHOFF_BASE + (slot_idx))

/*---------------------------------------------------------------------------*/
static ost_nbr_state_t *
find_nbr(const linkaddr_t *addr)
{
  for(int i = 0; i < OST_MAX_NEIGHBORS; i++) {
    if(ost_neighbors[i].valid &&
       linkaddr_cmp(&ost_neighbors[i].addr, addr)) {
      return &ost_neighbors[i];
    }
  }
  return NULL;
}

static ost_nbr_state_t *
get_or_create_nbr(const linkaddr_t *addr)
{
  ost_nbr_state_t *n = find_nbr(addr);
  if(n) return n;
  for(int i = 0; i < OST_MAX_NEIGHBORS; i++) {
    if(!ost_neighbors[i].valid) {
      memset(&ost_neighbors[i], 0, sizeof(ost_nbr_state_t));
      linkaddr_copy(&ost_neighbors[i].addr, addr);
      ost_neighbors[i].valid      = 1;
      ost_neighbors[i].next_slot  = OST_EXTRA_SLOT_BASE;
      return &ost_neighbors[i];
    }
  }
  return NULL;
}

/*---------------------------------------------------------------------------*/
static void
add_extra_cell(ost_nbr_state_t *n)
{
  if(n->extra_cells >= OST_MAX_EXTRA_CELLS) return;
  if(ost_sf == NULL) return;

  uint16_t slot  = n->next_slot++;
  uint16_t choff = OST_CHOFF_EXTRA(n->extra_cells);

  /* TX cell toward parent */
  struct tsch_link *lnk_tx =
    tsch_schedule_add_link(ost_sf,
                           LINK_OPTION_TX,
                           LINK_TYPE_NORMAL,
                           &n->addr,
                           slot, choff, 1);
  /* Matching RX cell (will be added on parent side via same logic) */
  struct tsch_link *lnk_rx =
    tsch_schedule_add_link(ost_sf,
                           LINK_OPTION_RX,
                           LINK_TYPE_NORMAL,
                           &tsch_broadcast_address,
                           slot, choff, 1);

  if(lnk_tx && lnk_rx) {
    n->extra_cells++;
    ost_total_extra++;
    LOG_INFO("Added extra cell slot=%u choff=%u (nbr extra=%u total=%u)\n",
             slot, choff, n->extra_cells, ost_total_extra);
  }
}

static void
del_extra_cell(ost_nbr_state_t *n)
{
  if(n->extra_cells == 0 || ost_sf == NULL) return;

  n->extra_cells--;
  n->next_slot--;
  ost_total_extra = (ost_total_extra > 0) ? ost_total_extra - 1 : 0;

  uint16_t slot  = n->next_slot;
  uint16_t choff = OST_CHOFF_EXTRA(n->extra_cells);

  tsch_schedule_remove_link_by_timeslot(ost_sf, slot, choff);
  LOG_INFO("Removed extra cell slot=%u (nbr extra=%u total=%u)\n",
           slot, n->extra_cells, ost_total_extra);
}

/*---------------------------------------------------------------------------*/
/* Queue monitor — runs every OST_MONITOR_INTERVAL_MS */
/*---------------------------------------------------------------------------*/
static void
ost_monitor_callback(void *ptr)
{
  /* Check each known neighbour's queue depth */
  uip_ds6_nbr_t *ds6_nbr;
  for(ds6_nbr = uip_ds6_nbr_head();
      ds6_nbr != NULL;
      ds6_nbr = uip_ds6_nbr_next(ds6_nbr)) {

    linkaddr_t *ll = uip_ds6_nbr_get_ll(ds6_nbr);
    if(ll == NULL) continue;

    struct tsch_neighbor *tsch_nbr = tsch_queue_get_nbr(ll);
    if(tsch_nbr == NULL) continue;

    uint16_t q_depth = tsch_queue_packet_count(tsch_nbr);
    ost_nbr_state_t *n = get_or_create_nbr(ll);
    if(n == NULL) continue;

    /* Log queue state */
    printf("OST_QUEUE nbr=%u q=%u extra=%u t_ms=%lu\n",
           ll->u8[7], q_depth, n->extra_cells,
           (unsigned long)((clock_time() * 1000UL) / CLOCK_SECOND));

    if(q_depth >= OST_ADD_THRESHOLD) {
      add_extra_cell(n);
    } else if(q_depth <= OST_DEL_THRESHOLD && n->extra_cells > 0) {
      del_extra_cell(n);
    }
  }

  /* Re-arm */
  ctimer_set(&ost_monitor_timer,
             (OST_MONITOR_INTERVAL_MS * CLOCK_SECOND) / 1000,
             ost_monitor_callback, NULL);
}

/*---------------------------------------------------------------------------*/
/* RPL suppress */
/*---------------------------------------------------------------------------*/
static void
ost_rpl_suppress_callback(void *ptr)
{
  ost_suppress_rpl();
}

void
ost_suppress_rpl(void)
{
  LOG_INFO("Suppressing RPL (t=%lu s)\n",
           (unsigned long)(clock_time() / CLOCK_SECOND));
  if(rpl_dag_root_is_root()) {
    rpl_dag_root_set_preference(255);
  }
}

/*---------------------------------------------------------------------------*/
/* ost_init */
/*---------------------------------------------------------------------------*/
void
ost_init(void)
{
  if(ost_initialised) return;
  memset(ost_neighbors, 0, sizeof(ost_neighbors));

  tsch_schedule_remove_all();

  /* Base slotframe: 3 slots */
  /* We use a large slotframe so extra cells have room to grow */
  ost_sf = tsch_schedule_add_slotframe(OST_SLOTFRAME_HANDLE, 12);
  if(ost_sf == NULL) {
    LOG_ERR("Failed to create OST slotframe\n");
    return;
  }

  /* Slot 0: EB (shared) */
  tsch_schedule_add_link(ost_sf,
    LINK_OPTION_TX | LINK_OPTION_RX | LINK_OPTION_SHARED,
    LINK_TYPE_ADVERTISING, &tsch_broadcast_address, 0, 0, 1);

  /* Slot 1: shared TX (base rate) */
  tsch_schedule_add_link(ost_sf,
    LINK_OPTION_TX | LINK_OPTION_SHARED,
    LINK_TYPE_NORMAL, &tsch_broadcast_address, 1, 1, 1);

  /* Slot 2: shared RX */
  tsch_schedule_add_link(ost_sf,
    LINK_OPTION_RX,
    LINK_TYPE_NORMAL, &tsch_broadcast_address, 2, 2, 1);

  /* Start queue monitor */
  ctimer_set(&ost_monitor_timer,
             (OST_MONITOR_INTERVAL_MS * CLOCK_SECOND) / 1000,
             ost_monitor_callback, NULL);

  /* RPL suppress timer */
  ctimer_set(&ost_rpl_timer,
             OST_RPL_SUPPRESS_S * CLOCK_SECOND,
             ost_rpl_suppress_callback, NULL);

  ost_initialised = 1;
  LOG_INFO("OST initialised: base=%u slots, max extra=%u per link\n",
           OST_BASE_SLOTFRAME_SIZE, OST_MAX_EXTRA_CELLS);
}

/*---------------------------------------------------------------------------*/
uint8_t
ost_get_extra_cell_count(void)
{
  return ost_total_extra;
}
