#!/usr/bin/env python3
"""Fetch/extract the hash-pinned official input without executing JavaScript."""
import argparse
import hashlib
from pathlib import Path
import re
import struct
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://hmlab.tech/hub/1.3.7/app_bin_data.js'
SOURCE_SHA = '9872b9bf33ed436582036e7ec79fbb5131f2f91f220f7b8d2055eebe6b6dd63f'
FIRMWARE_SHA = '5d6c874d1d37a5c20d5a4404502c81e93f4dbca261862b56c3f8041535536f77'
OUTPUT = ROOT / 'vendor/official-1.3.7/firmware-1.3.7.bin'


def extract(source):
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA:
        raise ValueError('official JavaScript SHA-256 mismatch; refusing changed input')
    match = re.search(rb'new Uint8Array\(\[(.*?)\]\)', source, re.S)
    if match is None:
        raise ValueError('official firmware array not found')
    data = bytes(int(token, 16) for token in re.findall(rb'0x([0-9a-fA-F]{2})\b', match[1]))
    if len(data) != 450240 or hashlib.sha256(data).hexdigest() != FIRMWARE_SHA:
        raise ValueError('extracted firmware length/SHA-256 mismatch')
    if struct.unpack_from('<I', data, 0x1000)[0] != 0x10307:
        raise ValueError('unexpected firmware version')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--download', action='store_true', help='Fetch the fixed official HTTPS URL')
    source.add_argument('--source', type=Path, help='Use an already downloaded app_bin_data.js')
    args = parser.parse_args()
    if args.download:
        if OUTPUT.is_file() and hashlib.sha256(OUTPUT.read_bytes()).hexdigest() == FIRMWARE_SHA:
            print(f'Official input already verified: {OUTPUT}')
            return
        with urlopen(URL, timeout=30) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            parser.error('official response exceeds download limit')
    else:
        raw = args.source.read_bytes()
    data = extract(raw)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(data)
    print(f'Verified official 1.3.7: {len(data)} bytes; SHA-256 {FIRMWARE_SHA}')


if __name__ == '__main__':
    main()
