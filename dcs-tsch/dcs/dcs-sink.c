/**
 * \file dcs-sink.c
 * \brief  Sink (RPL root / data collector) for DCS bursty-traffic evaluation.
 *
 * Listens on UDP port 5678. For every received packet logs:
 *   RX node_id seq_no mode send_ms recv_ms delay_ms
 *
 * Also acts as RPL root and TSCH coordinator (PAN coordinator role).
 * Builds the DCS slotframe at t=DCS_RPL_SUPPRESS_S, same as sensors.
 *
 * The Python parser (tools/parse_log.py) reads these log lines to compute
 * PDR, B-PDR, delay CDFs, and queue occupancy statistics.
 */

#include "contiki.h"
#include "contiki-net.h"
#include "net/ipv6/simple-udp.h"
#include "net/routing/routing.h"
#include "net/routing/rpl-lite/rpl-dag-root.h"
#include "net/mac/tsch/tsch.h"
#include "sys/log.h"
#include "sys/clock.h"
#include "dcs.h"
#include <stdint.h>
#include <string.h>
#include <stdio.h>

#define LOG_MODULE "DCS-Sink"
#define LOG_LEVEL  LOG_LEVEL_INFO

#define UDP_SERVER_PORT  5678
#define UDP_CLIENT_PORT  5679

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

PROCESS(dcs_sink_process, "DCS Sink");
AUTOSTART_PROCESSES(&dcs_sink_process);

static struct simple_udp_connection udp_conn;
static struct ctimer dcs_init_timer;
static uint32_t rx_count_normal = 0;
static uint32_t rx_count_burst  = 0;

/*---------------------------------------------------------------------------*/
static void
dcs_init_callback(void *ptr)
{
  LOG_INFO("Sink: initialising DCS slotframe\n");
  dcs_init();
}

/*---------------------------------------------------------------------------*/
static void
udp_rx_callback(struct simple_udp_connection *c,
                const uip_ipaddr_t *sender_addr, uint16_t sender_port,
                const uip_ipaddr_t *receiver_addr, uint16_t receiver_port,
                const uint8_t *data, uint16_t datalen)
{
  if(datalen < sizeof(sensor_pkt_t)) {
    LOG_WARN("Short packet: %u bytes\n", datalen);
    return;
  }

  sensor_pkt_t pkt;
  memcpy(&pkt, data, sizeof(sensor_pkt_t));

  uint32_t recv_ms = (uint32_t)((clock_time() * 1000UL) / CLOCK_SECOND);
  uint32_t delay_ms = (recv_ms >= pkt.send_ms)
                      ? (recv_ms - pkt.send_ms)
                      : 0; /* clock wrap guard */

  /* Exclude clock artifacts */
  if(delay_ms == 0 || delay_ms > 10000) {
    LOG_WARN("Excluded delay=%lu ms (node=%u seq=%lu)\n",
             (unsigned long)delay_ms,
             pkt.node_id,
             (unsigned long)pkt.seq_no);
    return;
  }

  if(pkt.mode == 1) rx_count_normal++;
  else              rx_count_burst++;

  /* Machine-readable log line for parse_log.py */
  printf("RX node=%u seq=%lu mode=%u send_ms=%lu recv_ms=%lu delay_ms=%lu "
         "prob=%u rate=%u dur=%u corr=%u\n",
         pkt.node_id,
         (unsigned long)pkt.seq_no,
         pkt.mode,
         (unsigned long)pkt.send_ms,
         (unsigned long)recv_ms,
         (unsigned long)delay_ms,
         pkt.burst_prob,
         pkt.burst_rate,
         pkt.burst_dur,
         pkt.corr_radius);

  LOG_INFO("RX node=%u seq=%lu mode=%u delay=%lu ms "
           "[total: N=%lu B=%lu]\n",
           pkt.node_id,
           (unsigned long)pkt.seq_no,
           pkt.mode,
           (unsigned long)delay_ms,
           (unsigned long)rx_count_normal,
           (unsigned long)rx_count_burst);
}

/*---------------------------------------------------------------------------*/
PROCESS_THREAD(dcs_sink_process, ev, data)
{
  PROCESS_BEGIN();

  LOG_INFO("DCS Sink starting (node %u)\n",
           linkaddr_node_addr.u8[7]);

  /* Become RPL root */
  NETSTACK_ROUTING.root_start();

  /* Register UDP listener */
  simple_udp_register(&udp_conn, UDP_SERVER_PORT, NULL,
                       UDP_CLIENT_PORT, udp_rx_callback);

  /* Schedule DCS init after RPL convergence */
  ctimer_set(&dcs_init_timer,
             DCS_RPL_SUPPRESS_S * CLOCK_SECOND,
             dcs_init_callback, NULL);

  LOG_INFO("Sink ready — waiting for sensor packets\n");

  PROCESS_WAIT_WHILE(1); /* never ends */

  PROCESS_END();
}
