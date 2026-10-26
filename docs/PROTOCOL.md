# HMS1 native CDC stream — experimental firmware extension

This protocol is implemented by `firmware/stream_receiver.c` and `host/native_stream.py`.
**Stock HM Hub 1.3.7 does not implement it.** Command 4 is an ignored branch in
the pinned official firmware. A stock device receives only a capability query
from this player and will time out before any image data is sent.

## Packet layout

Every request and reply is exactly 256 bytes. Integers are little endian;
pixel payload is RGB565 **big endian**, in left-to-right, top-to-bottom order.

| Offset | Length | Meaning |
| --- | --- | --- |
| 0 | 1 | CDC command 4 |
| 1 | 1 | Subcommand; replies set bit 7 |
| 2 | 4 | ASCII `HMS1` |
| 6 | 2 | Version 1 |
| 8 | 4 | Frame ID |
| 12 | 4 | Absolute byte offset within frame; replies report next offset |
| 16 | 2 | Payload length, 0–232 |
| 18 | 1 | Request bit 0 asks for DATA ACK; other bits reserved zero |
| 19 | 1 | Zero in requests; status in replies |
| 20 | 232 | Payload, zero padded by this host |
| 252 | 4 | IEEE CRC32 of bytes 0–251, compatible with zlib.crc32 |

Subcommands:

| Value | Operation | Payload and response |
| --- | --- | --- |
| 1 | HELLO | No data or offset; read-only. Reply payload: u16 width=320, u16 height=170, u8 stripe_rows=10, u8 window=64, u8 format=1, u8 features=3 (joined writes + statistics) |
| 2 | BEGIN | New frame ID, zero offset/data. Acquire display if needed; ACK offset 0 |
| 3 | DATA | Nonempty even byte count. Exact current offset and frame ID; must not cross a 6,400-byte stripe. ACK when requested |
| 4 | END | Offset 108,800; 4-byte CRC32 of all pixel bytes. ACK after CRC validation and the final LCD DMA completion fence |
| 5 | STOP | Release display ownership; ACK. Host sends ID/offset/data as zero |
| 6 | STATS | Read-only; zero offset/data. Reply has 16 little-endian u32 values (64 bytes); does not extend display lease |

Statuses: 0 success, 1 malformed/version/length, 2 state/order mismatch,
3 incomplete frame or frame CRC mismatch, 4 board enter/flush/finish failure.
Bad outer CRC or wrong command/magic is silently ignored. Most protocol errors
release display ownership. The host aborts on any rejection or missing ACK;
there is no retransmission/resume contract. BEGIN/END/STOP reply when processed;
after a hardware fault, command consumption stops pending watchdog recovery.

## Buffering and acknowledgments

Each frame is 108,800 bytes. A stripe is 320×10×2 = 6,400 bytes; there are
17 stripes. The host sends 27×232 + 136 bytes per stripe (28 DATA packets).
The pipeline release internally splits each wire stripe into two 5-row DMA
buffers in the same 6,400 bytes. A DATA payload may cross that internal boundary;
the receiver splits the copy. DATA ACK means accepted bytes; END ACK waits for
the last DMA. A packet never crosses a wire stripe. The negotiated ACK window can cross stripe
boundaries: with window 64, 476 DATA packets require 8 DATA ACKs. BEGIN and
END add two more ACKs, for 478 OUT packets and 10 IN packets per frame.

The release adapter uses compliant 512-byte High Speed bulk endpoints, a
512-byte normal-SRAM USB receive buffer and a 20×256-byte protocol RX queue.
It reserves room for all 512 armed bytes before rearming OUT. A full queue
makes the endpoint NAK until main-loop progress. The host joins up to 64
protocol packets into one write only when HELLO feature bit 0 is set.

The older native trial advertises window 8/features 0 and retains the stock
invalid 256-byte High Speed bulk MPS. Joined host writes must remain disabled
there: a single 2,048-byte write wedged OUT and required physical reconnection.
A larger advertised window alone did not solve that USB defect. All stock
control/Flash packets remain 256 bytes regardless of USB transfer size.

