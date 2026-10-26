#!/usr/bin/env python3
"""Bounded read-only probes. Opcode 5 is proven a no-op in stock 1.3.7.

Never sends DFU, config writes, reset, flash start/data/clear or HID pixels.
Results distinguish transport acceptance, replies, and physical observation.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import argparse
import collections
import hashlib
import json
import struct
import time
import zlib
import serial
from serial.tools import list_ports


def locate_port(path=None, serial_number=None):
    """Select one HM Hub; require an explicit port when selection is ambiguous."""
    if path is not None:
        return path
    ports = [p.device for p in list_ports.comports()
             if (p.vid, p.pid) == (0xc019, 0x0401)
             and (serial_number is None or p.serial_number == serial_number)]
    if len(ports) != 1:
        raise serial.SerialException(
            f'expected one HM Hub, found {len(ports)}; select a device with --port')
    return ports[0]


def packet(cmd, payload=b''):
    if len(payload) > 251:
        raise ValueError('payload too long')
    head = bytes([cmd]) + payload.ljust(251, b'\0')
    return head + struct.pack('<I', zlib.crc32(head))


class Link:
    def __init__(self, path=None, capture=True):
        self.port = serial.Serial(locate_port(path), 115200, timeout=.05, write_timeout=2,
                                  exclusive=True)
        self.buffer = bytearray()
        self.counts = collections.Counter()
        self.bad_bytes = 0
        self.capture = bytearray()
        self.capture_enabled = capture
        self.bulk_join = False

    def send(self, data):
        if self.port.write(data) != len(data):
            raise IOError('short serial write')

    def send_many(self, packets):
        # Current HM USB descriptors advertise an invalid HS bulk MPS of 256.
        # Multi-packet writes wedge OUT on this host. Preserve 256-byte writes.
        if self.bulk_join:self.send(b''.join(packets))
        else:
            for p in packets:self.send(p)

    def recv(self, until):
        while time.monotonic() < until:
            while len(self.buffer) >= 256:
                p = bytes(self.buffer[:256])
                if zlib.crc32(p[:252]) != struct.unpack_from('<I', p, 252)[0]:
                    del self.buffer[0]
                    self.bad_bytes += 1
                    continue
                del self.buffer[:256]
                self.counts[p[0]] += 1
                return p
            data = self.port.read(max(1, self.port.in_waiting))
            if self.capture_enabled:
                self.capture.extend(data)
            self.buffer.extend(data)
        return None

    def request(self, cmd, payload=b'', timeout=2):
        self.send(packet(cmd, payload))
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            p = self.recv(until)
            if p and p[0] == cmd:
                return p
        raise TimeoutError(f'no response to command {cmd}')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--port', help='Serial port; auto-selects a single c019:0401 device')
    ap.add_argument('--readback', action='store_true', help='Read used image area only; no throughput probe')
    ap.add_argument('--output', type=Path, default=ROOT/'build/stock-probe.json')
    a = ap.parse_args()
    a.output.parent.mkdir(parents=True, exist_ok=True)
    link = Link(a.port)
    result = {'physical_display_observation': 'not observed by this probe',
              'flash_write_commands_sent': 0, 'config_write_commands_sent': 0}
    try:
        info = link.request(1)
        hw, fw, flash = struct.unpack_from('<III', info, 1)
        result['device'] = dict(hw=hex(hw), fw=hex(fw), flash_bytes=flash)
        # The reviewed release preserves cmd8/sub4 and its stock readback
        # service. Do not permit arbitrary private firmware versions.
        if hw != 0xa101 or fw not in (0x10307, 0x81030308, 0x81030309, 0x8104030a):
            raise RuntimeError('unreviewed firmware/hardware: stopping')
        if fw != 0x10307 and not a.readback:
            raise RuntimeError('native release is allowed only for bounded readback here')
        before = link.request(3, b'\x01')
        result['config_before_hex'] = before[6:34].hex()
        if a.readback:
            link.send(packet(8, b'\x04'))
            deadline = time.monotonic() + 60
            data = bytearray()
            ended = False
            while time.monotonic() < deadline:
                p = link.recv(deadline)
                if not p or p[0] != 8:
                    continue
                if p[1] == 4:
                    ended = True
                    break
                if p[1] != 3:
                    raise RuntimeError(f'unexpected readback subtype {p[1]}')
                offset, length = struct.unpack_from('<IH', p, 2)
                if offset != len(data) or not 0 < length <= 244 or offset+length > flash:
                    raise RuntimeError('readback sequence/length invalid')
                data.extend(p[8:8+length])
            result['readback_ended'] = ended
            result['readback_bytes'] = len(data)
            if not ended:
                raise TimeoutError('readback did not finish in 60 seconds')
            if len(data) < 8192:
                raise RuntimeError('readback has no full header area')
            albums = []
            end = 8192
            for off in range(0, 8192-27, 28):
                magic = struct.unpack_from('<I', data, off)[0]
                if magic != 0xc0190001:
                    continue
                w, h, frames, delay, start, length, crc, hdrcrc = struct.unpack_from('<4H4I', data, off+4)
                if zlib.crc32(data[off:off+24]) != hdrcrc:
                    raise RuntimeError('album header CRC mismatch')
                if length != w*h*frames*2 or start < 8192 or start+length > len(data):
                    raise RuntimeError('album dimensions/bounds mismatch')
                if zlib.crc32(data[start:start+length]) != crc:
                    raise RuntimeError('album pixel CRC mismatch')
                albums.append(dict(width=w,height=h,frames=frames,delay_ms=delay,offset=start,length=length))
                end = max(end, start+length)
            if not albums or end != len(data):
                raise RuntimeError('readback length differs from used image area')
            saved = a.output.with_suffix('.album.bin')
            saved.write_bytes(data)
            result.update(backup_path=str(saved), backup_sha256=hashlib.sha256(data).hexdigest(), albums=albums)
            result['status'] = 'completed'
            return
        # Each no-op has its own handshake barrier; group eight for timing.
        # An extra handshake ends each group. This is
        # NOT a framebuffer test or proof that every preceding no-op was parsed.
        timings = []
        nop = packet(5, b'HIRES-NOOP-PROBE')
        start = time.monotonic()
        for _ in range(32):
            t = time.monotonic()
            for _ in range(8):
                link.send(nop)
                # Drain by a known response rather than extrapolate bulk bandwidth.
                link.request(1)
            link.request(1)
            timings.append(time.monotonic() - t)
        elapsed = time.monotonic() - start
        result['bulk_noop_probe'] = dict(host_written_bytes=32 * 17 * 256,
            handshake_replies=32 * 9, elapsed_seconds=elapsed,
            host_bytes_per_second=32 * 17 * 256 / elapsed,
            max_batch_seconds=max(timings),
            interpretation='bounded transport exercise; not lossless pixel throughput')
        after = link.request(3, b'\x01')
        result['config_after_hex'] = after[6:34].hex()
        result['configuration_unchanged'] = before[6:34] == after[6:34]
        result['status'] = 'completed'
    except Exception as exc:
        result['status'] = 'failed'
        result['error'] = str(exc)
        raise
    finally:
        link.port.close()
        result['received_commands'] = dict(link.counts)
        result['resync_discarded_bytes'] = link.bad_bytes
        capture = a.output.with_suffix('.rx.bin')
        capture.write_bytes(link.capture)
        result['capture_sha256'] = hashlib.sha256(link.capture).hexdigest()
        a.output.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
