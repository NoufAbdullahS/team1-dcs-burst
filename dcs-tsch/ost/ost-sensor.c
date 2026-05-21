/**
 * \file ost-sensor.c
 * \brief  Sensor application for OST evaluation.
 *
 * Identical traffic model to dcs-sensor.c (two-state NORMAL/BURST).
 * Uses ost_init() instead of dcs_init() for the scheduler.
 * All compile-time flags (BURST_PROB, BURST_RATE, BURST_DUR, CORR_RADIUS)
 * are identical to dcs-sensor.c for a fair comparison.
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
#include "ost.h"
#include <stdint.h>
#include <string.h>
#include <stdio.h>

#define LOG_MODULE "OST-Sensor"
#define LOG_LEVEL  LOG_LEVEL_INFO

#define UDP_SERVER_PORT 5678
#define UDP_CLIENT_PORT 5679

/* ── Traffic parameters (same defaults as DCS sensor) ─────────────────────── */
#ifndef SLOW_TRAFFIC
#define SLOW_TRAFFIC 0
#endif
#if SLOW_TRAFFIC
#  define NORMAL_INTERVAL_S 30
#else
#  define NORMAL_INTERVAL_S 2
#endif
#ifndef BURST_PROB
#define BURST_PROB    5
#endif
#ifndef BURST_RATE
#define BURST_RATE    10
#endif
#ifndef BURST_DUR
#define BURST_DUR     2
#endif
#ifndef CORR_RADIUS
#define CORR_RADIUS   0
#endif

#define BURST_INTERVAL_MS      (1000 / BURST_RATE)
#define BURST_CHECK_INTERVAL_S 10
#define BURST_PKT_COUNT        (BURST_RATE * BURST_DUR)

typedef struct __attribute__((packed)) {
  uint32_t seq_no;
  uint32_t send_ms;
  uint8_t  node_id;
  uint8_t  mode;
  uint8_t  burst_prob;
  uint8_t  burst_rate;
  uint8_t  burst_dur;
  uint8_t  corr_radius;
} sensor_pkt_t;

PROCESS(ost_sensor_process, "OST Sensor");
AUTOSTART_PROCESSES(&ost_sensor_process);

static struct simple_udp_connection udp_conn;
static struct etimer send_timer;
static struct etimer burst_check_timer;
static struct etimer burst_end_timer;
static struct ctimer ost_init_timer;
static struct ctimer queue_log_timer;

static uint32_t seq_no       = 0;
static uint8_t  in_burst     = 0;
static uint32_t burst_pkts_sent = 0;
static uip_ipaddr_t sink_addr;
static uint8_t  ost_ready    = 0;

static void udp_rx_callback(struct simple_udp_connection *c,
  const uip_ipaddr_t *sa, uint16_t sp, const uip_ipaddr_t *ra,
  uint16_t rp, const uint8_t *data, uint16_t dlen) { (void)c;(void)sa;(void)sp;(void)ra;(void)rp;(void)data;(void)dlen; }

static void
send_packet(uint8_t mode)
{
  if(!ost_ready) return;
  if(!NETSTACK_ROUTING.node_is_reachable()) return;
  sensor_pkt_t pkt = {
    .seq_no      = ++seq_no,
    .send_ms     = (uint32_t)((clock_time() * 1000UL) / CLOCK_SECOND),
    .node_id     = linkaddr_node_addr.u8[7],
    .mode        = mode,
    .burst_prob  = BURST_PROB,
    .burst_rate  = BURST_RATE,
    .burst_dur   = BURST_DUR,
    .corr_radius = CORR_RADIUS,
  };
  simple_udp_sendto(&udp_conn, &pkt, sizeof(pkt), &sink_addr);
  printf("TX node=%u seq=%lu mode=%u ms=%lu\n",
         pkt.node_id, (unsigned long)seq_no, mode,
         (unsigned long)pkt.send_ms);
}

static void start_burst(void) {
  in_burst = 1; burst_pkts_sent = 0;
  etimer_set(&send_timer, (BURST_INTERVAL_MS * CLOCK_SECOND) / 1000);
  etimer_set(&burst_end_timer, BURST_DUR * CLOCK_SECOND);
  LOG_INFO("BURST START (OST) prob=%u rate=%u dur=%u\n", BURST_PROB, BURST_RATE, BURST_DUR);
}
static void end_burst(void) {
  in_burst = 0;
  etimer_set(&send_timer, NORMAL_INTERVAL_S * CLOCK_SECOND);
  LOG_INFO("BURST END (OST)\n");
}
static void queue_log_cb(void *ptr) {
  printf("QUEUE node=%u t_ms=%lu global=%u burst=%u extra_cells=%u\n",
         linkaddr_node_addr.u8[7],
         (unsigned long)((clock_time() * 1000UL) / CLOCK_SECOND),
         tsch_queue_global_packet_count(),
         in_burst,
         ost_get_extra_cell_count());
  ctimer_set(&queue_log_timer, (200 * CLOCK_SECOND) / 1000, queue_log_cb, NULL);
}
static void ost_init_cb(void *ptr) {
  ost_init();
  ost_ready = 1;
  ctimer_set(&queue_log_timer, (200 * CLOCK_SECOND) / 1000, queue_log_cb, NULL);
}

PROCESS_THREAD(ost_sensor_process, ev, data)
{
  PROCESS_BEGIN();
  LOG_INFO("OST Sensor node=%u\n", linkaddr_node_addr.u8[7]);
  simple_udp_register(&udp_conn, UDP_CLIENT_PORT, NULL, UDP_SERVER_PORT, udp_rx_callback);
  uip_ip6addr(&sink_addr, 0xfe80,0,0,0,0x0201,0x0001,0x0001,0x0001);
  ctimer_set(&ost_init_timer, OST_RPL_SUPPRESS_S * CLOCK_SECOND, ost_init_cb, NULL);
  etimer_set(&send_timer, NORMAL_INTERVAL_S * CLOCK_SECOND);
  etimer_set(&burst_check_timer, BURST_CHECK_INTERVAL_S * CLOCK_SECOND);

  while(1) {
    PROCESS_WAIT_EVENT();
    if(etimer_expired(&send_timer)) {
      if(in_burst) {
        send_packet(2); burst_pkts_sent++;
        if(burst_pkts_sent >= BURST_PKT_COUNT) end_burst();
        else etimer_set(&send_timer, (BURST_INTERVAL_MS * CLOCK_SECOND) / 1000);
      } else {
        send_packet(1);
        etimer_set(&send_timer, NORMAL_INTERVAL_S * CLOCK_SECOND);
      }
    }
    if(etimer_expired(&burst_check_timer)) {
      etimer_set(&burst_check_timer, BURST_CHECK_INTERVAL_S * CLOCK_SECOND);
      if(!in_burst && (random_rand() % 100) < BURST_PROB) start_burst();
    }
    if(etimer_expired(&burst_end_timer) && in_burst) end_burst();
  }
  PROCESS_END();
}
