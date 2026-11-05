"""A stand-in for FFmpeg that behaves as a scenario says, so the reader's reconnect, stall and slicing logic can be tested
without a camera. Usage: python fake_ffmpeg.py <scenario.json>

scenario: {"state": "<file counting attempts>", "w": 64, "h": 36, "attempts": [step, step, ...]}   (the last step repeats)
step:     {"fail": "message"}                      write the message to stderr and exit 1
          {"frames": N, "gap": 0.01, "then": "exit" | "hang" | "continue"}   send N pictures, then end / go silent / keep going"""
import json
import os
import sys
import time

sc = json.load(open(sys.argv[1]))
n = int(open(sc["state"]).read() or 0) if os.path.exists(sc["state"]) else 0
open(sc["state"], "w").write(str(n + 1))
step = sc["attempts"][min(n, len(sc["attempts"]) - 1)]
if "fail" in step:
    sys.stderr.write(step["fail"] + "\n")
    sys.stderr.flush()
    sys.exit(1)
w, h = sc["w"], sc["h"]
i = 0
while True:
    if "frames" in step and i >= step["frames"] and step.get("then") != "continue":
        if step.get("then") == "hang":
            time.sleep(3600)
        sys.exit(0)
    sys.stdout.buffer.write(bytes([i % 256]) * (w * h * 3))
    sys.stdout.buffer.flush()
    i += 1
    time.sleep(step.get("gap", 0.01))
