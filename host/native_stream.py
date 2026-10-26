#!/usr/bin/env python3
"""Native 320x170 RGB565 video over capability-gated CDC; never writes Flash."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import argparse
from collections import deque
import ctypes
import hashlib
import json
import struct
import subprocess
import time
import zlib
from host.stock_probe import Link

FRAME_BYTES = 108800


def make_packet(sub, frame=0, offset=0, data=b'', ack=True):
    if len(data)>232:
        raise ValueError('payload too long')
    head=struct.pack('<BB4sHIIHBB',4,sub,b'HMS1',1,frame,offset,len(data),int(ack),0)
    head=(head+data).ljust(252,b'\0')
    return head+struct.pack('<I',zlib.crc32(head))


class Simulator:
    def __init__(self):
        self.lib=ctypes.CDLL(str(ROOT/'build/receiver_sim.so'))
        self.lib.sim_init()
        self.start=time.monotonic()
    def send(self,p):
        self.lib.sim_send(p,int((time.monotonic()-self.start)*1000)&0xffffffff)
    def recv(self,until):
        p=ctypes.create_string_buffer(256)
        if self.lib.sim_ack(p):return p.raw
        return None
    def screen(self):
        p=ctypes.create_string_buffer(FRAME_BYTES);self.lib.sim_screen(p);return p.raw
    def close(self):pass


class Player:
    def __init__(self,link):self.link=link;self.packets=0;self.window=8;self.features=0
    def send(self,p):self.link.send(p);self.packets+=1
    def send_many(self,packets):
        if hasattr(self.link,'send_many'):
            self.link.send_many(packets);self.packets+=len(packets)
        else:
            for p in packets:self.send(p)
    def receive_ack(self,sub,frame,offset=None):
        until=time.monotonic()+2
        while time.monotonic()<until:
            p=self.link.recv(until)
            if p is None:break
            if len(p)!=256 or zlib.crc32(p[:252])!=struct.unpack_from('<I',p,252)[0]:continue
            if p[:8]!=bytes([4,sub|128])+b'HMS1\x01\0':continue
            if struct.unpack_from('<I',p,8)[0]!=frame:continue
            if p[19]:raise RuntimeError(f'receiver rejected operation {sub}: status {p[19]}')
            if offset is not None and struct.unpack_from('<I',p,12)[0]!=offset:
                raise RuntimeError('receiver ACK offset mismatch')
            return p
        if sub==1:
            raise RuntimeError('No HMS1 capability reply; device support is not confirmed. Stock 1.3.7 does not implement HMS1.')
        raise RuntimeError(f'Timed out waiting for HMS1 ACK: operation={sub}, frame={frame}, offset={offset}. No display success is inferred.')
    def hello(self):
        self.send(make_packet(1));p=self.receive_ack(1,0)
        width,height,rows,window,fmt=struct.unpack_from('<HHBBB',p,20)
        if (width,height,rows,fmt)!=(320,170,10,1) or not 1<=window<=128:
            raise RuntimeError('unsupported receiver capabilities')
        self.window=window
        self.features=p[27]
        if hasattr(self.link,'bulk_join'):self.link.bulk_join=bool(self.features&1)
    def stats(self):
        if not self.features&2:return None
        self.send(make_packet(6));p=self.receive_ack(6,0)
        values=struct.unpack_from('<16I',p,20)
        if values[0]!=0x31545348:raise RuntimeError('invalid stats response')
        keys=('magic','uptime_ms','lcd_ms','receiver_ms','lcd_calls','receiver_calls',
              'usb_callbacks','usb_bytes','usb_max_receive','rcc_cr','rcc_pllcfgr',
              'rcc_cfgr','spi1_cr1','cpu_id','systick_reload','reserved')
        return dict(zip(keys,values))
    def frame(self,data,index):
        if len(data)!=FRAME_BYTES:raise ValueError('incomplete RGB565 frame')
        self.send(make_packet(2,index));self.receive_ack(2,index,0)
        batch=[]
        for stripe in range(0,FRAME_BYTES,6400):
            offset=stripe
            while offset<stripe+6400:
                n=min(232,stripe+6400-offset)
                ack=len(batch)+1==self.window or offset+n==FRAME_BYTES
                batch.append(make_packet(3,index,offset,data[offset:offset+n],ack))
                offset+=n
                if ack:
                    self.send_many(batch);self.receive_ack(3,index,offset);batch=[]
        self.send(make_packet(4,index,FRAME_BYTES,struct.pack('<I',zlib.crc32(data))))
        self.receive_ack(4,index,FRAME_BYTES)
    def stop(self):
        self.send(make_packet(5));self.receive_ack(5,0)
    def wait_until(self,deadline):
        # Stock telemetry continues at 50 Hz. During long low-fps delays,
        # drain unsolicited packets so the tty/IN/TX queues cannot fill.
        if not isinstance(self.link,Link):
            time.sleep(max(0,deadline-time.monotonic()));return
        while deadline-time.monotonic()>.05:
            self.link.recv(min(deadline,time.monotonic()+.02))
        time.sleep(max(0,deadline-time.monotonic()))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('video',type=Path,nargs='?')
    ap.add_argument('--simulate',action='store_true')
    ap.add_argument('--probe-only',action='store_true')
    ap.add_argument('--port',help='Serial port; auto-selects a single c019:0401 device')
    ap.add_argument('--fps',type=int,default=12,choices=range(1,31))
    ap.add_argument('--seconds',type=float)
    ap.add_argument('--loop',action='store_true',help='Repeat the complete video continuously until stopped')
    ap.add_argument('--realtime',action='store_true',help='Pace at requested frame rate; do not skip frames')
    ap.add_argument('--output',type=Path,default=ROOT/'build/native-stream-result.json')
    a=ap.parse_args()
    if not a.probe_only and (a.video is None or not a.video.is_file()):ap.error('video required')
    if a.seconds is not None and a.seconds<=0:ap.error('--seconds must be positive')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    link=Simulator() if a.simulate else Link(a.port,capture=False)
    player=Player(link);decoder=None;ready=False;frames=0;play_start=None
    transfers=deque(maxlen=2048) if a.loop else []
    transfer_total=0.;transfer_min=float('inf');transfer_max=0.
    result=dict(mode='C receiver simulation' if a.simulate else 'CDC hardware',
        resolution=[320,170],requested_fps=a.fps,realtime_pacing=a.realtime,loop=a.loop,video=str(a.video) if a.video else None,physical_display_observation='not observed',flash_writes=0)
    start=time.monotonic()
    try:
        if isinstance(link,Link):
            info=link.request(1)
            hw,fw,flash=struct.unpack_from('<III',info,1)
            result['device']=dict(hardware=hex(hw),firmware=hex(fw),flash_bytes=flash)
            result['config_before_hex']=link.request(3,b'\x01')[6:34].hex()
        player.hello();ready=True
        result['ack_window_packets']=player.window
        result['receiver_stats_before']=player.stats()
        if not a.probe_only:
            command=['ffmpeg','-nostdin','-v','error']
            if a.loop:command+=['-stream_loop','-1']
            command+=['-i',str(a.video),'-an','-vf',
                     f'fps={a.fps},scale=320:170:force_original_aspect_ratio=decrease:flags=lanczos,pad=320:170:(ow-iw)/2:(oh-ih)/2,setsar=1']
            if a.seconds:command+=['-t',str(a.seconds)]
            command+=['-f','rawvideo','-pix_fmt','rgb565be','pipe:1']
            decoder=subprocess.Popen(command,stdout=subprocess.PIPE)
            while True:
                data=decoder.stdout.read(FRAME_BYTES)
                if not data:break
                if play_start is None:play_start=time.monotonic()
                if a.realtime and frames:
                    player.wait_until(play_start+frames/a.fps)
                t=time.monotonic();player.frame(data,frames+1);dt=time.monotonic()-t
                transfers.append(dt);transfer_total+=dt;transfer_min=min(transfer_min,dt);transfer_max=max(transfer_max,dt);frames+=1
                if frames==1 or frames%100==0:
                    print(f'{frames} frames ACKed, {frames/a.fps:.1f}s source, {time.monotonic()-play_start:.1f}s playback',flush=True)
                if a.simulate and link.screen()!=data:
                    raise RuntimeError('C receiver LCD image differs from decoded video frame')
            if decoder.wait(timeout=5):raise RuntimeError('ffmpeg failed')
            if a.simulate:
                result['last_frame_sha256']=hashlib.sha256(link.screen()).hexdigest()
                result['every_frame_pixel_exact']=True
                result['simulated_lcd_flushes']=link.lib.sim_flushes()
                result['cdc_rx_reassembly']='each request split 63+193 bytes through C assembler'
        result['status']='completed'
    except KeyboardInterrupt:
        result['status']='stopped'
    except Exception as exc:
        result['status']='failed';result['error']=str(exc)
    finally:
        if decoder is not None and decoder.poll() is None:
            decoder.terminate()
            try:decoder.wait(timeout=3)
            except subprocess.TimeoutExpired:decoder.kill();decoder.wait()
        if ready:
            try:result['receiver_stats_after']=player.stats()
            except Exception as exc:result['stats_error']=str(exc)
            try:player.stop();result['display_lease_released']=True
            except Exception as exc:result['stop_error']=str(exc)
            if isinstance(link,Link):
                try:
                    result['config_after_hex']=link.request(3,b'\x01')[6:34].hex()
                    result['configuration_unchanged']=result['config_after_hex']==result.get('config_before_hex')
                except Exception as exc:result['config_read_error']=str(exc)
        if isinstance(link,Link):link.port.close()
        else:link.close()
        result.update(frames_acked=frames,packets_written=player.packets,
                      elapsed_seconds=time.monotonic()-start,source_seconds=frames/a.fps)
        if transfers:
            result.update(transfer_fps=frames/transfer_total,
                frame_transfer_seconds_min=transfer_min,
                frame_transfer_seconds_mean=transfer_total/frames,
                frame_transfer_seconds_max=transfer_max,
                playback_seconds=time.monotonic()-play_start,
                frame_transfer_seconds=list(transfers),
                frame_transfer_history_truncated=len(transfers)<frames)
        a.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='frame_transfer_seconds'},indent=2))
    return 0 if result['status'] in ('completed','stopped') else 1


if __name__=='__main__':raise SystemExit(main())
