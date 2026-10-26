#!/usr/bin/env python3
"""Display native-resolution color bars, fine lines and a moving frame ID."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import argparse
import json
import struct
import time
from PIL import Image, ImageDraw, ImageFont
from host.native_stream import Player
from host.stock_probe import Link


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', help='Serial port; auto-selects a single HM Hub')
    parser.add_argument('--seconds', type=float, default=45)
    parser.add_argument('--fps', type=int, default=5, choices=range(1, 31))
    parser.add_argument('--output', type=Path, default=ROOT/'build/native-pattern.json')
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error('--seconds must be positive')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default()
    link = Link(args.port, capture=False)
    player = Player(link)
    result = dict(resolution=[320, 170], flash_write_commands=0, physical_display_observation='pending')
    timings = []
    start = time.monotonic()
    frames = 0
    ready = False
    try:
        player.hello()
        ready = True
        while time.monotonic() - start < args.seconds:
            image = Image.new('RGB', (320, 170), 'black')
            draw = ImageDraw.Draw(image)
            for i, color in enumerate(['red', 'lime', 'blue', 'yellow', 'cyan', 'magenta', 'white', 'gray']):
                draw.rectangle((40*i, 0, 40*i+39, 31), fill=color)
            draw.text((12, 36), 'NATIVE 320 x 170', font=font, fill='white')
            draw.text((12, 61), f'FRAME {frames+1:04d}', font=font, fill='yellow')
            for y in range(95, 127):
                for x in range(8, 152):
                    image.putpixel((x, y), (255, 255, 255) if (x+y) % 2 else (0, 0, 0))
            for x in range(160, 312, 2):
                draw.line((x, 95, x, 126), fill='cyan')
            x = (frames * 7) % 320
            draw.line((x, 135, x, 169), fill='yellow')
            raw = b''.join(struct.pack('>H', ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))
                           for r, g, b in image.getdata())
            before = time.monotonic()
            player.frame(raw, frames+1)
            timings.append(time.monotonic()-before)
            frames += 1
            if frames == 1:
                image.save(args.output.with_suffix('.png'))
                print('First frame ACKed; inspect the physical panel.', flush=True)
            player.wait_until(start + frames / args.fps)
        result['status'] = 'completed'
    except KeyboardInterrupt:
        result['status'] = 'stopped'
    except Exception as exc:
        result.update(status='failed', error=str(exc))
        raise
    finally:
        if ready:
            try:
                player.stop()
                result['stop_acked'] = True
            except Exception as exc:
                result['stop_error'] = str(exc)
        link.port.close()
        result.update(frames_acked=frames, elapsed_seconds=time.monotonic()-start,
                      mean_transfer_seconds=sum(timings)/len(timings) if timings else None)
        args.output.write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