STATS word order is magic `0x31545348`, uptime_ms, accumulated lcd_ms,
accumulated receiver_ms (including LCD), LCD calls, receiver calls, USB callback
count, USB bytes, largest USB receive, RCC_CR, RCC_PLLCFGR, RCC_CFGR, SPI1_CR1,
CPU_ID, SysTick_RELOAD, reserved. Counters wrap as u32. Register addresses are
fixed in the reviewed adapter; this is not an arbitrary memory read command.
Feature bit 1 announces STATS. Neither HELLO nor STATS keeps a stream alive.
In pipeline version `0x8104030a` and its web-compatible release, LCD calls count 34 DMA submissions per frame;
`lcd_ms` measures CPU time in LCD setup and completion waits, excluding DMA
time overlapped with packet processing. It is not total SPI transfer time.
Historical synchronous releases count 17 transfers per frame.

The web-compatible release retains the official cmd1 version `0x10307` to
avoid automatic reflashing by the official 1.3.7 web driver. Stock 1.3.7 has no
HMS1 support. Always use HELLO to detect streaming capabilities; the official
version word alone cannot identify the extension. The updater pins each image
by SHA and requires the native two-way handshake even with that stock version.

An END ACK means the receiver accepted the CRC and the final LCD DMA
completed successfully. It is **not optical evidence of displayed pixels**. Stripes
appear progressively; no framebuffer swap, TE synchronization, atomic commit,
or rollback is provided. Full-frame CRC failure may be detected after some/all
stripes reached the panel. Physical acceptance requires observing a detailed
test pattern and monotonically changing frame counter on the actual panel.

## Board integration contract

`firmware/stream_receiver.h` defines all callbacks. Call receiver APIs only from one
serialized main-loop context, never directly from the USB ISR.

- `hm_display_buffer()` returns a stable 6,400-byte DMA-accessible normal SRAM
  buffer. On the analyzed firmware the existing strip starts at `0x20004ce0`;
  reusing it requires exclusive ownership, not just knowing its address.
- `hm_display_enter()` waits for prior transfers and stops other GUI/LCD users.
  It must leave the previous state intact on failure and return nonzero.
- `hm_display_flush(y, 5, buffer)` waits for the previous DMA, then starts
  x=0..319, y..y+4 using stock asynchronous rectangle `0x08024070`. On success
  the other half-buffer is writable; the supplied half stays owned by DMA.
  The two addresses are `0x20004ce0` and `0x20005960`.
- `hm_display_finish()` waits up to 50 ms for DMA completion. END must call it
  before acknowledging success. Stock internal setup still has waits; the
  independent watchdog is the final escape if it wedges.
- `hm_display_leave()` drains DMA before restoring/redrawing the previous GUI
  without saving a transient mode to Flash. It returns nonzero on timeout;
  STOP then reports status 4. Faults suppress both GUI paths and command
  dispatch until watchdog recovery, including across DTR reconnects.
- `hm_cdc_tx()` must **copy** the 256-byte stack packet into a TX queue before
  returning zero. On failure the receiver releases the display; no ACK is
  guaranteed. Queue/drain logic must continue during streaming.
- `hm_stream_tick(now_ms)` must run regularly. More than 1,000 ms without
  accepted BEGIN/DATA/END releases the display. HELLO cannot prolong it.
  Disconnect handling should release promptly; do not depend solely on host
  STOP. GUI redraw after release is the board adapter's responsibility.

The receiver has no Flash functions, arbitrary-address writes or DFU entry.
The linked adapter is pinned to the archived 1.3.7 image. The fast board build
reuses the original 256-entry IEEE CRC table at `0x080379e8`, trusts the original
CRC-validated dispatcher for outer packets, and uses aligned word copies with
byte fallback. It still validates every incoming packet once in the original
dispatcher, plus the complete frame CRC at END. Standalone desktop builds keep
their own outer validation and small nibble table.

`firmware/cdc_stream_rx.c` supplies fixed-frame assembly over arbitrary USB fragments;
its metadata is not compatible with the old ring. `firmware/board_patch.c` integrates
it, copies TX replies, guards normal GUI and page-tick rendering during a
stream, restores the old page on release, and preserves watchdog/USB service.
All renderer/receiver APIs execute in the serialized main loop, outside the
short interrupt-exclusion region. See [board integration](BOARD_INTEGRATION.md) for exact hooks.


Stock 50-Hz telemetry continues during streaming. Keep reading during low-fps
waits to avoid filling IN/TX queues. `Player.wait_until()` drains unsolicited
telemetry; STOP releases display ownership. Loop playback keeps one session
and caps its timing history at 2,048 frames.
