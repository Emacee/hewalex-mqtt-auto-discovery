#!/usr/bin/env python3
"""
Simulated Hewalex PCWU behind an RS485-to-TCP adapter, for testing without hardware.

  python3 tests/pcwu_simulator.py --port 8899               # the PCWU's free RS485 port
  python3 tests/pcwu_simulator.py --port 8899 --eavesdrop   # the cable between PCWU and G-426

On the free port the PCWU (default address 2/2) only answers requests. On the
controller cable the PCWU (1/1) is master: twice a second it reads registers
100 and 252 from the G-426 controller (2/2) and writes its status block to it.
The controller answers any request addressed to it from its own memory, which
holds the copy of the status block (with a message counter in bits 13..15 of
register 196) and a copy of the settings. `pump_answers_on_cable` makes the
PCWU also answer requests addressed to 1/1 on that cable.

The framing here is written independently from rootfs/app/geco.py (CRCs come
from the reference library) so the end-to-end test does not just test the
add-on against itself.

Behaviour modelled after the documentation of the real device:
  * readable range 100..537, anything else is answered with an error frame
    (marker 0xC0, code 0x04)
  * only settings (>= 302) are writable; writes to the status block are
    acknowledged but ignored, like the real PCWU does
  * an out-of-range hot water temperature is replaced by the factory default
    (50.0 °C), like the real PCWU does
  * disabling the heat pump (304 = 0) sets WaitingStatus (202) to 2
"""

from __future__ import annotations

import argparse
import os
import socketserver
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference"))
from hewalex_geco.crc import crc8, crc16  # noqa: E402

MEM_START = 100
MEM_END = 538


def frame(dst_hard, src_hard, dst_soft, src_soft, fnc, start=0, count=0, data=b"", error=None):
    payload = [dst_soft & 0xFF, dst_soft >> 8, src_soft & 0xFF, src_soft >> 8, fnc]
    if error is None:
        payload += [0x80, 0x00, count, start & 0xFF, start >> 8] + list(data)
    else:
        payload += [0xC0, 0x00, error]
    crc = crc16(payload)
    payload += [crc >> 8, crc & 0xFF]
    header = [0x69, dst_hard, src_hard, 0x84, 0, 0, len(payload)]
    return bytes(header + [crc8(header)] + payload)


def parse_frames(buf: bytearray):
    """Yield (hard_to, hard_from, soft_to, soft_from, fnc, count, start, data) and consume buf."""
    while True:
        i = buf.find(0x69)
        if i < 0:
            buf.clear()
            return
        del buf[:i]
        if len(buf) < 8:
            return
        if bytes(buf[3:6]) != b"\x84\x00\x00" or crc8(list(buf[:7])) != buf[7]:
            del buf[:1]
            continue
        total = 8 + buf[6]
        if len(buf) < total:
            return
        hdr, p = bytes(buf[:8]), bytes(buf[8:total])
        if len(p) < 12 or crc16(list(p[:-2])) != (p[-2] << 8 | p[-1]):
            del buf[:1]
            continue
        del buf[:total]
        yield hdr[1], hdr[2], p[0] | p[1] << 8, p[2] | p[3] << 8, p[4], p[7], p[8] | p[9] << 8, p[10:-2]


