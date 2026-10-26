/* Desktop test adapter; captures LCD flushes in RAM. Never accesses USB. */
#include "stream_receiver.h"
#include "cdc_stream_rx.h"
#include <string.h>
static unsigned char screen[108800], ack[256], stripe[6400];
void hm_board_stats(uint8_t data[232]){memset(data,0,64);}
static unsigned char rx_storage[20*256];
static struct hm_rx rx;
static unsigned ack_ready, entered, flushes, released;
static unsigned fail_enter, fail_flush;
int hm_display_enter(void){if(fail_enter)return -1;entered=1;return 0;}
uint8_t *hm_display_buffer(void){return stripe;}
int hm_display_leave(void){entered=0;released++;return 0;}
int hm_display_finish(void){return 0;}
int hm_display_flush(uint16_t y,uint16_t rows,const uint8_t *p){
    if(fail_flush || !entered || y+rows>170)return -1;
    memcpy(screen+y*640u,p,rows*640u);flushes++;return 0;
}
int hm_cdc_tx(const uint8_t p[256]){if(ack_ready)return -1;memcpy(ack,p,256);ack_ready=1;return 0;}
int sim_ack(uint8_t *p){if(!ack_ready)return 0;memcpy(p,ack,256);ack_ready=0;return 1;}
void sim_screen(uint8_t *p){memcpy(p,screen,108800);}
unsigned sim_flushes(void){return flushes;}
unsigned sim_released(void){return released;}
void sim_init(void){
    hm_stream_init();hm_rx_init(&rx,rx_storage,20);
    ack_ready=0;flushes=0;released=0;fail_enter=0;fail_flush=0;
    memset(screen,0,sizeof(screen));
}
void sim_fail_enter(unsigned enabled){fail_enter=enabled;}
void sim_fail_flush(unsigned enabled){fail_flush=enabled;}
void sim_send(const uint8_t p[256],uint32_t now){
    uint8_t ready[256];
    /* Every video packet takes the real assembler path with a short fragment. */
    hm_rx_feed(&rx,p,63);
    while(hm_rx_pop(&rx,ready))hm_stream_packet(ready,now);
    hm_rx_feed(&rx,p+63,193);
    while(hm_rx_pop(&rx,ready))hm_stream_packet(ready,now);
}
