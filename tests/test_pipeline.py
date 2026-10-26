#!/usr/bin/env python3
"""Delayed-DMA ownership, completion fences and timeout recovery in Thumb code."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import json
import struct
import unittest
from tests.test_patched_firmware import Board
from host.native_stream import Player, make_packet, FRAME_BYTES


def start_stripe(board, frame=1):
    board.send(make_packet(2, frame))
    board.acks.clear()
    # 3248 bytes crosses the internal 3200 boundary inside a DATA packet.
    for off in range(0, 3248, 232):
        board.send(make_packet(3, frame, off, bytes([off // 232]) * 232, False))
    assert board.pending_dma and board.overlap_writes


def queue_frame(board, frame=1):
    board.send(make_packet(2, frame));board.acks.clear()
    for stripe in range(0, FRAME_BYTES, 6400):
        for off in range(stripe, stripe + 6400, 232):
            board.send(make_packet(3, frame, off, bytes(min(232, stripe + 6400 - off)), False))
    assert board.pending_dma


class PipelineTest(unittest.TestCase):
    def test_release_paths_drain_dma(self):
        for action in ('stop', 'disconnect', 'lease', 'bad_offset', 'bad_crc', 'tx_full'):
            with self.subTest(action=action):
                b = Board();start_stripe(b)
                if action == 'stop':b.send(make_packet(5))
                elif action == 'disconnect':b.call(0x803702a, 0);b.pump()
                elif action == 'lease':b.set_ms(b.ms + 1001);b.pump()
                elif action == 'bad_offset':b.send(make_packet(3, 1, 0, b'xx'))
                elif action == 'bad_crc':b.send(make_packet(4, 1, 3248, bytes(4)))
                else:
                    b.busy = True
                    # Fill the actual stock TX queue, including failed enqueue.
                    for _ in range(21):b.send(make_packet(1))
                self.assertFalse(b.pending_dma)
                self.assertFalse(b.active())
                b.pump();self.assertGreater(b.gui_calls, 0)

    def test_end_waits_for_final_dma(self):
        import zlib
        b = Board();queue_frame(b)
        self.assertEqual(len(b.rects), 34)
        b.send(make_packet(4, 1, FRAME_BYTES, struct.pack('<I', zlib.crc32(bytes(FRAME_BYTES)))))
        self.assertFalse(b.pending_dma)
        self.assertEqual(b.acks[-1][19], 0)
        self.assertEqual(b.screen, bytes(FRAME_BYTES))

    def test_timeout_does_not_resume_gui_or_ack_success(self):
        import zlib
        for action in ('stop', 'end', 'next_stripe', 'disconnect', 'lease'):
            with self.subTest(action=action):
                b = Board()
                if action == 'end':queue_frame(b)
                else:start_stripe(b)
                b.lcd_ready = False;b.tick_ready_poll = True
                before = b.gui_calls
                if action == 'stop':b.send(make_packet(5))
                elif action == 'end':
                    b.send(make_packet(4, 1, FRAME_BYTES, struct.pack('<I', zlib.crc32(bytes(FRAME_BYTES)))))
                    self.assertEqual(b.acks[-1][19], 4)
                elif action == 'next_stripe':
                    for off in range(3248, 6400, 232):
                        b.send(make_packet(3, 1, off, bytes(min(232, 6400-off)), False))
                    self.assertEqual(b.acks[-1][19], 4)
                elif action == 'disconnect':b.call(0x803702a, 0)
                else:b.set_ms(b.ms + 1001)
                b.pump();b.call('patch_gui_tick')
                self.assertEqual(b.gui_calls, before)
                self.assertEqual(b.u.mem_read(0x200002ef, 1), b'\0')
                self.assertFalse(b.active())
                # A DTR reconnect cannot clear the sticky hardware fault.
                b.call(0x803702a, 0);b.call(0x803702a, 1)
                b.pump();b.call('patch_gui_tick')
                self.assertEqual(b.gui_calls, before)
                b.acks.clear();b.send(make_packet(5));self.assertFalse(b.acks)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PipelineTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    (ROOT/'build/test-results/pipeline-test.json').write_text(json.dumps(dict(
        hardware_test=False, actual_patched_machine_code=True,
        tests_run=result.testsRun, successful=result.wasSuccessful(),
        checks=['active DMA source never overwritten', 'cross-half payload',
                'END completion fence', 'STOP/disconnect/lease/error/TX-full drain',
                'stuck DMA suppresses main and timer GUI callbacks']), indent=2)+'\n')
    sys.exit(not result.wasSuccessful())
