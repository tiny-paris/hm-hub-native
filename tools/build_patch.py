#!/usr/bin/env python3
"""Produce an auditable candidate, never open USB. All stock bytes SHA pinned.
Binary append is limited by the actual bootloader write bound 0x08080000.
Hardware evidence is recorded separately for each pinned candidate SHA.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import argparse,hashlib,json,struct,subprocess
from elftools.elf.elffile import ELFFile
from capstone import Cs,CS_ARCH_ARM,CS_MODE_THUMB
BASE=0x08010000
SHA='5d6c874d1d37a5c20d5a4404502c81e93f4dbca261862b56c3f8041535536f77'

def branch(src,dst,link=False):
    d=dst-(src+4)
    assert d%2==0 and -(1<<24)<=d<(1<<24)
    s=(d>>24)&1;i1=(d>>23)&1;i2=(d>>22)&1
    j1=(1^i1^s);j2=(1^i2^s)
    return struct.pack('<HH',0xf000|(s<<10)|((d>>12)&1023),
        (0xd000 if link else 0x9000)|(j1<<13)|(j2<<11)|((d>>1)&2047))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--release',action='store_true');ap.add_argument('--bulk512',action='store_true');ap.add_argument('--fast',action='store_true')
    ap.add_argument('--stock-version',action='store_true',help='Preserve official 1.3.7 identity for its web driver; detect HMS1 support via HELLO')
    args=ap.parse_args()
    if args.fast and not args.bulk512:ap.error('--fast requires --bulk512')
    if args.stock_version and not (args.release and args.bulk512 and args.fast):ap.error('--stock-version requires --release --bulk512 --fast')
    out=ROOT/'build';out.mkdir(exist_ok=True)
    flags=['clang','--target=arm-none-eabi','-mcpu=cortex-m4','-mthumb','-O2' if args.fast else '-Oz','-ffreestanding','-fno-builtin','-fno-unwind-tables','-fno-asynchronous-unwind-tables','-ffunction-sections','-fdata-sections','-Wall','-Wextra','-Werror']
    if not args.release:flags+=['-DHM_TRIAL=1']
    if args.bulk512:flags+=['-DHM_BULK512=1']
    if args.fast:flags+=['-DHM_TRUST_DISPATCH_CRC=1','-DHM_USE_STOCK_CRC_TABLE=1','-DHM_FAST_COPIES=1']
    objs=[]
    for name in ['stream_receiver.c','cdc_stream_rx.c','board_patch.c','patch_entry.S']:
        obj=out/(name+'.o');subprocess.run(flags+['-c',str(ROOT/'firmware'/name),'-o',str(obj)],check=True);objs.append(str(obj))
    elf=out/'native-candidate.elf'
    subprocess.run(['ld.lld','-T',str(ROOT/'firmware/board_patch.ld'),'-Map='+str(out/'native-candidate.map'),'-o',str(elf)]+objs,check=True)
    original=(ROOT/'vendor/official-1.3.7/firmware-1.3.7.bin').read_bytes();assert hashlib.sha256(original).hexdigest()==SHA
    candidate=bytearray(original);syms={};patches=[]
    with elf.open('rb') as f:
        e=ELFFile(f)
        for s in e.get_section_by_name('.symtab').iter_symbols():syms[s.name]=s['st_value']
        for sec in e.iter_sections():
            if not (sec['sh_flags'] & 2) or sec['sh_type']=='SHT_NOBITS' or not sec['sh_size']:continue
            a=sec['sh_addr'];data=sec.data();assert 0x807dec0<=a and a+len(data)<=0x8080000
            pos=a-BASE
            if pos+len(data)>len(candidate):candidate.extend(b'\xff'*(pos+len(data)-len(candidate)))
            candidate[pos:pos+len(data)]=data
    def change(a,data,description):
        pos=a-BASE;before=bytes(candidate[pos:pos+len(data)]);candidate[pos:pos+len(data)]=data
        patches.append(dict(address=hex(a),before=before.hex(),after=data.hex(),description=description))
    def hook(a,name,link=False):
        dst=syms[name]&~1;data=branch(a,dst,link)
        ins=list(Cs(CS_ARCH_ARM,CS_MODE_THUMB).disasm(data,a));assert len(ins)==1 and int(ins[0].op_str,16)==dst
        change(a,data,('BL ' if link else 'B.W ')+name)
    hook(0x80349c4,'patch_init')
    hook(0x803702a,'patch_connect')
    hook(0x801434e,'patch_usb_rx')
    hook(0x8034aa4,'patch_rx_pop',True)
    hook(0x8034bc6,'patch_rx_pop',True)
    hook(0x8034b10,'patch_dispatch')
    hook(0x8034634,'patch_gui',True)
    hook(0x8034644,'patch_flash_service',True)
    hook(0x8034e14,'patch_tx')
    hook(0x80362ae,'patch_watchdog',True)
    hook(0x80361d4,'patch_gui_tick',True)
    if args.bulk512:
        hook(0x801432c,'patch_usb_buffer_init')
        hook(0x8014408,'patch_usb_descriptor')
        # Change only CDC bulk endpoint and receive sizes, never HID sizes.
        for a,before,after in [(0x8013034,'4ff48075','4ff40075'),
                (0x8013106,'4ff48073','4ff40073'),
                (0x8012e46,'4ff48073','4ff40073')]:
            assert original[a-BASE:a-BASE+4]==bytes.fromhex(before)
            change(a,bytes.fromhex(after),'CDC High Speed bulk MPS/receive = 512')
    # Extend only normal-SRAM ZI descriptor; preserve other compressed data,
    # CCM heap/stack, vector table, reset handler and original version address.
    assert struct.unpack_from('<I',original,0x807dc28-BASE)[0]==0x1ea5c
    zi_end=0x2001f200 if args.bulk512 else 0x2001f000
    change(0x807dc28,struct.pack('<I',zi_end-0x200004c0),'extend SRAM ZI end to '+hex(zi_end))
    # The official 1.3.7 web driver automatically flashes on any version
    # mismatch. Keep its identity in the compatible release, and use the
    # HMS1 capability handshake / pinned image SHA to identify the extension.
    version=0x10307 if args.stock_version else (0x8104030a if args.release else 0x81040307)
    if not args.stock_version:
        change(0x8011000,struct.pack('<I',version),'private native stream version')
    path=out/'native-candidate.bin';path.write_bytes(candidate)
    archive='native-bulk512' if args.bulk512 else 'native'
    if args.fast:archive+='-fast'
    archive+='-pipeline'
    if args.stock_version:archive+='-compatible'
    archive+='-release.bin' if args.release else '-trial.bin'
    (out/archive).write_bytes(candidate)
    review=dict(trial=not args.release,bulk512=args.bulk512,fast=args.fast,stock_version=args.stock_version,lcd_pipeline_rows=5,lcd_buffers=2,version=hex(version),zi_end=hex(zi_end),status='candidate_not_hardware_validated',official_sha256=SHA,
        candidate_sha256=hashlib.sha256(candidate).hexdigest(),bytes=len(candidate),
        patch_code_bytes=len(candidate)-len(original),bss_start=hex(syms['__patch_bss_start']),bss_end=hex(syms['__patch_bss_end']),
        hooks=patches,unresolved=['exact MCU manufacturer/model','LCD ownership under every stock interrupt interaction','bad cold-boot or interrupted-update recovery without SWD','this SHA requires its own recorded target validation'])
    (ROOT/'build/candidate-review.json').write_text(json.dumps(review,indent=2)+'\n')
    (out/'native-candidate.symbols.json').write_text(json.dumps(syms,indent=2)+'\n')
    print(json.dumps({k:v for k,v in review.items() if k!='hooks'},indent=2))
if __name__=='__main__':main()
