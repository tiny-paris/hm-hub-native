# HM Hub 1.3.7 board integration

This adapter targets hardware ID `0xa101`, USB `c019:0401`, Cortex-M4, and
official firmware SHA-256
`5d6c874d1d37a5c20d5a4404502c81e93f4dbca261862b56c3f8041535536f77`.
The default pipeline build preserves version `0x10307`; use HMS1 HELLO
to detect the extension. All addresses below are specific to this image.

## Memory and hooks

| Address | Change or ownership |
| --- | --- |
| `0x0807dec0..0x0807ffff` | Append patch inside bootloader's application boundary |
| `0x2001ef20` onward | New zero-initialized receiver, queue and adapter metadata |
| `0x2001f000..0x2001f1ff` | New aligned 512-byte normal-SRAM USB receive buffer |
| `0x10000000` | Reuse 20×256 RX storage with new incompatible metadata |
| `0x10001400`, metadata `0x2001e9a8` | Preserve stock TX queue/copying enqueue |
| `0x20004ce0..0x200065df` | Reuse two 3,200-byte LCD half-buffers under exclusive ownership |
| `0x080349c4` | RX/TX initialization |
| `0x0803702a` | DTR connect/disconnect; defer receiver reset to main loop |
| `0x0801434e` | Consume actual USB length before rearming OUT |
| `0x08034aa4`, `0x08034bc6` | RX pop with eight-packet main-loop budget |
| `0x08034b10` | Previously ignored cmd4 → HMS1; preserve dispatcher CRC |
| `0x08034634` | GUI ownership, lease tick, heartbeat and RX budget |
| `0x08034e14` | Nonblocking TX drain, replacing stock busy spin |
| `0x08034644` | Reserve TX room before stock readback advances |
| `0x080362ae` | Timer watchdog feed gated by main-loop heartbeat |
| `0x080361d4` | Suppress old page drawing tick during streaming |
| `0x0801432c`, `0x08014408` | New USB buffer and HS/FS descriptor wrapper |
| `0x08013034`, `0x08013106`, `0x08012e46` | CDC open / initial receive / HS rearm sizes 512 |
| `0x0807dc28` | Extend only normal-SRAM ZI through `0x2001f200` |
| `0x08011000` | Preserve official `0x10307` in compatible release; private release used `0x8104030a` |

`build/candidate-review.json` contains current before/after bytes and linked image size.
Vector table, reset entry, compressed initialized data, CCM heap/stack and
bootloader remain intact. Actual Keil scatter-loader emulation compares every
old initialized SRAM/CCM byte against stock, checks new zero fill and confirms
bytes beyond it untouched. CPUID reports Cortex-M4 r0p1; exact manufacturer/model
is still unknown and cannot be inferred solely from F4-compatible addresses.

## USB framing and backpressure

Stock HS bulk MPS=256 is invalid. Joined writes wedged OUT even below queue
capacity; physical reconnection cleared it. The extension uses HS 512 / FS 64
descriptors, CDC bulk open=512, a new buffer and corrected rearm lengths.
HID endpoints and 256-byte application/control packets remain unchanged.

Only one receive is armed. Reserve all 512 bytes in the 20-packet RX queue
before rearming; otherwise USB NAKs until the main consumer frees space.
Consume completion bytes before buffer reuse. Arbitrary short fragments are
assembled; 512-byte completions retain both protocol packets. PRIMASK protects
only queue metadata/arming. CRC and LCD operations run outside this region.
Unexpected oversize/short-consume faults request watchdog recovery instead of
silently dropping data. Disconnect clears partial framing and releases display
in main loop. HELLO advertises window 64 and joined-write capability.

TX replies are copied into stock queue. Busy IN returns without spinning or
popping. Original CRC-validated control/Flash dispatch remains available.
Stock readback can enqueue DATA plus END in one call and advances its offset
before enqueue. The service wrapper defers it when TX count exceeds 16/20,
leaving room for both. Actual machine-code regression reproduces the old
missing END and verifies the fix. No new Flash operation is issued.
Fast builds trust that existing outer CRC check, reuse stock IEEE table at
`0x080379e8`, and use aligned word copies with byte fallback. Full-frame CRC
remains mandatory; standalone C builds retain their own outer CRC check.

## Display and recovery

Direction must be zero. Enter waits for stock display ready. Suppress main GUI
and old page drawing tick while stream owns the LCD; USB, power sampling,
timers and watchdog remain active. Split the existing 6,400-byte stripe into two 5-row buffers at `0x20004ce0`
and `0x20005960`. Flush waits for the previous DMA, launches stock asynchronous
`0x08024070(0,y,319,y+4,buffer)`, and returns so packet CRC/copy work can fill
the other half. END and all ownership-release paths drain the final DMA. DMA requires normal SRAM
and its length is narrowed to 16 bits: no CCM pointer or 108,800-byte full-frame
transfer may be supplied.

Ready waits have a 50-ms bound. Original internal DMA setup still has waits;
timer watchdog feed stops when main heartbeat is stale for 5 seconds. Board
fault clears stock DFU watchdog flag. Faults remain sticky across DTR reconnects
and suppress both main/timer GUI drawing and further command consumption.
The original force-DFU command remains usable during healthy operation;
hardware faults instead require watchdog recovery.
Trial also requests DFU after 60 seconds unless updater confirms two-way HELLO.
Release removes that temporary deadline, retaining heartbeat/fault protection.

STOP, error, disconnect or 1,000-ms idle lease releases ownership and forces
old page re-entry (`0x20004c52=255`) without saving configuration. END ACK follows
frame CRC and completion of all 34 half-stripe transfers. It is not optical evidence. There is no
TE synchronization or atomic swap; progressive updates may tear.

## Verification and limits

Actual Thumb emulation uses stock CRC/dispatcher/queues, substituting only
physical peripherals. It covers exact pixels with 63/512-byte fragments,
corrupt outer CRC rejection, queue NAK, eight-packet budget, TX busy, lease and
disconnect release, DMA timeout and stale heartbeat. USB tests execute original
descriptor/init/rearm paths for HS/FS/HS and confirm unchanged HID endpoints.

`tests/test_pipeline.py` additionally delays DMA consumption, rejects CPU writes
to an in-flight source, checks cross-half payloads, and covers final completion,
STOP/error/disconnect/lease/TX-full drain, timeout and reconnect fault handling.
`tools/benchmark_pipeline.py` compares overlapping/serialized DMA scheduling
with the same Thumb image; its timing assumptions are not hardware FPS.