class Pcwu:
    def __init__(self):
        self.lock = threading.Lock()
        self.mem = bytearray(MEM_END - MEM_START)
        self.writes = []
        self.ignore_writes = set()   # addresses that acknowledge but keep their value
        words = {
            128: 85, 130: 452, 132: 498, 134: 0xFE0C, 136: 0xFE0C, 138: 401, 140: 457, 142: 12, 144: 95,
            146: 612, 194: 2, 196: 0x0805, 198: 250, 202: 0, 206: 0,
            302: 1, 304: 1, 306: 0, 308: 500, 310: 50, 312: 20, 326: 1, 328: 0, 330: 0, 332: 45,
            334: (-70) & 0xFFFF, 336: 130, 338: 8, 344: 0, 364: 0, 366: 450, 368: 550, 370: 1, 396: 0,
            398: 450, 400: 550, 402: 1, 428: 400, 430: 0, 454: 650, 456: 450, 460: 1, 498: 1, 500: 1,
            502: 0, 516: 1, 518: 1, 522: 1, 524: 1,
        }
        for address, value in words.items():
            self.set_word(address, value)
        for i, b in enumerate(bytes.fromhex("1890016f01008002") + bytes(12)):
            self.mem[i] = b                      # registers 100..119 as the controller reports them
        self.mem[252 - MEM_START] = 0x10         # controller status: display on, nothing changed
        for address, mask in ((314, 0x3FFFC0), (318, 0xFFFFFF), (322, 0xFFFFFF), (374, 0)):
            self.mem[address - MEM_START:address - MEM_START + 4] = struct.pack("<I", mask)
        for i, b in enumerate((26, 10, 10, 5, 12, 30, 0, 0)):  # date 2026-10-10, time 12:30:00
            self.mem[120 - MEM_START + i] = b

    def set_word(self, address, value):
        self.mem[address - MEM_START:address - MEM_START + 2] = struct.pack("<H", value & 0xFFFF)

    def word(self, address):
        return struct.unpack_from("<H", self.mem, address - MEM_START)[0]

    def read(self, start, count):
        if start < MEM_START or start + count > MEM_END or count == 0:
            return None
        with self.lock:
            return bytes(self.mem[start - MEM_START:start - MEM_START + count])

    def write(self, start, data):
        if start < MEM_START or start + len(data) > MEM_END:
            return False
        with self.lock:
            self.writes.append((start, bytes(data)))
            if start < 302 or start in self.ignore_writes:
                return True
            self.mem[start - MEM_START:start - MEM_START + len(data)] = data
            if not 100 <= self.word(308) <= 600:
                self.set_word(308, 500)
            self.set_word(202, 0 if self.word(304) else 2)
            return True


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        server = self.server
        sock = self.request
        lock = threading.Lock()
        stop = threading.Event()
        if server.eavesdrop:
            threading.Thread(target=self.stream_bus, args=(sock, lock, stop), daemon=True).start()
        buf = bytearray()
        try:
            while True:
                try:
                    chunk = sock.recv(512)
                except OSError:
                    return
                if not chunk:
                    return
                buf += chunk
                for to_hard, from_hard, to_soft, from_soft, fnc, count, start, data in parse_frames(buf):
                    target = self.target(to_hard, to_soft)
                    if target is None:
                        continue
                    if server.drop_next:
                        server.drop_next -= 1
                        continue
                    time.sleep(0.01)
                    if server.noise:
                        sock.sendall(b"\x69\x00\xff\x69")
                    if fnc == 0x40:
                        data = target.read(start, count)
                        reply = (frame(from_hard, to_hard, from_soft, to_soft, 0x50, start, count, data)
                                 if data is not None else
                                 frame(from_hard, to_hard, from_soft, to_soft, 0x50, error=0x04))
                    elif fnc == 0x60:
                        ok = target.write(start, data)
                        reply = (frame(from_hard, to_hard, from_soft, to_soft, 0x70, start, count)
                                 if ok else
                                 frame(from_hard, to_hard, from_soft, to_soft, 0x70, error=0x04))
                    else:
                        continue
                    with lock:
                        sock.sendall(reply)
        finally:
            stop.set()

    def target(self, hard, soft):
        server = self.server
        if not server.eavesdrop:
            return server.pcwu if (hard, soft) == (server.dev_hard, server.dev_soft) else None
        if (hard, soft) == (2, 2):
            return server.controller
        if (hard, soft) == (1, 1) and server.pump_answers_on_cable:
            return server.pcwu
        return None

    def stream_bus(self, sock, lock, stop):
        """Traffic between PCWU (hard 1) and G-426 controller (hard 2), twice a second."""
        pcwu = self.server.pcwu
        controller = self.server.controller
        counter = 0
        while not stop.is_set():
            counter = (counter + 1) % 8
            status = bytearray(pcwu.read(120, 104))
            flags = struct.unpack_from("<H", status, 76)[0] & 0x1FFF | counter << 13
            struct.pack_into("<H", status, 76, flags)
            controller.mem[120 - MEM_START:224 - MEM_START] = status
            cycle = (
                frame(2, 1, 2, 1, 0x40, 100, 20),
                frame(1, 2, 1, 2, 0x50, 100, 20, controller.read(100, 20)),
                frame(2, 1, 2, 1, 0x60, 120, 104, bytes(status)),
                frame(1, 2, 1, 2, 0x70, 120, 104),
                frame(2, 1, 2, 1, 0x40, 252, 4),
                frame(1, 2, 1, 2, 0x50, 252, 4, controller.read(252, 4)),
            )
            try:
                for f in cycle:
                    with lock:
                        sock.sendall(f[:5])
                        time.sleep(0.005)
                        sock.sendall(f[5:])
                    time.sleep(0.01)
                with lock:
                    sock.sendall(b"\x00\x69\xff")  # line noise
            except OSError:
                return
            time.sleep(0.36)


class SimulatorServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port=0, eavesdrop=False, dev_hard=2, dev_soft=2, pump_answers_on_cable=False):
        super().__init__(("127.0.0.1", port), Handler)
        self.pcwu = Pcwu()
        self.controller = Pcwu()                 # G-426: copy of status and settings
        self.eavesdrop = eavesdrop
        self.pump_answers_on_cable = pump_answers_on_cable
        self.dev_hard, self.dev_soft = dev_hard, dev_soft
        self.drop_next = 0
        self.noise = False

    @property
    def port(self):
        return self.server_address[1]

    def start(self):
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--eavesdrop", action="store_true", help="simulate the controller cable instead of the free port")
    ap.add_argument("--pump-answers-on-cable", action="store_true")
    args = ap.parse_args()
    server = SimulatorServer(args.port, args.eavesdrop, pump_answers_on_cable=args.pump_answers_on_cable)
    print(f"Simulated PCWU listening on 127.0.0.1:{server.port} "
          f"({'controller cable' if args.eavesdrop else 'free port'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
