#!/usr/bin/env python3
"""Execute original class-init/descriptor/rearm paths with patched instructions.
Only lowest-level USB endpoint/open/prepare functions are substituted.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import json,struct
from tests.test_patched_firmware import Board,ROOT,REGS
from tests.test_patch_startup import load
from unicorn.arm_const import *
class USB(Board):
 def __init__(self):
  self.opens=[];self.prepares=[];self.rx_pointers=[];self.real_rearm=False
  super().__init__()
 def hook(self,u,a,n,x):
  if a==0x8012df4:
   p=u.reg_read(UC_ARM_REG_R1);self.rx_pointers.append(p)
   u.mem_write(0x2006e124,struct.pack('<I',p));self.ret(0)
  elif a==0x8013da8:
   self.opens.append(tuple(u.reg_read(r) for r in REGS[1:]));self.ret(0)
  elif a==0x8013ecc:
   self.prepares.append(tuple(u.reg_read(r) for r in REGS[1:]));self.ret(0)
  elif a==0x8012e24 and self.real_rearm:pass
  else:super().hook(u,a,n,x)
b=USB();mem,_=load('vendor/official-1.3.7/firmware-1.3.7.bin');b.u.mem_write(0x20000000,mem[:0x4c0])
assert b.usb_buf==0x2001f000
for speed,expected in ((1,512),(0,64),(1,512)):
 assert b.call(0x8014408,0x20070010,speed)==0x73
 assert struct.unpack('<H',b.u.mem_read(0x200001ad,2))[0]==expected
 assert struct.unpack('<H',b.u.mem_read(0x200001b4,2))[0]==expected
assert struct.unpack('<I',b.u.mem_read(0x20070010,4))[0]==0x20000144
ctx=0x20004970;state=0x2006e000
b.u.mem_write(ctx+0x18,struct.pack('<I',0));b.u.mem_write(ctx+0x10,struct.pack('<I',0x2006f000));b.u.mem_write(0x2006f004,struct.pack('<I',state));b.u.mem_write(ctx+8,b'\1')
b.u.mem_write(state+0x10c,struct.pack('<I',0x2006d000));b.u.mem_write(0x2006d008,struct.pack('<I',0x8014323))
b.call(0x8014322);assert b.rx_pointers[-1]==b.usb_buf
assert b.call(0x8012fac,ctx)==0
assert (2,2,512) in b.opens and (0x82,2,512) in b.opens
assert (1,3,256) in b.opens and (0x81,3,256) in b.opens
assert (2,b.usb_buf,512) in b.prepares
b.real_rearm=True
assert b.call(0x8012e24,ctx)==1 and b.prepares[-1]==(2,b.usb_buf,512)
b.u.mem_write(ctx+8,b'\0')
assert b.call(0x8012e24,ctx)==1 and b.prepares[-1]==(2,b.usb_buf,64)
r=dict(hardware_test=False,actual_machine_code=True,hs_fs_hs_descriptors_correct=True,initial_usb_buffer=hex(b.usb_buf),initial_bulk_endpoints_512=True,initial_receive_512=True,rearm_hs512_fs64=True,hid_endpoints_unchanged=True,opens=b.opens,prepares=b.prepares)
(ROOT/'build/test-results/usb512-machine-test.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
