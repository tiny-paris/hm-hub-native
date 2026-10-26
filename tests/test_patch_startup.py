#!/usr/bin/env python3
"""Execute the real Keil scatter loader on original/candidate, including its
compressed initialized data and zero-fill descriptors, before C runtime/main.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import json,struct
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_THUMB,UC_MODE_MCLASS,UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_SP,UC_ARM_REG_PC

def load(name):
 u=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
 for a,n in [(0x8010000,0x70000),(0x10000000,0x10000),(0x20000000,0x20000)]:
  u.mem_map(a,n)
  if a!=0x8010000:u.mem_write(a,b'\xa5'*n)
 u.mem_write(0x8010000,(ROOT/name).read_bytes());u.reg_write(UC_ARM_REG_SP,0x1000af60)
 def hook(u,a,n,_):
  if a==0x8010292:u.emu_stop()
 u.hook_add(UC_HOOK_CODE,hook);u.emu_start(0x801019d,0x8010292,count=1000000)
 assert u.reg_read(UC_ARM_REG_PC)==0x8010292
 return bytes(u.mem_read(0x20000000,0x20000)),bytes(u.mem_read(0x10000000,0x10000))
a,ac=load('vendor/official-1.3.7/firmware-1.3.7.bin');b,bc=load('build/native-candidate.bin')
assert a[:0x1ef1c]==b[:0x1ef1c]
assert ac==bc
zi_end=0x4c0+struct.unpack_from('<I',(ROOT/'build/native-candidate.bin').read_bytes(),0x807dc28-0x8010000)[0]
assert a[0x1ef1c:zi_end]==b'\xa5'*(zi_end-0x1ef1c)
assert b[0x1ef1c:zi_end]==b'\0'*(zi_end-0x1ef1c)
assert a[zi_end:]==b[zi_end:]
assert b[0x2ef]==1
r=dict(actual_machine_code_scatter_loader=True,existing_sram_initializers_identical=True,ccm_heap_stack_identical=True,new_zero_fill_region=['0x2001ef1c',hex(0x20000000+zi_end)],beyond_reserved_region_untouched=True,initial_watchdog_flag=1,hardware_test=False)
(ROOT/'build/test-results/patch-startup-test.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
