import glob
import os
import sys
import time

import serial  # provided by the ESP-IDF python env

PORT = os.getenv("VR_SERIAL_PORT", "")
if not PORT:
    candidates = sorted(
        glob.glob("/dev/cu.usbmodem*")
        + glob.glob("/dev/cu.wchusbserial*")
        + glob.glob("/dev/cu.SLAB_USBtoUART*")
    )
    PORT = candidates[0] if candidates else ""
if not PORT:
    print("OPEN_FAIL: no serial port found; set VR_SERIAL_PORT")
    sys.exit(0)
try:
    s = serial.Serial(PORT, 115200, timeout=1)
except Exception as exc:  # noqa: BLE001
    print("OPEN_FAIL:", exc)
    sys.exit(0)

# Toggle DTR/RTS to trigger a reset so we capture the boot banner.
try:
    s.setDTR(False)
    s.setRTS(True)
    time.sleep(0.1)
    s.setRTS(False)
    time.sleep(0.1)
except Exception:  # noqa: BLE001
    pass

deadline = time.time() + 12
buf = b""
while time.time() < deadline:
    chunk = s.read(1024)
    if chunk:
        buf += chunk
s.close()

text = buf.decode("utf-8", "replace")
print("LEN", len(buf))
for line in text.splitlines():
    line = line.rstrip("\r")
    if line.strip():
        print(line[:180])
