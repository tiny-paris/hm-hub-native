#ifndef HM_CDC_STREAM_RX_H
#define HM_CDC_STREAM_RX_H
#include <stdint.h>
#include <stddef.h>
/* Caller serializes ALL operations between USB ISR and main loop.
 * Storage is slots*256 bytes; no allocation and no peripheral access here.
 * Never rearm USB for more bytes than hm_rx_room() permits. If feed returns
 * short, retain/retry the suffix; USB ACK cannot be undone by this module.
 */
struct hm_rx {
    uint8_t *storage;
    uint32_t slots, head, tail, count;
    uint16_t partial;
};
int hm_rx_init(struct hm_rx *q, uint8_t *storage, uint32_t slots);
size_t hm_rx_room(const struct hm_rx *q);
size_t hm_rx_feed(struct hm_rx *q, const uint8_t *data, size_t bytes);
int hm_rx_pop(struct hm_rx *q, uint8_t out[256]);
#endif
