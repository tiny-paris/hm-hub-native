/* Fixed-size protocol frame assembly across arbitrary USB receive boundaries.
 * CRC/command dispatch belongs in the main loop, not the receive interrupt.
 */
#include "cdc_stream_rx.h"
#include "hm_copy.h"
int hm_rx_init(struct hm_rx *q,uint8_t *storage,uint32_t slots){
    if(!q)return -1;
    q->storage=storage;q->slots=0;q->head=0;q->tail=0;q->count=0;q->partial=0;
    if(!storage || !slots || slots>UINT32_MAX/256u)return -1;
    q->slots=slots;return 0;
}
size_t hm_rx_room(const struct hm_rx *q){
    return (size_t)(q->slots-q->count)*256u-q->partial;
}
size_t hm_rx_feed(struct hm_rx *q,const uint8_t *p,size_t n){
    size_t accepted=0;
    if(!p)return 0;
    while(n && q->count<q->slots){
        size_t take=256u-q->partial;if(take>n)take=n;
        uint8_t *dst=q->storage+(size_t)q->head*256u+q->partial;
        hm_copy(dst,p,take);
        p+=take;n-=take;accepted+=take;q->partial=(uint16_t)(q->partial+take);
        if(q->partial==256){
            q->partial=0;q->count++;q->head++;
            if(q->head==q->slots)q->head=0;
        }
    }
    return accepted;
}
int hm_rx_pop(struct hm_rx *q,uint8_t out[256]){
    if(!out || !q->count)return 0;
    const uint8_t *src=q->storage+(size_t)q->tail*256u;
    hm_copy(out,src,256);
    q->tail++;if(q->tail==q->slots)q->tail=0;q->count--;return 1;
}
