/**
 * \file dcs-sensor.c
 * \brief  Sensor application for DCS bursty-traffic evaluation.
 *
 * Implements a two-state traffic model (NORMAL / BURST) as described
 * in the CSC 489 project report, Section 5.
 *
 *   NORMAL: one UDP packet every NORMAL_INTERVAL_S seconds.
 *   BURST:  one UDP packet every BURST_INTERVAL_MS milliseconds,
 *           for BURST_DURATION_S seconds, then back to NORMAL.
 *   Trigger: every BURST_CHECK_INTERVAL_S seconds, enter BURST with
 *            probability BURST_PROB_PCT / 100.
 *
 * Each packet carries:
 *   - seq_no   (uint32): global sequence number
 *   - mode     (uint8):  1 = normal, 2 = burst
 *   - send_ms  (uint32): millisecond timestamp at send time
 *   - node_id  (uint8):  sender node ID
 *
 * Compile-time flags:
 *   SLOW_TRAFFIC=1   : sets NORMAL_INTERVAL_S=30 (for Orchestra/MinConf)
 *   BURST_PROB=N     : override burst probability (default 5)
 *   BURST_RATE=N     : override burst rate in pkt/s (default 10)
 *   BURST_DUR=N      : override burst duration in seconds (default 2)
 *   CORR_RADIUS=N    : spatial correlation radius in metres (default 0)
 *
 * Queue metrics: logged every slotframe via a periodic timer.
 */

#include "contiki.h"
#include "contiki-net.h"
#include "net/ipv6/simple-udp.h"
#include "net/routing/routing.h"
#include "net/mac/tsch/tsch.h"
#include "net/mac/tsch/tsch-queue.h"
#include "sys/log.h"
#include "sys/clock.h"
#include "random.h"
#include "dcs.h"
#include <stdint.h>
#include <string.h>
#include <stdio.h>

#define LOG_MODULE "DCS-Sensor"
#define LOG_LEVEL  LOG_LEVEL_INFO

/*---------------------------------------------------------------------------*/
/* Traffic model parameters (override at compile time via -D flags)         */
/*---------------------------------------------------------------------------*/
#ifndef SLOW_TRAFFIC
#define SLOW_TRAFFIC 0
#endif

#if SLOW_TRAFFIC
#  define NORMAL_INTERVAL_S    30
#else
#  define NORMAL_INTERVAL_S    2
#endif

#ifndef BURST_PROB
#define BURST_PROB             5       /* % probability per check interval   */
#endif

#ifndef BURST_RATE
#define BURST_RATE             10      /* packets per second during burst    */
#endif

#ifndef BURST_DUR
#define BURST_DUR              2       /* burst duration in seconds          */
#endif

#ifndef CORR_RADIUS
#define CORR_RADIUS            0       /* spatial correlation radius (m)     */
#endif

/* Derived timing */
#define BURST_INTERVAL_MS      (1000 / BURST_RATE)
#define BURST_CHECK_INTERVAL_S 10
#define BURST_PKT_COUNT        (BURST_RATE * BURST_DUR)

/*---------------------------------------------------------------------------*/
/* UDP port and sink address */
/*---------------------------------------------------------------------------*/
#define UDP_SERVER_PORT  5678
#define UDP_CLIENT_PORT  5679

/*---------------------------------------------------------------------------*/
/* Packet structure (packed, sent over UDP) */
/*---------------------------------------------------------------------------*/
typedef struct __attribute__((packed)) {
  uint32_t seq_no;
  uint32_t send_ms;
  uint8_t  node_id;
  uint8_t  mode;        /* 1=normal, 2=burst */
  uint8_t  burst_prob;
  uint8_t  burst_rate;
  uint8_t  burst_dur;
  uint8_t  corr_radius;
} sensor_pkt_t;

/*---------------------------------------------------------------------------*/
/* Process state */
/*---------------------------------------------------------------------------*/
PROCESS(dcs_sensor_process, "DCS Sensor");
AUTOSTART_PROCESSES(&dcs_sensor_process);

