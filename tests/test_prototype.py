#!/usr/bin/env python3
"""Receiver integration tests: pixel fidelity, bounds, ordering and recovery."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ctypes
import struct
import time
import unittest
import zlib
from host.native_stream import Simulator, Player, make_packet, FRAME_BYTES


class ReceiverTest(unittest.TestCase):
    def setUp(self):
        self.link=Simulator();self.player=Player(self.link);self.player.hello()
    def tearDown(self):
        while self.link.recv(0):pass
        self.player.stop()
    def ack_status(self,p):
        self.link.send(p);a=self.link.recv(0);self.assertIsNotNone(a);return a[19]
    def test_native_one_pixel_detail_and_consecutive_frames(self):
        for i in range(2):
            data=bytes((x+i*31)%256 for x in range(FRAME_BYTES))
            self.player.frame(data,i+1)
            self.assertEqual(self.link.screen(),data)
        self.assertTrue(self.link.lib.hm_stream_active())
        self.player.stop();self.assertFalse(self.link.lib.hm_stream_active())
    def test_out_of_order_cancels_display_lease(self):
        self.assertEqual(self.ack_status(make_packet(2,7)),0)
        self.assertEqual(self.ack_status(make_packet(3,7,232,b'xx')),2)
        self.assertFalse(self.link.lib.hm_stream_active())
    def test_oversize_length_and_bad_version(self):
        for field, value in [(16,234),(6,2)]:
            p=bytearray(make_packet(2,1));struct.pack_into('<H',p,field,value)
            struct.pack_into('<I',p,252,zlib.crc32(p[:252]))
            self.assertEqual(self.ack_status(bytes(p)),1)
    def test_corrupt_packet_does_not_write_or_ack(self):
        self.assertEqual(self.ack_status(make_packet(2,3)),0)
        old=self.link.screen();p=bytearray(make_packet(3,3,0,b'xx'));p[20]^=1
        self.link.send(bytes(p));self.assertIsNone(self.link.recv(0));self.assertEqual(old,self.link.screen())
        now=int((time.monotonic()-self.link.start)*1000)
        self.link.lib.hm_stream_tick(now+1001);self.assertFalse(self.link.lib.hm_stream_active())
    def test_incomplete_frame_rejected(self):
        self.assertEqual(self.ack_status(make_packet(2,1)),0)
        self.assertEqual(self.ack_status(make_packet(4,1,0,b'\0'*4)),3)
        self.assertFalse(self.link.lib.hm_stream_active())
    def test_frame_crc_mismatch_releases_display(self):
        self.player.frame(bytes(FRAME_BYTES),1)
        self.assertEqual(self.ack_status(make_packet(2,2)),0)
        # Exercise a whole frame followed by intentionally wrong final CRC.
        for stripe in range(0,FRAME_BYTES,6400):
            for off in range(stripe,stripe+6400,232):
                n=min(232,stripe+6400-off)
                self.assertEqual(self.ack_status(make_packet(3,2,off,bytes(n))),0)
        self.assertEqual(self.ack_status(make_packet(4,2,FRAME_BYTES,b'\0'*4)),3)
        self.assertFalse(self.link.lib.hm_stream_active())
    def test_stripe_overflow_rejected(self):
        self.assertEqual(self.ack_status(make_packet(2,4)),0)
        for off in range(0,6264,232):
            self.assertEqual(self.ack_status(make_packet(3,4,off,bytes(232))),0)
        self.assertEqual(self.ack_status(make_packet(3,4,6264,bytes(232))),1)
    def test_no_flash_command_is_implemented(self):
        before=self.link.lib.sim_flushes()
        for sub in [1,2,3,4]:
            p=bytearray(make_packet(sub));p[0]=8
            struct.pack_into('<I',p,252,zlib.crc32(p[:252]))
            self.link.send(bytes(p));self.assertIsNone(self.link.recv(0))
        self.assertEqual(self.link.lib.sim_flushes(),before)
    def test_board_enter_failure_does_not_acquire_display(self):
        self.link.lib.sim_fail_enter(1)
        self.assertEqual(self.ack_status(make_packet(2,1)),4)
        self.assertFalse(self.link.lib.hm_stream_active())
        self.assertEqual(self.link.lib.sim_flushes(),0)
    def test_board_flush_failure_releases_display(self):
        self.link.lib.sim_fail_flush(1)
        with self.assertRaisesRegex(RuntimeError,'status 4'):
            self.player.frame(bytes(FRAME_BYTES),1)
        self.assertFalse(self.link.lib.hm_stream_active())
        self.assertEqual(self.link.lib.sim_flushes(),0)
    def test_tx_full_releases_without_false_data_ack(self):
        self.link.send(make_packet(2,1))  # leave BEGIN reply in the one-slot TX queue
        self.link.send(make_packet(3,1,0,b'xx'))
        self.assertFalse(self.link.lib.hm_stream_active())
        ack=self.link.recv(0);self.assertEqual(ack[1],0x82)
        self.assertIsNone(self.link.recv(0))
    def test_lease_wraparound_and_readonly_hello(self):
        self.assertFalse(self.link.lib.hm_stream_active())
        self.link.lib.hm_stream_packet(make_packet(2,1),ctypes.c_uint32(0xfffffff0))
        self.assertEqual(self.link.recv(0)[19],0)
        self.link.lib.hm_stream_packet(make_packet(1),ctypes.c_uint32(0x300))
        self.assertEqual(self.link.recv(0)[19],0)
        self.link.lib.hm_stream_tick(0x3d8)  # exactly 1000 ms, including wrap
        self.assertTrue(self.link.lib.hm_stream_active())
        self.link.lib.hm_stream_tick(0x3d9)
        self.assertFalse(self.link.lib.hm_stream_active())


if __name__=='__main__':unittest.main(verbosity=2)
