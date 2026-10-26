/* Experimental CDC native-pixel receiver. No flash API, arbitrary address or DFU.
 * Integration source, NOT a firmware image. See PROTOCOL.md / REPORT.zh-CN.md.
 */
#include "stream_receiver.h"
#include "hm_copy.h"
#define FRAME_BYTES 108800u
#define STRIPE_BYTES 6400u
#define PAYLOAD 232u
static struct {
    uint32_t id, offset, crc, last_ms;
    uint16_t used;
    uint8_t active, receiving, slot;
    uint8_t *stripe;
} s;
static uint16_t rd16(const uint8_t *p) {return (uint16_t)(p[0]|((uint16_t)p[1]<<8));}
static uint32_t rd32(const uint8_t *p) {return rd16(p)|((uint32_t)rd16(p+2)<<16);}
static void wr16(uint8_t *p,uint16_t v){p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);}
static void wr32(uint8_t *p,uint32_t v){wr16(p,(uint16_t)v);wr16(p+2,(uint16_t)(v>>16));}
static uint32_t crc_update(uint32_t c,const uint8_t *p,uint32_t n){
#ifdef HM_USE_STOCK_CRC_TABLE
    /* Exact same IEEE table already used by pinned original 0x08018f44. */
    const uint32_t *table=(const uint32_t*)0x080379e8;
    while(n--)c=(c>>8)^table[(c^*p++)&255u];
#else
    /* 64 bytes of read-only table, two lookups per byte instead of 8 bit steps.
     * Reflection/initial/final XOR match zlib, unlike an unadapted MCU CRC unit.
     */
    static const uint32_t nibble[16]={
        0x00000000u,0x1db71064u,0x3b6e20c8u,0x26d930acu,
        0x76dc4190u,0x6b6b51f4u,0x4db26158u,0x5005713cu,
        0xedb88320u,0xf00f9344u,0xd6d6a3e8u,0xcb61b38cu,
        0x9b64c2b0u,0x86d3d2d4u,0xa00ae278u,0xbdbdf21cu};
    while(n--){c^=*p++;c=(c>>4)^nibble[c&15u];c=(c>>4)^nibble[c&15u];}
#endif
    return c;
}
static int release(void){
    int result=s.active?hm_display_leave():0;
    s.active=0;s.receiving=0;s.used=0;
    return result;
}
static void reply(uint8_t sub,uint8_t status,uint32_t id){
    uint8_t p[256]={0};
    p[0]=4;p[1]=(uint8_t)(sub|128);p[2]='H';p[3]='M';p[4]='S';p[5]='1';
    wr16(p+6,1);wr32(p+8,id);wr32(p+12,s.offset);p[19]=status;
    if(sub==1){
        wr16(p+16,8);wr16(p+20,320);wr16(p+22,170);
        p[24]=10;p[26]=1; /* RGB565 big endian */
#ifdef HM_BULK512
        p[25]=64;p[27]=3; /* window 64, joined writes + read-only stats */
#else
        p[25]=8;
#endif
    }
    if(sub==6){wr16(p+16,64);hm_board_stats(p+20);}
    wr32(p+252,~crc_update(~0u,p,252));
    if(hm_cdc_tx(p))release();
}
void hm_stream_init(void){release();s.id=0;s.offset=0;s.crc=~0u;s.last_ms=0;s.stripe=hm_display_buffer();}
int hm_stream_active(void){return s.active;}
void hm_stream_tick(uint32_t now){if(s.active && (uint32_t)(now-s.last_ms)>1000u)release();}
void hm_stream_packet(const uint8_t p[256],uint32_t now){
    if(p[0]!=4 || p[2]!='H'||p[3]!='M'||p[4]!='S'||p[5]!='1')return;
    /* Corrupt packets do not extend the lease or touch the display. */
#ifndef HM_TRUST_DISPATCH_CRC
    if(rd32(p+252)!=~crc_update(~0u,p,252))return;
#endif
    uint8_t sub=p[1];uint32_t id=rd32(p+8),off=rd32(p+12);uint16_t n=rd16(p+16);
    if(rd16(p+6)!=1 || n>PAYLOAD || (p[18]&~1u) || p[19]){
        release();reply(sub,1,id);return;
    }
    if(sub==1){ /* read-only capability query */
        if(n || off){reply(sub,1,id);return;}reply(sub,0,id);return;
    }
    if(sub==6){if(n || off){reply(sub,1,id);return;}reply(sub,0,id);return;}
    if(sub==5){int result=release();reply(sub,result?4:0,id);return;}
    if(sub==2){
        if(n || off || s.receiving){release();reply(sub,2,id);return;}
        if(!s.stripe || (!s.active && hm_display_enter())){reply(sub,4,id);return;}
        s.active=1;s.receiving=1;s.id=id;s.offset=0;s.used=0;s.slot=0;s.crc=~0u;s.last_ms=now;
        reply(sub,0,id);return;
    }
    if(!s.active || !s.receiving || id!=s.id || off!=s.offset){
        release();reply(sub,2,id);return;
    }
    if(sub==3){
        if(!n || (n&1u) || s.offset>FRAME_BYTES-n || s.offset%STRIPE_BYTES+n>STRIPE_BYTES){
            release();reply(sub,1,id);return;
        }
        /* Keep 10-row wire boundaries, but pipeline two 5-row DMA buffers.
         * A valid payload may straddle the internal half-buffer boundary. */
        s.crc=crc_update(s.crc,p+20,n);s.last_ms=now;
        const uint8_t *src=p+20;
        while(n){
            uint16_t take=(uint16_t)(HM_LCD_BYTES-s.used);if(take>n)take=n;
            uint8_t *buf=s.stripe+s.slot*HM_LCD_BYTES;
            hm_copy(buf+s.used,src,take);
            s.used=(uint16_t)(s.used+take);s.offset+=take;src+=take;n=(uint16_t)(n-take);
            if(s.used==HM_LCD_BYTES){
                uint16_t y=(uint16_t)((s.offset-HM_LCD_BYTES)/640u);
                if(hm_display_flush(y,HM_LCD_ROWS,buf)){release();reply(sub,4,id);return;}
                s.used=0;s.slot^=1u;
            }
        }
        if(p[18]&1u)reply(sub,0,id);
        return;
    }
    if(sub==4){
        if(n!=4 || s.offset!=FRAME_BYTES || s.used || rd32(p+20)!=~s.crc){
            release();reply(sub,3,id);return;
        }
        if(hm_display_finish()){release();reply(sub,4,id);return;}
        s.receiving=0;s.last_ms=now;reply(sub,0,id);return;
    }
    release();reply(sub,1,id);
}
