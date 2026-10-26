"""Exercise complete DFU sessions without USB, including same-version stock."""
from collections import deque
from contextlib import redirect_stdout
import io
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from host import dfu_update as dfu
from host.native_stream import make_packet
from host.stock_probe import packet


class Clock:
    now = 100.0

    def monotonic(self):
        return self.now


class Device:
    def __init__(self, clock, hello):
        self.clock, self.hello = clock, hello
        self.port = self
        self.capture = bytearray()
        self.queue = deque()
        self.version = 0x10307
        self.images = []

    def identity(self):
        return packet(1, struct.pack('<III', 0xa101, self.version, 8388608))

    def request(self, command):
        assert command == 1
        return self.identity()

    def close(self):
        pass

    def next_block(self):
        offset = len(self.written)
        length = min(244, self.length-offset)
        self.queue.append(packet(2, b'\x02'+struct.pack('<IH', offset, length)))

    def send(self, p):
        assert zlib.crc32(p[:252]) == struct.unpack_from('<I', p, 252)[0]
        if p[0] == 1:
            self.queue.append(self.identity())
        elif p[:2] == b'\x02\x03':
            self.queue.append(packet(2, b'\x01'+struct.pack('<I', 0xa101)))
        elif p[:2] == b'\x02\x01':
            self.version, self.length, self.crc = struct.unpack_from('<III', p, 2)
            self.written = bytearray()
            self.next_block()
        elif p[:2] == b'\x02\x02':
            offset, length = struct.unpack_from('<IH', p, 2)
            assert offset == len(self.written)
            self.written.extend(p[8:8+length])
            if len(self.written) < self.length:
                self.next_block()
            else:
                assert zlib.crc32(self.written) == self.crc
                self.images.append(bytes(self.written))
                if self.hello == 'rollback' and len(self.images) == 1:
                    self.queue.append(packet(2, b'\x01'+struct.pack('<I', 0xa101)))
                else:
                    self.queue.append(self.identity())
        elif p[:8] == b'\x04\x01HMS1\x01\0':
            if self.hello == 'missing':
                return
            ident = struct.unpack_from('<I', p, 8)[0]
            caps = struct.pack('<HHBBBB', 320, 170, 10, 64, 1, 3)
            if self.hello == 'wrong_commit' and ident:
                caps = struct.pack('<HHBBBB', 320, 170, 10, 8, 1, 0)
            self.queue.append(make_packet(0x81, ident, data=caps))
        else:
            raise AssertionError(f'unexpected command {p[:8].hex()}')

    def recv(self, until):
        if self.queue:
            self.clock.now += .001
            return self.queue.popleft()
        self.clock.now = until
        return None


class CompatibleUpdateTest(unittest.TestCase):
    def run_update(self, hello):
        clock = Clock()
        device = Device(clock, hello)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'result.json'
            image = (dfu.ROOT/'build/native-bulk512-fast-pipeline-compatible-release.bin').read_bytes()
            args = ['dfu_update', '--write-native-compatible-release', '--image-sha256', hashlib.sha256(image).hexdigest(), '--seconds', '10', '--output', str(output)]
            with patch.object(dfu.sys, 'argv', args), patch.object(dfu, 'locate', return_value='fake'), \
                 patch.object(dfu, 'Link', return_value=device), patch.object(dfu.time, 'monotonic', clock.monotonic), \
                 redirect_stdout(io.StringIO()):
                code = dfu.main()
            result = json.loads(output.read_text())
        return code, result, device

    def test_web_identity_and_explicit_build_hash(self):
        original = (dfu.ROOT/'vendor/official-1.3.7/firmware-1.3.7.bin').read_bytes()
        current = (dfu.ROOT/'build/native-bulk512-fast-pipeline-compatible-release.bin').read_bytes()
        # Same official version avoids the 1.3.7 web driver's automatic DFU.
        self.assertEqual(current[4096:4100], original[4096:4100])
        sha = hashlib.sha256(current).hexdigest()
        self.assertIsNotNone(dfu.Image(current, sha, native=True).native_caps)
        with self.assertRaises(ValueError):
            dfu.Image(current, '0' * 64, native=True)
        self.assertIsNone(dfu.Image(original).native_caps)

    def test_same_version_requires_two_way_capabilities(self):
        code, result, device = self.run_update('valid')
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'installed_verified')
        self.assertTrue(result['native_handshake_verified'])
        self.assertEqual(device.images[0], (dfu.ROOT/'build/native-bulk512-fast-pipeline-compatible-release.bin').read_bytes())

    def test_stock_version_without_extension_is_not_success(self):
        for hello in ('missing', 'wrong_commit'):
            with self.subTest(hello=hello):
                code, result, _ = self.run_update(hello)
                self.assertEqual(code, 1)
                self.assertEqual(result['status'], 'not_verified')
                self.assertIsNone(result['installed_image_sha256'])

    def test_same_version_keeps_watchdog_rollback(self):
        code, result, device = self.run_update('rollback')
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'restored_original')
        self.assertEqual(result['installed_image_sha256'], dfu.ORIGINAL_SHA)
        self.assertEqual(len(device.images), 2)
        self.assertEqual(device.images[1], (dfu.ROOT/'vendor/official-1.3.7/firmware-1.3.7.bin').read_bytes())


if __name__ == '__main__':
    unittest.main()
