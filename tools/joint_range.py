#!/usr/bin/env python3
"""Joint range and dead-zone check (firmware/arm_rec must be uploaded).

Usage:  python3 tools/joint_range.py [/dev/ttyACM0]
With the follower torque off (WAIT), move both arms by hand through ALL poses you will use
in the task: rest pose, every corner of the reach area, touching the table. Ctrl+C = summary.

Flags:
  EDGE : value closer than EDGE units to 0 or 1023 -> too close to the dead zone
  JUMP : change larger than JUMP units between two samples -> passing through the dead zone / bad read
The summary is written to data/joint_range.json; it suggests FOL_MIN/FOL_MAX values.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "robot"))
from opencm import N, OpenCM  # noqa: E402

LEADER_IDS = (13, 17, 14, 9, 10)     # same as firmware/arm_rec (display only)
FOLLOWER_IDS = (11, 18, 16, 12, 8)
EDGE = 40
JUMP = 100
MARGIN = 20
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "joint_range.json")


class Track:
    def __init__(self):
        self.lo, self.hi, self.prev, self.jumps, self.cur = 9999, -1, None, 0, None

    def add(self, v):
        if self.prev is not None and abs(v - self.prev) > JUMP:
            self.jumps += 1
        self.prev = self.cur = v
        self.lo, self.hi = min(self.lo, v), max(self.hi, v)

    def flags(self):
        f = []
        if self.lo < EDGE or self.hi > 1023 - EDGE:
            f.append("EDGE")
        if self.jumps:
            f.append("JUMP x%d" % self.jumps)
        return " ".join(f)


def show(lead, fol, hz):
    out = ["\033[H\033[J", "joint range (Ctrl+C = summary)   %.0f Hz\n" % hz,
           "%-6s %-4s %6s %6s %6s   %s" % ("joint", "ID", "now", "min", "max", "warning")]
    for name, ids, tr in (("leader", LEADER_IDS, lead), ("follower", FOLLOWER_IDS, fol)):
        for i in range(N):
            t = tr[i]
            out.append("%-6s %-4d %6s %6s %6s   %s" % ("%s%d" % (name[0].upper(), i + 1), ids[i], t.cur,
                                                     t.lo if t.hi >= 0 else "-", t.hi if t.hi >= 0 else "-",
                                                     t.flags()))
        out.append("")
    print("\n".join(out), flush=True)


def main():
    arm = OpenCM(sys.argv[1] if len(sys.argv) > 1 else None, on_msg=None)
    arm.stream(True)
    lead = [Track() for _ in range(N)]
    fol = [Track() for _ in range(N)]
    last_t, n, t0, hz = None, 0, time.time(), 0.0
    try:
        while True:
            s = arm.latest
            if s is not None and s.t_ms != last_t:
                last_t = s.t_ms
                n += 1
                for i in range(N):
                    lead[i].add(s.leader[i])
                    fol[i].add(s.follower[i])
            if time.time() - t0 > 0.25:
                hz, n, t0 = n / (time.time() - t0), 0, time.time()
                show(lead, fol, hz)
            time.sleep(0.003)
    except KeyboardInterrupt:
        pass
    arm.stream(False)
    arm.close()

    res = {}
    print("\nSUMMARY")
    for name, ids, tr in (("leader", LEADER_IDS, lead), ("follower", FOLLOWER_IDS, fol)):
        res[name] = [{"id": ids[i], "min": tr[i].lo, "max": tr[i].hi, "jumps": tr[i].jumps} for i in range(N)]
        for i in range(N):
            if tr[i].flags():
                print("  ! %s joint %d (ID %d): %d..%d  %s" % (name, i + 1, ids[i], tr[i].lo, tr[i].hi, tr[i].flags()))
    print("\nSuggestion (measured + %d units margin; fix EDGE/JUMP joints mechanically first):" % MARGIN)
    print("const int16_t FOL_MIN[N_JOINT] = {%s};" % ", ".join(str(max(0, t.lo - MARGIN)) for t in fol))
    print("const int16_t FOL_MAX[N_JOINT] = {%s};" % ", ".join(str(min(1023, t.hi + MARGIN)) for t in fol))
    json.dump(res, open(OUT, "w"), indent=1)
    print("written:", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
