#!/usr/bin/env python3
"""Run actual patched Thumb image with only physical peripherals substituted."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import json,struct,zlib,time
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_THUMB,UC_MODE_MCLASS,UC_HOOK_CODE,UC_HOOK_MEM_WRITE
from unicorn.arm_const import *
from host.native_stream import Player,make_packet
REGS=[UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3]
class Board:
 def __init__(self, image='build/native-candidate.bin', symbols='build/native-candidate.symbols.json'):
  self.u=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
  for a,n in [(0x8000000,0x100000),(0x10000000,0x10000),(0x20000000,0x80000)]:self.u.mem_map(a,n)
  self.u.mem_write(0x8010000,(ROOT/image).read_bytes())
  self.syms=json.loads((ROOT/symbols).read_text())
  self.usb_buf=self.syms.get('usb_buffer',0x20000ff0)
  self.acks=[];self.screen=bytearray(108800);self.rects=[];self.arm_calls=0;self.gui_calls=0;self.watchdog_feeds=0;self.ms=100
  self.busy=False;self.lcd_ready=True;self.fragment=256;self.tick_ready_poll=False
  self.pending_dma=None;self.dma_polls=0;self.overlap_writes=0
  self.u.hook_add(UC_HOOK_CODE,self.hook)
  self.u.hook_add(UC_HOOK_MEM_WRITE,self.memory_write,begin=0x20004ce0,end=0x200065df)
  self.set_ms(100);self.u.mem_write(0x200002ef,b'\1');self.u.mem_write(0x200078fd,b'\1')
  self.call(0x80349c4);self.call(0x803702a,1);self.call('patch_gui')
 def set_ms(self,ms):self.ms=ms;self.u.mem_write(0x2001ea48,struct.pack('<Q',ms))
 def ret(self,v=0):self.u.reg_write(UC_ARM_REG_R0,v);self.u.reg_write(UC_ARM_REG_PC,self.u.reg_read(UC_ARM_REG_LR))
 def memory_write(self,u,access,address,size,value,_):
  if self.pending_dma:
   p,y,rows,data=self.pending_dma
   assert address+size<=p or address>=p+len(data), 'CPU overwrote active DMA buffer'
   self.overlap_writes+=1
 def complete_dma(self):
  if self.pending_dma:
   p,y,rows,data=self.pending_dma
   assert bytes(self.u.mem_read(p,len(data)))==data
   self.screen[y*640:(y+rows)*640]=data;self.pending_dma=None
 def hook(self,u,a,n,_):
  if a==0x80ff000:u.emu_stop()
  elif a==0x801c860:
   if self.tick_ready_poll:self.set_ms(self.ms+1)
   if self.pending_dma and self.lcd_ready:
    self.dma_polls-=1
    if self.dma_polls<=0:self.complete_dma()
   self.ret(int(self.lcd_ready and self.pending_dma is None))
  elif a==0x8024070:
   x,y,x1,y1=[u.reg_read(r) for r in REGS];p=struct.unpack('<I',u.mem_read(u.reg_read(UC_ARM_REG_SP),4))[0]
   rows=y1-y+1
   assert (x,x1)==(0,319) and rows in (5,10) and y+rows<=170
   assert p in (0x20004ce0,0x20005960) and not self.pending_dma
   self.pending_dma=(p,y,rows,bytes(u.mem_read(p,rows*640)));self.dma_polls=3
   self.rects.append((y,y1));self.ret()
  elif a==0x8012df4:
   assert u.reg_read(UC_ARM_REG_R0)==0x20004970 and u.reg_read(UC_ARM_REG_R1)==self.usb_buf;self.ret()
  elif a==0x8012e24:self.arm_calls+=1;self.ret(1)
  elif a==0x8036e52:self.ret(int(self.busy))
  elif a==0x8036eb4:
   assert u.reg_read(UC_ARM_REG_R1)==256
   packet=bytes(u.mem_read(u.reg_read(UC_ARM_REG_R0),256))
   if packet[:2] in (b'\x04\x84',b'\x04\x85') and not packet[19]:assert not self.pending_dma
   self.acks.append(packet);self.ret()
  elif a in (0x8023464,0x801ccc4):
   assert not self.pending_dma, 'GUI resumed before DMA completed'
   self.gui_calls+=1;self.ret()
  elif a==0x8037042:self.watchdog_feeds+=1;self.ret()
  elif a in (0x801af44,0x80125dc):self.ret()
 def call(self,a,*args):
  if isinstance(a,str):a=self.syms[a]&~1
  self.u.reg_write(UC_ARM_REG_SP,0x1000af60);self.u.reg_write(UC_ARM_REG_LR,0x80ff001)
  for r,v in zip(REGS,args):self.u.reg_write(r,v)
  self.u.emu_start(a|1,0x80ff000,count=4000000)
  assert self.u.reg_read(UC_ARM_REG_PC)==0x80ff000,hex(self.u.reg_read(UC_ARM_REG_PC))
  return self.u.reg_read(UC_ARM_REG_R0)
 def receive(self,p):
  self.u.mem_write(self.usb_buf,p);self.u.mem_write(0x20070000,struct.pack('<I',len(p)))
  self.call(0x801434e,self.usb_buf,0x20070000)
 def pump(self):self.call('patch_gui');self.call(0x8034a8c);self.call(0x8034e14)
 def send(self,p):
  for i in range(0,len(p),self.fragment):self.receive(p[i:i+self.fragment])
  self.pump()
 def send_many(self,packets):
  # Honor the target's OUT rearm flag, as USB NAK does on real hardware.
  data=b''.join(packets)
  for i in range(0,len(data),self.fragment):
   while self.u.mem_read(self.syms['armed'],1)==b'\0':self.pump()
   self.receive(data[i:i+self.fragment])
  self.pump()
 def recv(self,until):
  while not self.acks and struct.unpack('<I',self.u.mem_read(self.syms['rx']+16,4))[0]:self.pump()
  self.call(0x8034e14)
  if self.acks:return self.acks.pop(0)
  return None
 def active(self):return bool(self.call('hm_stream_active'))

def main():
 start=time.monotonic();b=Board();player=Player(b);b.fragment=63;player.hello()
 # Even a build trusting dispatcher CRC must reject corrupted outer packets
 # before patch_command. Exercise the real original dispatcher, not its API.
 bad=bytearray(make_packet(1,999));bad[252]^=1;b.send(bytes(bad));assert not b.acks
 data=bytes(((x*7)^(x//640)*13)&255 for x in range(108800));player.frame(data,1)
 assert b.screen==data and len(b.rects)==34 and b.active() and b.overlap_writes>0
 if b.usb_buf!=0x20000ff0:
  b.fragment=512;player.frame(data[::-1],2);assert b.screen==data[::-1] and len(b.rects)==68
 g=b.gui_calls;b.pump();assert b.gui_calls==g
 player.stop();b.pump();assert not b.active() and b.gui_calls>g
 if player.features&2:
  b.u.mem_map(0x40010000,0x20000);b.u.mem_map(0xe0000000,0x100000)
  stats=player.stats();assert stats['lcd_calls']==68 and stats['usb_max_receive']==512
 # Full RX queue is never rearmed until main loop pops; 21st transfer remains
 # on the host rather than getting acknowledged and dropped by our ISR.
 c=Board();p=make_packet(1)
 for _ in range(20):c.receive(p)
 assert c.u.mem_read(c.syms['armed'],1)==b'\0'
 c.pump();assert c.u.mem_read(c.syms['armed'],1)==b'\1'
 # Bounded dispatch: exactly eight pop attempts per main iteration.
 count=struct.unpack('<I',c.u.mem_read(c.syms['rx']+16,4))[0];assert count==12,count
 # Stale main-loop heartbeat stops the timer's WDT keepalive.
 c.call('patch_watchdog');feeds=c.watchdog_feeds;c.set_ms(c.ms+5001);c.call('patch_watchdog');assert c.watchdog_feeds==feeds
 # Lease timeout releases GUI; physical disconnect queues release in main.
 player.send(make_packet(2,2));player.receive_ack(2,2,0);b.set_ms(b.ms+1001);b.pump();assert not b.active()
 player.send(make_packet(2,3));player.receive_ack(2,3,0);b.call(0x803702a,0);b.pump();assert not b.active()
 # DMA not ready causes bounded failure and requests watchdog recovery.
 d=Board();dp=Player(d);dp.hello();d.lcd_ready=False;d.tick_ready_poll=True
 d.send(make_packet(2,1));assert not d.active();d.pump();assert d.u.mem_read(0x200002ef,1)==b'\0'
 # TX busy must not consume queued replies and must not spin.
 e=Board();e.busy=True;e.send(make_packet(1));assert not e.acks;e.busy=False;e.call(0x8034e14);assert len(e.acks)==1
 r=dict(hardware_test=False,actual_patched_machine_code=True,pixel_exact_full_frame=True,usb_fragment_bytes=[63,512] if b.usb_buf!=0x20000ff0 else [63],lcd_stripes=len(b.rects),rx_full_backpressure=True,main_loop_packet_budget=8,watchdog_stale_heartbeat=True,lease_and_disconnect_release=True,dma_timeout_requests_recovery=True,tx_busy_nonblocking=True,elapsed_seconds=time.monotonic()-start)
 (ROOT/'build/test-results/patched-firmware-test.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
if __name__=='__main__':main()