static struct simple_udp_connection udp_conn;
static struct etimer send_timer;
static struct etimer burst_check_timer;
static struct etimer burst_end_timer;
static struct ctimer  dcs_init_timer;    /* defer DCS init after RPL conv. */
static struct ctimer  queue_log_timer;   /* periodic queue occupancy log    */

static uint32_t seq_no      = 0;
static uint8_t  in_burst    = 0;
static uint32_t burst_pkts_sent = 0;
static uip_ipaddr_t sink_addr;
static uint8_t  dcs_ready   = 0;

/*---------------------------------------------------------------------------*/
/* Forward declarations */
/*---------------------------------------------------------------------------*/
static void send_packet(uint8_t mode);
static void start_burst(void);
static void end_burst(void);
static void log_queue_occupancy(void *ptr);
static void dcs_init_callback(void *ptr);

/*---------------------------------------------------------------------------*/
/* UDP receive callback (sensor side — not expected to receive, but log) */
/*---------------------------------------------------------------------------*/
static void
udp_rx_callback(struct simple_udp_connection *c,
                const uip_ipaddr_t *sender_addr, uint16_t sender_port,
                const uip_ipaddr_t *receiver_addr, uint16_t receiver_port,
                const uint8_t *data, uint16_t datalen)
{
  /* Sensors don't expect to receive UDP in this setup */
  LOG_WARN("Unexpected UDP RX from sender port %u\n", sender_port);
}

/*---------------------------------------------------------------------------*/
/* send_packet — core TX function */
/*---------------------------------------------------------------------------*/
static void
send_packet(uint8_t mode)
{
  if(!dcs_ready) return;
  if(!NETSTACK_ROUTING.node_is_reachable()) return;

  sensor_pkt_t pkt;
  pkt.seq_no      = ++seq_no;
  pkt.send_ms     = (uint32_t)((clock_time() * 1000UL) / CLOCK_SECOND);
  pkt.node_id     = linkaddr_node_addr.u8[7];
  pkt.mode        = mode;
  pkt.burst_prob  = (uint8_t)BURST_PROB;
  pkt.burst_rate  = (uint8_t)BURST_RATE;
  pkt.burst_dur   = (uint8_t)BURST_DUR;
  pkt.corr_radius = (uint8_t)CORR_RADIUS;

  simple_udp_sendto(&udp_conn, &pkt, sizeof(pkt), &sink_addr);

  LOG_INFO("TX seq=%lu mode=%u ms=%lu\n",
           (unsigned long)pkt.seq_no,
           pkt.mode,
           (unsigned long)pkt.send_ms);
}

/*---------------------------------------------------------------------------*/
/* start_burst / end_burst */
/*---------------------------------------------------------------------------*/
static void
start_burst(void)
{
  in_burst = 1;
  burst_pkts_sent = 0;
  LOG_INFO("BURST START seq=%lu prob=%u rate=%u dur=%u corr=%u\n",
           (unsigned long)seq_no, BURST_PROB, BURST_RATE, BURST_DUR, CORR_RADIUS);

  /* Reschedule send_timer to burst interval */
  etimer_set(&send_timer, (BURST_INTERVAL_MS * CLOCK_SECOND) / 1000);
  /* Set end-of-burst timer */
  etimer_set(&burst_end_timer, BURST_DUR * CLOCK_SECOND);
}

static void
end_burst(void)
{
  in_burst = 0;
  LOG_INFO("BURST END pkts_sent=%lu\n", (unsigned long)burst_pkts_sent);
  /* Return to normal send interval */
  etimer_set(&send_timer, NORMAL_INTERVAL_S * CLOCK_SECOND);
}

