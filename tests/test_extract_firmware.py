"""Reject changed download bytes before creating the build input."""
import hashlib
import struct
import unittest
from unittest.mock import patch

from tools import extract_firmware as firmware


class OfficialInputTest(unittest.TestCase):
    def test_changed_download_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'JavaScript SHA-256 mismatch'):
            firmware.extract(b'changed upstream response')

    def test_source_and_extracted_image_are_both_verified(self):
        image = bytearray(450240)
        struct.pack_into('<I', image, 0x1000, 0x10307)
        source = b'new Uint8Array([' + b','.join(f'0x{v:02x}'.encode() for v in image) + b'])'
        with patch.object(firmware, 'SOURCE_SHA', hashlib.sha256(source).hexdigest()), \
             patch.object(firmware, 'FIRMWARE_SHA', hashlib.sha256(image).hexdigest()):
            self.assertEqual(firmware.extract(source), image)
        with patch.object(firmware, 'SOURCE_SHA', hashlib.sha256(source).hexdigest()):
            with self.assertRaisesRegex(ValueError, 'firmware length/SHA-256 mismatch'):
                firmware.extract(source)
