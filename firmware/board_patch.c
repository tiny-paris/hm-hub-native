/* Experimental binary adapter, pinned to official 1.3.7 SHA256.
 * Never writes MCU/external flash; updater is a separate host operation.
 * Stock DMA has internal waits: independent hardware watchdog is the final
 * escape if those waits wedge. Hardware validation is recorded separately.
 */
#include "stream_receiver.h"
#include "cdc_stream_rx.h"
#define V8(a) (*(volatile uint8_t *)(a))
#define V16(a) (*(volatile uint16_t *)(a))
#define V32(a) (*(volatile uint32_t *)(a))
#define FN(a,t) ((t)((a)|1u))
#define USB_CTX ((void*)0x20004970)
#ifdef HM_BULK512
uint8_t usb_buffer[512] __attribute__((section(".usb_rx"),aligned(512)));
#define USB_BUF usb_buffer
#define USB_ARM_BYTES 512u
extern uint32_t stock_usb_descriptor(void *,uint32_t);
uint32_t patch_usb_descriptor(void *out,uint32_t speed){
    uint32_t result=stock_usb_descriptor(out,speed);
    /* One shared RAM configuration descriptor; restore HS after FS queries. */
    V16(0x200001ad)=speed?512:64;
    V16(0x200001b4)=speed?512:64;
    return result;
}
#else
#define USB_BUF ((uint8_t*)0x20000ff0)
#define USB_ARM_BYTES 256u
#endif
#define RX_STORAGE ((uint8_t*)0x10000000)
#define TX_META ((void*)0x2001e9a8)
#define TX_BUF ((uint8_t*)0x2001e888)
static struct hm_rx rx;
static volatile uint32_t heartbeat;
static volatile uint8_t initialized, connected, armed, fault, reset_pending;
static uint8_t budget;
static uint32_t lcd_ms,hms_ms,lcd_calls,hms_calls,usb_calls,usb_bytes,usb_max;
#ifdef HM_TRIAL
static uint32_t trial_started;
static uint8_t trial_confirmed;
#endif
static uint32_t now(void){return V32(0x2001ea48);}
static uint32_t lock(void){uint32_t p;__asm volatile("mrs %0, primask\ncpsid i":"=r"(p)::"memory");return p;}
static void unlock(uint32_t p){__asm volatile("msr primask, %0"::"r"(p):"memory");}
static int ready(void){return FN(0x0801c860,int(*)(void))();}
static int wait_lcd(void){uint32_t t=now();while(!ready()){if((uint32_t)(now()-t)>50){fault=1;return -1;}}return 0;}
static void maybe_arm(void){
    if(connected && !fault && !armed && hm_rx_room(&rx)>=USB_ARM_BYTES){
        armed=1;
        FN(0x08012df4,int(*)(void*,void*))(USB_CTX,USB_BUF);
        if(FN(0x08012e24,int(*)(void*))(USB_CTX)!=1){armed=0;fault=1;}
    }
}
void patch_init(void){
    /* Invoked before USB initialization, and on connection by original path. */
    hm_rx_init(&rx,RX_STORAGE,20);
    FN(0x080357d4,void(*)(void*,void*,uint32_t,uint32_t))(TX_META,(void*)0x10001400,256,20);
#ifdef HM_TRIAL
    if(!initialized)trial_started=now();
#endif
    /* Fault is sticky until MCU reset: DTR reconnect must not return a stuck
     * DMA source to the GUI or admit another frame before watchdog recovery. */
    heartbeat=now();initialized=1;armed=1;reset_pending=1;
}
void patch_connect(uint32_t yes){
    FN(0x0801af44,void(*)(void))(); /* preserve album readback cancellation */
    connected=(uint8_t)!!yes;
    if(yes)patch_init();else reset_pending=1;
}
int patch_usb_rx(uint8_t *buf,const uint32_t *actual){
    /* Called in USB ISR. Consume before rearm. No LCD/GUI or TX here. */
    uint32_t p=lock(),n=*actual;armed=0;
    usb_calls++;usb_bytes+=n;if(n>usb_max)usb_max=n;
    if(n>USB_ARM_BYTES || hm_rx_feed(&rx,buf,n)!=n)fault=1;
    maybe_arm();unlock(p);return 0;
}
void patch_tx(void){
    /* Stock drain spins forever while USB busy; only pop when sendable. */
    if(!connected || FN(0x08036e52,int(*)(void))())return;
    if(FN(0x08035794,int(*)(void*,void*))(TX_META,TX_BUF))
        FN(0x08036eb4,void(*)(void*,uint32_t))(TX_BUF,256);
}
void patch_flash_service(void){
    /* Stock readback advances its offset before enqueue and may enqueue DATA
     * plus END in one call. The old blocking TX drain guaranteed room; our
     * nonblocking drain must reserve that room before permitting progress.
     * No Flash command is issued here: retain the original service verbatim. */
    if(V32(0x20001950) && V32(0x2001e9bc)>16)return;
    FN(0x0801ac24,void(*)(void))();
}
int patch_rx_pop(void *unused,uint8_t *out){
    (void)unused;patch_tx();if(!budget || fault)return 0;--budget;
    uint32_t p=lock();int r=hm_rx_pop(&rx,out);maybe_arm();unlock(p);return r;
}
void patch_gui(void){
    heartbeat=now();budget=8;
#ifdef HM_TRIAL
    if(!trial_confirmed && (uint32_t)(now()-trial_started)>60000){V8(0x200002ef)=0;return;}
#endif
    if(reset_pending){reset_pending=0;hm_stream_init();}
    hm_stream_tick(now());
    if(fault){V8(0x200002ef)=0;return;} /* watchdog -> stock bootloader */
    if(!hm_stream_active())FN(0x08023464,void(*)(void))();
}
void patch_gui_tick(void){
    if(!fault && !hm_stream_active())FN(0x0801ccc4,void(*)(void))();
}
void patch_watchdog(void){
    /* Original tick checks the official DFU flag before calling this hook.
     * Before adapter init retain stock startup behavior; afterward a wedged
     * main loop can no longer be kept alive indefinitely by its timer ISR. */
    if(!initialized || (uint32_t)(now()-heartbeat)<5000)
        FN(0x08037042,void(*)(void))();
}
void patch_command(void){
    const uint8_t *p=(const uint8_t*)0x2001e788;
    uint32_t t=now();hm_stream_packet(p,t);hms_ms+=now()-t;hms_calls++;
#ifdef HM_TRIAL
    /* Host sends this second HELLO only after receiving the first capability
     * ACK. The stock dispatcher has already checked outer CRC. */
    if(p[1]==1 && V32(0x2001e78a)==0x31534d48 && p[6]==1 && p[7]==0 && V32(0x2001e790)==0x434f4d4d)
        trial_confirmed=1;
#endif
}
int hm_display_enter(void){
    if(V8(0x20001803)!=0 || fault)return -1; /* first build: stock orientation */
    return wait_lcd();
}
uint8_t *hm_display_buffer(void){return (uint8_t*)0x20004ce0;}
int hm_display_leave(void){
    /* Never return the DMA source to the GUI while it is still in flight. */
    if(hm_display_finish())return -1;
    V8(0x20004c52)=255;return 0;
}
int hm_display_finish(void){
    uint32_t t=now();int result=wait_lcd();lcd_ms+=now()-t;return result;
}
int hm_display_flush(uint16_t y,uint16_t rows,const uint8_t *p){
    if((p!=(const uint8_t*)0x20004ce0 && p!=(const uint8_t*)(0x20004ce0+HM_LCD_BYTES))
        || rows!=HM_LCD_ROWS || y>170-HM_LCD_ROWS || y%HM_LCD_ROWS)return -1;
    if(hm_display_finish())return -1;
    uint32_t t=now();
    FN(0x08024070,void(*)(uint32_t,uint32_t,uint32_t,uint32_t,const uint8_t*))(0,y,319,y+rows-1,p);
    lcd_ms+=now()-t;lcd_calls++;return 0;
}
void hm_board_stats(uint8_t data[232]){
    uint32_t values[16]={0x31545348,now(),lcd_ms,hms_ms,lcd_calls,hms_calls,
        usb_calls,usb_bytes,usb_max,V32(0x40023800),V32(0x40023804),
        V32(0x40023808),V32(0x40013000),V32(0xe000ed00),V32(0xe000e014),0};
    for(unsigned i=0;i<sizeof(values);i++)data[i]=((const uint8_t*)values)[i];
}
int hm_cdc_tx(const uint8_t p[256]){
    if(!connected)return -1;
    return FN(0x080357fc,int(*)(void*,const void*))(TX_META,p)?0:-1;
}
void __aeabi_memclr(void *v,unsigned n){uint8_t *p=v;while(n--)*p++=0;}
void __aeabi_memclr4(void *v,unsigned n){__aeabi_memclr(v,n);}
