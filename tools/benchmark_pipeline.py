#!/usr/bin/env python3
"""Compare asynchronous vs serialized DMA with identical candidate Thumb code.

This is a scheduling model, NOT a hardware FPS measurement: each emulated
instruction costs a configurable number of cycles, SPI costs bytes*8/42MHz,
and LCD setup costs 8us. USB/host delays, bus contention and IRQs are omitted.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import argparse
import hashlib
import json
from tests.test_patched_firmware import Board
from host.native_stream import Player, FRAME_BYTES


class TimedBoard(Board):
    def __init__(self, serialized, cycles):
        self.serialized = serialized
        self.instruction_ns = cycles * 1e9 / 168_000_000
        self.elapsed_ns = 0
        self.dma_due = 0
        self.instructions = 0
        super().__init__()
        self.fragment = 512

    def hook(self, u, address, size, user):
        self.instructions += 1
        self.elapsed_ns += self.instruction_ns
        if self.pending_dma and self.elapsed_ns >= self.dma_due:
            self.complete_dma()
        if address == 0x801c860:
            self.ret(int(self.pending_dma is None))
            return
        super().hook(u, address, size, user)
        if address == 0x8024070:
            self.elapsed_ns += 8_000  # modeled LCD command/setup cost
            self.dma_due = self.elapsed_ns + len(self.pending_dma[3]) * 8e9 / 42_000_000
            if self.serialized:
                self.elapsed_ns = self.dma_due
                self.complete_dma()


def benchmark(cycles):
    data = bytes((i * 7 ^ i // 640 * 13) & 255 for i in range(FRAME_BYTES))
    result = {}
    for serialized in (True, False):
        b = TimedBoard(serialized, cycles);p = Player(b);p.hello()
        start = b.elapsed_ns;instructions = b.instructions
        p.frame(data, 1)
        assert b.screen == data and b.pending_dma is None
        result['serialized' if serialized else 'pipeline'] = dict(
            modeled_frame_ms=(b.elapsed_ns-start)/1e6,
            thumb_instructions=b.instructions-instructions,
            lcd_transfers=len(b.rects), cpu_writes_during_dma=b.overlap_writes)
    assert result['pipeline']['cpu_writes_during_dma'] > 0
    assert result['pipeline']['modeled_frame_ms'] < result['serialized']['modeled_frame_ms']
    result['modeled_time_reduction_percent'] = 100 * (1 - result['pipeline']['modeled_frame_ms'] / result['serialized']['modeled_frame_ms'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cycles-per-instruction', type=float, default=2)
    args = parser.parse_args()
    if not 0.1 <= args.cycles_per_instruction <= 20:
        parser.error('cycles per instruction must be between 0.1 and 20')
    result = dict(hardware_test=False, actual_patched_machine_code=True,
                  candidate_sha256=hashlib.sha256((ROOT/'build/native-candidate.bin').read_bytes()).hexdigest(),
                  model=dict(core_hz=168_000_000, spi_hz=42_000_000,
                             cycles_per_instruction=args.cycles_per_instruction,
                             lcd_setup_us=8, usb_host_irq_time_included=False),
                  comparison=benchmark(args.cycles_per_instruction))
    path = ROOT/'build/test-results/pipeline-model-benchmark.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':main()
