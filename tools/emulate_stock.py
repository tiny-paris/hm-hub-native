#!/usr/bin/env python3
"""Execute the downloaded firmware's real Thumb handlers, without USB access.

Only ring-pop, debug logging, and physical LCD flush are substituted. CRC,
HID memcpy/bounds checks and 32x17-to-LCD rasterization run as machine code.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import hashlib
import json
import struct
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
from unicorn.arm_const import *
from host.stock_probe import packet

SHA = '5d6c874d1d37a5c20d5a4404502c81e93f4dbca261862b56c3f8041535536f77'
REGS = [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3]


class Firmware:
    def __init__(self):
        b = (ROOT / 'vendor/official-1.3.7/firmware-1.3.7.bin').read_bytes()
        assert hashlib.sha256(b).hexdigest() == SHA
        self.u = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        for base, size in [(0x08000000, 0x100000), (0x10000000, 0x10000),
                           (0x20000000, 0x80000)]:
            self.u.mem_map(base, size)
        self.u.mem_write(0x08010000, b)
        self.queue = []
        self.lcd = bytearray(108800)
        self.rects = []
        self.u.hook_add(UC_HOOK_CODE, self.hook)

    def ret(self, value=None):
        if value is not None:
            self.u.reg_write(UC_ARM_REG_R0, value)
        self.u.reg_write(UC_ARM_REG_PC, self.u.reg_read(UC_ARM_REG_LR))

    def hook(self, u, addr, size, user):
        if addr == 0x080ff000:
            u.emu_stop()
        elif addr == 0x08035794:  # ring_buffer_pop
            if self.queue:
                u.mem_write(u.reg_read(UC_ARM_REG_R1), self.queue.pop(0))
                self.ret(1)
            else:
                self.ret(0)
        elif addr == 0x080125dc:  # debug logging only
            self.ret()
        elif addr == 0x080240fc:  # blocking LCD rectangle write
            x0, y0, x1, y1 = [u.reg_read(r) for r in REGS]
            ptr = struct.unpack('<I', u.mem_read(u.reg_read(UC_ARM_REG_SP), 4))[0]
            assert 0 <= x0 <= x1 < 320 and 0 <= y0 <= y1 < 170
            data = u.mem_read(ptr, (x1-x0+1)*(y1-y0+1)*2)
            for y in range(y0, y1+1):
                n = (x1-x0+1)*2
                off = (y*320+x0)*2
                self.lcd[off:off+n] = data[(y-y0)*n:(y-y0+1)*n]
            self.rects.append([x0, y0, x1, y1])
            self.ret()

    def call(self, address, *args):
        self.u.reg_write(UC_ARM_REG_SP, 0x1000f000)
        self.u.reg_write(UC_ARM_REG_LR, 0x080ff001)
        for r, v in zip(REGS, args):
            self.u.reg_write(r, v)
        self.u.emu_start(address | 1, 0x080ff000, count=3000000)
        assert self.u.reg_read(UC_ARM_REG_PC) == 0x080ff000, 'instruction limit exceeded'

    def hid(self, total, index, payload):
        self.u.mem_write(0x20070000, bytes([5, 1, total, index]) + payload)
        self.call(0x08023908, 0x20070000)

    def render(self):
        self.u.mem_write(0x2001e98c, struct.pack('<I', 1000))
        self.call(0x080237c4)


def main():
    f = Firmware()
    frame = bytes(c for y in range(17) for x in range(32)
                  for c in (x*8, y*15, ((x+y)&1)*255))
    for i in range(7):
        f.hid(7, i, frame[i*252:(i+1)*252].ljust(252, b'\0'))
    assert bytes(f.u.mem_read(0x10002800, 1632)) == frame
    f.render()
    first = bytes(f.lcd)
    assert len(f.rects) == 17
    # Verify every 10x10 tile is constant in the actual binary renderer output.
    for y in range(170):
        for x in range(320):
            a = 2*(y*320+x)
            b = 2*((y//10*10)*320+(x//10*10))
            assert first[a:a+2] == first[b:b+2]
    ram = bytes(f.u.mem_read(0x10002800, 2048))
    f.hid(9, 8, b'\xff'*252)
    assert bytes(f.u.mem_read(0x10002800, 2048)) == ram
    f.hid(8, 7, b'\xff'*252)
    assert bytes(f.u.mem_read(0x10002800+1764, 252)) == b'\xff'*252
    f.render()
    assert bytes(f.lcd) == first  # index 7 does not add screen pixels
    ignored = [0, 4, 5, 7, 9, 10, 11, 255]
    ram_before = bytes(f.u.mem_read(0x10002800, 2048))
    for cmd in ignored:
        f.queue = [packet(cmd, b'\xff'*251)]
        f.call(0x08034a8c)
        assert not f.queue
        assert bytes(f.u.mem_read(0x10002800, 2048)) == ram_before
    # LCD SPI order is RGB565 big endian; this preview is emulated, not a photo.
    rgb = bytearray()
    for (v,) in struct.iter_unpack('>H', first):
        rgb.extend(((v>>11)*255//31, ((v>>5)&63)*255//63, (v&31)*255//31))
    (ROOT/'build/test-results/stock-emulated-screen.ppm').write_bytes(b'P6\n320 170\n255\n'+rgb)
    result = dict(firmware_sha256=SHA, hardware_test=False,
        hid_index_8_rejected=True, hid_index_7_has_no_visible_pixels=True,
        renderer_fixed_10x10_tiles=True, rows=17, columns=32,
        ignored_cdc_commands_tested=ignored, lcd_flush_rectangles=f.rects[:17])
    (ROOT/'build/test-results/stock-emulation.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
