#!/usr/bin/env python3
"""Actual stock readback code + patched TX queue reservation; mock SPI/LVGL only."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import json,struct,zlib
from tests.test_patched_firmware import Board,ROOT
from host.native_stream import make_packet
class ReadbackBoard(Board):
 def hook(self,u,a,n,x):
  from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2
  if a==0x801aa0c:
   off=u.reg_read(UC_ARM_REG_R0);dest=u.reg_read(UC_ARM_REG_R1);size=u.reg_read(UC_ARM_REG_R2)
   u.mem_write(dest,bytes((off+i)&255 for i in range(size)));self.ret()
  elif a==0x801cbb0:self.ret()
  elif a==0x8036e5e:self.ret(1)
  else:super().hook(u,a,n,x)
 def setup(self,count):
  self.busy=True
  for _ in range(count):
   self.u.mem_write(0x20070000,make_packet(1));assert self.call('hm_cdc_tx',0x20070000)==0
  self.u.mem_write(0x20001950,struct.pack('<I',240));self.u.mem_write(0x20004960,struct.pack('<I',240))
 def remaining(self):return struct.unpack('<I',self.u.mem_read(0x20001950,4))[0]
 def count(self):return struct.unpack('<I',self.u.mem_read(0x2001e9bc,4))[0]
 def drain(self):
  self.busy=False
  while self.count():self.call('patch_tx')
  for p in self.acks:assert zlib.crc32(p[:252])==struct.unpack_from('<I',p,252)[0]

old=ReadbackBoard();old.setup(19);old.call(0x801ac24)
assert old.remaining()==0 and old.count()==20
old.drain();assert any(p[:2]==b'\x08\x03' for p in old.acks)
assert not any(p[:2]==b'\x08\x04' for p in old.acks)
new=ReadbackBoard();new.setup(17);new.call('patch_flash_service')
assert new.remaining()==240 and new.count()==17
new.busy=False;new.call('patch_tx');new.busy=True
assert new.count()==16
new.call('patch_flash_service');assert new.remaining()==0 and new.count()==18
new.drain();data=[p for p in new.acks if p[:2]==b'\x08\x03'];end=[p for p in new.acks if p[:2]==b'\x08\x04']
assert len(data)==len(end)==1 and data[0][8:248]==bytes(range(240))
result=dict(actual_patched_and_stock_machine_code=True,hardware_test=False,old_missing_end_reproduced=True,full_tx_defers_readback_without_offset_advance=True,data_and_end_preserved=True,all_reply_crcs_valid=True,mocked_physical_spi_read=True)
(ROOT/'build/test-results/readback-backpressure-machine-test.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
