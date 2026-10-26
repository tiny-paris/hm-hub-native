"""Device selection must never guess between multiple attached Hubs."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import serial
from host.stock_probe import locate_port


class PortSelectionTest(unittest.TestCase):
    def port(self, name, number, vid=0xc019, pid=0x0401):
        return SimpleNamespace(device=name, serial_number=number, vid=vid, pid=pid)

    def test_only_matching_usb_identity_is_selected(self):
        ports = [self.port('other', None, vid=0x1234), self.port('hub', 'example')]
        with patch('host.stock_probe.list_ports.comports', return_value=ports):
            self.assertEqual(locate_port(), 'hub')

    def test_missing_or_ambiguous_device_requires_selection(self):
        for ports in ([], [self.port('first', 'one'), self.port('second', 'two')]):
            with self.subTest(ports=ports), patch('host.stock_probe.list_ports.comports', return_value=ports):
                with self.assertRaises(serial.SerialException):
                    locate_port()

    def test_serial_filter_and_explicit_port(self):
        ports = [self.port('first', 'one'), self.port('second', 'two')]
        with patch('host.stock_probe.list_ports.comports', return_value=ports) as scan:
            self.assertEqual(locate_port(serial_number='two'), 'second')
            scan.reset_mock()
            self.assertEqual(locate_port('/dev/selected'), '/dev/selected')
            scan.assert_not_called()
