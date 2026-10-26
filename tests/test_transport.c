#include "cdc_stream_rx.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static uint8_t source[256*73], store[256*20], out[256];
static void fragmented(size_t chunk){
    struct hm_rx q;assert(!hm_rx_init(&q,store,20));
    size_t sent=0,received=0;
    while(sent<sizeof(source)){
        size_t n=sizeof(source)-sent;if(n>chunk)n=chunk;
        size_t wrote=hm_rx_feed(&q,source+sent,n);sent+=wrote;
        assert(wrote || q.count);
        while(hm_rx_pop(&q,out)){
            assert(!memcmp(out,source+received,256));received+=256;
        }
    }
    assert(received==sizeof(source));assert(!q.partial);
    assert(hm_rx_room(&q)==sizeof(store));
}
int main(void){
    for(size_t i=0;i<sizeof(source);i++)source[i]=(uint8_t)(i*19u+i/256u);
    const size_t chunks[]={1,63,64,127,128,255,256,257,511,512,2048,8192};
    for(unsigned i=0;i<sizeof(chunks)/sizeof(chunks[0]);i++)fragmented(chunks[i]);
    struct hm_rx q;assert(!hm_rx_init(&q,store,20));
    /* Full queue returns exact consumed prefix without overwriting old frames. */
    assert(hm_rx_feed(&q,source,sizeof(source))==sizeof(store));
    assert(hm_rx_room(&q)==0);assert(hm_rx_feed(&q,source,256)==0);
    for(unsigned i=0;i<20;i++){assert(hm_rx_pop(&q,out));assert(!memcmp(out,source+i*256,256));}
    assert(!hm_rx_pop(&q,out));
    /* Partial last slot, backpressure, then resume the unconsumed suffix. */
    assert(!hm_rx_init(&q,store,1));
    assert(hm_rx_feed(&q,source,63)==63);assert(hm_rx_room(&q)==193);
    assert(!hm_rx_pop(&q,out));assert(hm_rx_feed(&q,source+63,512)==193);
    assert(hm_rx_pop(&q,out));assert(!memcmp(out,source,256));
    assert(hm_rx_feed(&q,source+256,256)==256);assert(hm_rx_pop(&q,out));
    assert(!memcmp(out,source+256,256));
    assert(hm_rx_feed(&q,source,13)==13);
    assert(!hm_rx_init(&q,store,1));assert(!q.partial);assert(!hm_rx_pop(&q,out));
    assert(hm_rx_init(&q,store,0));assert(hm_rx_room(&q)==0);
    assert(hm_rx_feed(&q,source,256)==0);
    puts("CDC assembly passed: 12 chunk sizes, wraparound, full/partial queue, retained suffix, disconnect reset.");
    return 0;
}
