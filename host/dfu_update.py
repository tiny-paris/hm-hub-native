#!/usr/bin/env python3
"""Reviewed official CDC updater. Default verifies image locally; --probe-boot
only sends official force-DFU (2/3); --write-original restores exact 1.3.7.
Only hash-pinned reviewed images can be selected. Native trial falls back to
original if its two-way capability handshake fails and watchdog re-enters DFU.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import argparse, hashlib, json, struct, time, zlib
import serial
from host.stock_probe import Link, packet, locate_port
ORIGINAL_SHA='5d6c874d1d37a5c20d5a4404502c81e93f4dbca261862b56c3f8041535536f77'
COMPATIBLE_RELEASE_SHA='162404c55af2ab737407c4532d483de1a397cd802ac4e74cd475a80b13902678'
NATIVE_VERSIONS=(0x81010307,0x81010308,0x81020307,0x81020308,0x81030307,0x81030308,0x81030309,0x8104030a)
# The web-compatible image deliberately reports the same version as stock.
# Verification / recovery must use the pinned image identity, not that word.
COMPATIBLE_CAPABILITIES=struct.pack('<HHBBBB',320,170,10,64,1,3)
NATIVE_CAPABILITIES={COMPATIBLE_RELEASE_SHA:COMPATIBLE_CAPABILITIES}

class Image:
    def __init__(self, data, expected_sha=ORIGINAL_SHA, *, native=False):
        if hashlib.sha256(data).hexdigest()!=expected_sha:
            raise ValueError('image does not match pinned reviewed SHA256')
        self.data=data
        self.version=struct.unpack_from('<I',data,4096)[0]
        self.native_caps=COMPATIBLE_CAPABILITIES if native else NATIVE_CAPABILITIES.get(expected_sha)
        self.high_water=0
        self.last=None
    def offer(self):
        return packet(2,b'\x01'+struct.pack('<III',self.version,len(self.data),zlib.crc32(self.data)))
    def requested(self,p):
        if len(p)!=256 or p[:2]!=b'\x02\x02' or zlib.crc32(p[:252])!=struct.unpack_from('<I',p,252)[0]:
            raise ValueError('invalid DFU request')
        offset,n=struct.unpack_from('<IH',p,2)
        if not 0<n<=244 or offset+n>len(self.data):
            raise ValueError(f'DFU request out of bounds: {offset}+{n}')
        if offset!=self.high_water and (offset,n)!=self.last:
            raise ValueError(f'DFU request nonsequential: {offset}, expected {self.high_water}')
        self.high_water=max(self.high_water,offset+n);self.last=(offset,n)
        return packet(2,b'\x02'+struct.pack('<IH',offset,n)+self.data[offset:offset+n])

def locate(path=None, serial_number=None):
    return locate_port(path, serial_number)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    g=ap.add_mutually_exclusive_group()
    g.add_argument('--probe-boot',action='store_true')
    g.add_argument('--write-original',action='store_true')
    g.add_argument('--write-native-compatible-release',action='store_true')
    ap.add_argument('--port',help='Serial port; auto-selects a single c019:0401 device')
    ap.add_argument('--serial-number',help='Optional serial filter when multiple Hubs are connected')
    ap.add_argument('--image-sha256',help='Explicit expected hash for a reviewed local compatible build')
    ap.add_argument('--already-in-boot',action='store_true')
    ap.add_argument('--seconds',type=float,default=25)
    ap.add_argument('--output',type=Path,default=ROOT/'build/dfu-session.json')
    a=ap.parse_args()
    if a.image_sha256 and not a.write_native_compatible_release:
        ap.error('--image-sha256 requires --write-native-compatible-release')
    target='build/native-bulk512-fast-pipeline-compatible-release.bin' if a.write_native_compatible_release else 'vendor/official-1.3.7/firmware-1.3.7.bin'
    target_sha=(a.image_sha256 or COMPATIBLE_RELEASE_SHA) if a.write_native_compatible_release else ORIGINAL_SHA
    im=Image((ROOT/target).read_bytes(),target_sha,native=a.write_native_compatible_release)
    if a.write_native_compatible_release and im.version!=0x10307:
        ap.error('compatible build must preserve official version 0x10307')
    out=dict(image_sha256=target_sha,image_bytes=len(im.data),image_version=hex(im.version),events=[],application_write_bytes=0,external_flash_commands=0,physical_display_observation='not observed')
    writing=a.write_original or a.write_native_compatible_release
    if not a.probe_boot and not writing:
        print(json.dumps(out,indent=2));return
    # Validate rollback input before entering DFU or making any device writes.
    Image((ROOT/'vendor/official-1.3.7/firmware-1.3.7.bin').read_bytes())
    a.output.parent.mkdir(parents=True,exist_ok=True)
    link=None;captures=bytearray();started=time.monotonic();last_handshake=0;offered=False;native_stage=0
    def event(kind,**kw):
        e=dict(t=round(time.monotonic()-started,3),kind=kind,**kw);out['events'].append(e);print(json.dumps(e),flush=True)
    def close():
        nonlocal link
        if link:
            captures.extend(link.capture);link.port.close();link=None
    try:
        if not a.already_in_boot:
            link=Link(locate(a.port,a.serial_number))
            p=link.request(1)
            hw,fw,flash=struct.unpack_from('<III',p,1)
            if hw!=0xa101 or fw not in (0x10307,0x80010307)+NATIVE_VERSIONS or flash!=8388608:raise ValueError('unexpected application identity')
            event('app_identity',hw=hex(hw),fw=hex(fw))
            link.send(packet(2,b'\x03'));event('force_dfu_sent')
        until=time.monotonic()+a.seconds
        while time.monotonic()<until:
            try:
                if link is None:
                    link=Link(locate(a.port,a.serial_number));event('serial_opened')
                p=link.recv(min(until,time.monotonic()+.25))
                if p:
                    if p[0]==2:

                        if p[1]!=2 or im.high_water%15360==0:
                            event('dfu_rx',sub=p[1],prefix=p[:16].hex())
                        if p[1]==1:
                            hw=struct.unpack_from('<I',p,2)[0]
                            if hw!=0xa101:raise ValueError(f'unexpected bootloader HW {hw:#x}')
                            if offered and im.native_caps is not None and im.high_water==len(im.data) and not out.get('native_handshake_verified'):
                                event('trial_recovery_restore_original')
                                im=Image((ROOT/'vendor/official-1.3.7/firmware-1.3.7.bin').read_bytes());offered=False
                                native_stage=0;out.pop('application_restart_verified',None)
                                out['fallback_to_original']=True;until=time.monotonic()+60
                            if writing and not offered:
                                link.send(im.offer());offered=True;event('reviewed_image_offered')
                        elif p[1]==2:
                            if not offered:raise ValueError('data request without image offer')
                            link.send(im.requested(p));out['application_write_bytes']=im.high_water
                            if im.high_water==len(im.data):event('all_image_bytes_requested')
                        else:raise ValueError('unreviewed bootloader response')
                    elif p[0]==1:
                        hw,fw,flash=struct.unpack_from('<III',p,1)
                        event('app_handshake',hw=hex(hw),fw=hex(fw),flash=flash)
                        if offered and im.high_water==len(im.data) and (hw,fw)==(0xa101,im.version):
                            out['application_restart_verified']=True
                            if im.native_caps is not None:
                                if not native_stage:
                                    from host.native_stream import make_packet
                                    link.send(make_packet(1));native_stage=1;event('native_hello_sent')
                            else:break
                    elif p[:8]==b'\x04\x81HMS1\x01\0' and p[19]==0:
                        from host.native_stream import make_packet
                        ident=struct.unpack_from('<I',p,8)[0]
                        caps_ok=im.native_caps is not None and p[20:28]==im.native_caps and struct.unpack_from('<H',p,16)[0]==8
                        if native_stage==1 and ident==0 and caps_ok:
                            link.send(make_packet(1,0x434f4d4d));native_stage=2;event('native_commit_sent')
                        elif native_stage==2 and ident==0x434f4d4d and caps_ok:
                            out['native_handshake_verified']=True;event('native_handshake_verified');break
                    elif p[0]==10:
                        event('device_log',text=p[2:2+min(p[1],250)].decode(errors='replace'))
                # Handshake is read-only, also discovers if bootloader returns to app.
                if time.monotonic()-last_handshake>2:
                    link.send(packet(1));last_handshake=time.monotonic()
            except (serial.SerialException,OSError) as exc:
                event('serial_reconnect',error=str(exc));close();time.sleep(.2)
        verified=out.get('application_restart_verified') and (im.native_caps is None or out.get('native_handshake_verified'))
        out['installed_image_sha256']=hashlib.sha256(im.data).hexdigest() if verified else None
        out['installed_image_version']=hex(im.version) if verified else None
        out['status']='observed' if a.probe_boot else ('restored_original' if verified and out.get('fallback_to_original') else ('installed_verified' if verified else 'not_verified'))
    except Exception as exc:
        out['status']='failed';out['error']=str(exc);raise
    finally:
        close();a.output.with_suffix('.rx.bin').write_bytes(captures)
        out['elapsed_seconds']=round(time.monotonic()-started,3)
        a.output.write_text(json.dumps(out,indent=2)+'\n')
        print(json.dumps({k:v for k,v in out.items() if k!='events'},indent=2))
    return 0 if out['status'] in ('observed','installed_verified') else 1
if __name__=='__main__':raise SystemExit(main())
