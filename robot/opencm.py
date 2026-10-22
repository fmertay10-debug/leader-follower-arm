"""OpenCM9.04 (firmware/arm_rec) serial link: reads D lines in the background, sends commands.

    from opencm import OpenCM
    arm = OpenCM()            # /dev/ttyACM* automatically
    arm.stream(True)
    s = arm.latest            # Sample(t_pc, t_ms, state, leader, goal, follower, load, volt, temp, temp_id)
    arm.start_capture(); ...; rows = arm.stop_capture()
    arm.events.get_nowait()   # "B" = button pressed
"""
import glob
import queue
import threading
import time
from typing import NamedTuple

import serial

N = 5
ST_WAIT, ST_SYNC, ST_RUN, ST_HOLD, ST_PC, ST_HOMING, ST_READY = range(7)
STATE_NAMES = ["WAIT", "SYNC", "FOLLOW", "HOLD", "PC", "HOMING", "READY"]


class Sample(NamedTuple):
    t_pc: float       # PC clock (time.time), for matching with the camera
    t_ms: int         # OpenCM millis()
    state: int
    leader: tuple
    goal: tuple
    follower: tuple
    load: tuple
    volt: float       # lowest voltage of the 10 servos [V]
    temp: int         # temperature of the hottest servo [C]
    temp_id: int      # ID of that servo


def find_port():
    ports = sorted(glob.glob("/dev/ttyACM*"))
    if not ports:
        raise RuntimeError("OpenCM not found (no /dev/ttyACM*). Is the USB cable plugged in?")
    return ports[0]


def parse(line):
    f = line.split(",")
    if len(f) != 6 + 4 * N or f[0] != "D":
        return None
    try:
        v = [int(x) for x in f[1:]]
    except ValueError:
        return None
    return Sample(time.time(), v[0], v[1], tuple(v[2:2 + N]), tuple(v[2 + N:2 + 2 * N]),
                  tuple(v[2 + 2 * N:2 + 3 * N]), tuple(v[2 + 3 * N:2 + 4 * N]), v[2 + 4 * N] / 10.0,
                  v[3 + 4 * N], v[4 + 4 * N])


class OpenCM:
    def __init__(self, port=None, on_msg=print):
        self.ser = serial.Serial(port or find_port(), 115200, timeout=0.1)
        self.on_msg = on_msg          # '#' messages go here
        self.events = queue.Queue()   # "E,x" lines -> "x"
        self.latest = None
        self._cap = None
        self._lock = threading.Lock()
        self._run = True
        self._th = threading.Thread(target=self._reader, daemon=True)
        self._th.start()

    def _reader(self):
        buf = b""
        while self._run:
            try:
                buf += self.ser.read(self.ser.in_waiting or 1)
            except serial.SerialException as e:
                if self.on_msg:
                    self.on_msg("# SERIAL CONNECTION LOST: %s" % e)
                return
            *lines, buf = buf.split(b"\n")
            for raw in lines:
                line = raw.decode("ascii", "replace").strip()
                if line.startswith("D,"):
                    s = parse(line)
                    if s is None:
                        continue
                    with self._lock:
                        self.latest = s
                        if self._cap is not None:
                            self._cap.append(s)
                elif line.startswith("E,"):
                    self.events.put(line[2:])
                elif line and self.on_msg:
                    self.on_msg(line)

    def send(self, cmd):
        self.ser.write((cmd + "\n").encode())

    def send_goal(self, goal):
        self.send("G," + ",".join(str(int(round(g))) for g in goal))

    def state(self):
        s = self.latest
        return s.state if s is not None else None

    def stream(self, on=True, timeout=2.0):
        """Brings the stream to the requested state ('r' is a toggle, so it checks first)."""
        def flowing():
            s = self.latest
            return s is not None and time.time() - s.t_pc < 0.3
        if flowing() == on:
            return
        self.send("r")
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(0.05)
            if flowing() == on:
                return
        if on:
            raise RuntimeError("No D lines from the OpenCM (is arm_rec uploaded?)")

    def start_capture(self):
        with self._lock:
            self._cap = []

    def stop_capture(self):
        with self._lock:
            rows, self._cap = self._cap, None
        return rows or []

    def close(self):
        self._run = False
        self._th.join(timeout=1)
        self.ser.close()