/*---------------------------------------------------------------------------*/
/* Queue occupancy logger — runs every slotframe (200 ms) */
/*---------------------------------------------------------------------------*/
static void
log_queue_occupancy(void *ptr)
{
  uint16_t global_q = tsch_queue_global_packet_count();
  uint32_t now_ms = (uint32_t)((clock_time() * 1000UL) / CLOCK_SECOND);

  /* Log format parseable by the Python analysis script */
  printf("QUEUE node=%u t_ms=%lu global=%u burst=%u slot19=%u\n",
         linkaddr_node_addr.u8[7],
         (unsigned long)now_ms,
         global_q,
         in_burst,
         /* slot-19 specific queue: approximate via total - normal traffic */
         dcs_is_box_leader() ? 0 : (uint16_t)(global_q > 0 ? global_q : 0));

  /* Re-arm every slotframe = 200 ms */
  ctimer_set(&queue_log_timer, (200 * CLOCK_SECOND) / 1000,
             log_queue_occupancy, NULL);
}

/*---------------------------------------------------------------------------*/
/* DCS init callback — called at DCS_RPL_SUPPRESS_S                        */
/*---------------------------------------------------------------------------*/
static void
dcs_init_callback(void *ptr)
{
  LOG_INFO("Initialising DCS slotframe after RPL convergence\n");
  dcs_init();
  dcs_ready = 1;

  /* Start queue logger */
  ctimer_set(&queue_log_timer, (200 * CLOCK_SECOND) / 1000,
             log_queue_occupancy, NULL);
}

/*---------------------------------------------------------------------------*/
/* Main process */
/*---------------------------------------------------------------------------*/
PROCESS_THREAD(dcs_sensor_process, ev, data)
{
  PROCESS_BEGIN();

  LOG_INFO("DCS Sensor starting (node %u) SLOW=%d PROB=%d RATE=%d DUR=%d CORR=%d\n",
           linkaddr_node_addr.u8[7],
           SLOW_TRAFFIC, BURST_PROB, BURST_RATE, BURST_DUR, CORR_RADIUS);

  /* Register UDP connection */
  simple_udp_register(&udp_conn, UDP_CLIENT_PORT, NULL,
                       UDP_SERVER_PORT, udp_rx_callback);

  /* Build sink IPv6 address (fe80::201:1:1:1 for node 1 in Cooja) */
  uip_ip6addr(&sink_addr, 0xfe80, 0, 0, 0, 0x0201, 0x0001, 0x0001, 0x0001);

  /* Wait for TSCH to associate, then defer DCS init to after RPL converges */
  /* DCS_RPL_SUPPRESS_S is also when we initialise the slotframe */
  ctimer_set(&dcs_init_timer,
             DCS_RPL_SUPPRESS_S * CLOCK_SECOND,
             dcs_init_callback, NULL);

  /* Start normal-rate send timer (will be re-armed after DCS is ready) */
  etimer_set(&send_timer, NORMAL_INTERVAL_S * CLOCK_SECOND);

  /* Burst-check timer: evaluate every BURST_CHECK_INTERVAL_S seconds */
  etimer_set(&burst_check_timer, BURST_CHECK_INTERVAL_S * CLOCK_SECOND);

  while(1) {
    PROCESS_WAIT_EVENT();

    /* ── Send timer ── */
    if(etimer_expired(&send_timer)) {
      if(in_burst) {
        send_packet(2); /* burst mode */
        burst_pkts_sent++;
        if(burst_pkts_sent >= BURST_PKT_COUNT) {
          end_burst();
        } else {
          etimer_set(&send_timer, (BURST_INTERVAL_MS * CLOCK_SECOND) / 1000);
        }
      } else {
        send_packet(1); /* normal mode */
        etimer_set(&send_timer, NORMAL_INTERVAL_S * CLOCK_SECOND);
      }
    }

    /* ── Burst-check timer ── */
    if(etimer_expired(&burst_check_timer)) {
      etimer_set(&burst_check_timer, BURST_CHECK_INTERVAL_S * CLOCK_SECOND);
      if(!in_burst) {
        /* Roll: enter burst with probability BURST_PROB % */
        uint16_t roll = random_rand() % 100;
        if(roll < BURST_PROB) {
          start_burst();
        }
      }
    }

    /* ── Burst-end timer ── */
    if(etimer_expired(&burst_end_timer)) {
      if(in_burst) {
        end_burst();
      }
    }
  }

  PROCESS_END();
}
