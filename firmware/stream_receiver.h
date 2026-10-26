#ifndef HM_STREAM_RECEIVER_H
#define HM_STREAM_RECEIVER_H
#include <stdint.h>
#define HM_LCD_ROWS 5u
#define HM_LCD_BYTES (320u * 2u * HM_LCD_ROWS)
/* Board integration: all callbacks run in the main loop, never USB ISR.
 * enter must grant exclusive LCD/SPI ownership; leave restores prior GUI state.
 * flush waits for the PREVIOUS DMA, then starts the supplied half-buffer.
 * On success the other half is writable; the supplied half remains owned by
 * DMA until the next flush or finish. finish drains the last DMA before END.
 * leave must drain DMA before restoring the GUI; on timeout the board must
 * suppress GUI writes and request recovery. All waits must be bounded.
 * tx must COPY 256 bytes into the CDC TX queue before returning (0=success).
 */
int hm_display_enter(void);
/* 6400 bytes, valid for the receiver lifetime, DMA-accessible normal SRAM.
 * May reuse the stock RGB renderer's strip only with exclusive GUI ownership. */
uint8_t *hm_display_buffer(void);
int hm_display_leave(void);
int hm_display_flush(uint16_t y, uint16_t rows, const uint8_t *rgb565be);
int hm_display_finish(void);
int hm_cdc_tx(const uint8_t packet[256]);
/* Read-only bounded counters/register snapshot; no caller-supplied addresses. */
void hm_board_stats(uint8_t data[232]);
void hm_stream_init(void);
void hm_stream_tick(uint32_t now_ms);
void hm_stream_packet(const uint8_t packet[256], uint32_t now_ms);
int hm_stream_active(void);
#endif
