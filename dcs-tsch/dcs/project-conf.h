/**
 * \file project-conf.h
 * \brief  Contiki-NG project configuration for DCS evaluation.
 *
 * These settings are shared by sensor and sink binaries.
 * Override individual settings with -D flags in the Makefile.
 */

#ifndef PROJECT_CONF_H
#define PROJECT_CONF_H

/*---------------------------------------------------------------------------*/
/* TSCH configuration */
/*---------------------------------------------------------------------------*/
/* Use software-only mote type (ContikiMoteType) in Cooja */
#define TSCH_CONF_DEFAULT_TIMESLOT_TIMING tsch_timeslot_timing_us_10000

/* Allow TSCH to auto-start after first EB received (sensors) */
#define TSCH_CONF_AUTOSTART 0  /* we control start manually after RPL conv. */

/* TSCH queue: 8 packets per neighbor (matches paper assumption) */
#define TSCH_QUEUE_NUM_PER_NEIGHBOR 8

/* MAC retries */
#define TSCH_MAC_MAX_FRAME_RETRIES 3

/* 16 channels (IEEE 802.15.4 default) */
#define TSCH_CONF_DEFAULT_HOPPING_SEQUENCE TSCH_HOPPING_SEQUENCE_4_4

/* Disable TSCH keep-alive (reduces control traffic noise) */
#define TSCH_CONF_KEEPALIVE_TIMEOUT 0

/* EB period (slotframe 0, size 397 — Orchestra default, kept for compat) */
#define TSCH_SCHEDULE_CONF_DEFAULT_LENGTH 1

/*---------------------------------------------------------------------------*/
/* RPL configuration */
/*---------------------------------------------------------------------------*/
/* Allow RPL to form the tree; we silence it at t=120 s from application */
#define RPL_CONF_WITH_DAO_ACK         0   /* no DAO ack needed in simulation */
#define RPL_CONF_DIO_INTERVAL_MIN     12  /* 2^12 ms = ~4 s initial DIO      */
#define RPL_CONF_DIO_INTERVAL_DOUBLINGS 8 /* trickle max interval ~17 min   */
#define RPL_CONF_DAO_LATENCY          (CLOCK_SECOND * 4)

/*---------------------------------------------------------------------------*/
/* Network / IPv6 */
/*---------------------------------------------------------------------------*/
#define UIP_CONF_BUFFER_SIZE    256
#define UIP_CONF_UDP            1
#define NETSTACK_CONF_WITH_IPV6 1

/*---------------------------------------------------------------------------*/
/* Logging */
/*---------------------------------------------------------------------------*/
#define LOG_CONF_LEVEL_RPL        LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_TCPIP      LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_IPV6       LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_6LOWPAN    LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_MAC        LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_FRAMER     LOG_LEVEL_WARN
/* Keep DCS and sensor/sink logs visible */
#define LOG_CONF_LEVEL_MAIN       LOG_LEVEL_INFO

/*---------------------------------------------------------------------------*/
/* Serial output: use printf (Cooja captures stdout) */
/*---------------------------------------------------------------------------*/
#define SLIP_CONF_STDOUT          1

#endif /* PROJECT_CONF_H */
