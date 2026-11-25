#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["pyserial"]
# ///

"""Program the Serial Modem app onto the BG95 interposer (nRF9151), verify it over AT, and make
sure the modem firmware matches the one you provide.

Setup this script assumes:
  * The DK's on-board debugger is routed to its debug-out header and connected to the interposer.
    nrfutil/J-Link therefore talks to the nRF9151 using the DK's serial number.
  * The DK itself runs the bg95_programmer_at_interface app: whatever is typed on its VCOM1
    is sent as an AT command to the interposer's UART0 and the reply is printed back.

Steps:
  1. Check that the target behind the probe really is an nRF9151 (never program the DK itself).
  2. Recover the device (clears AP-Protect and erases flash/UICR).
  3. Program the modem firmware zip.
  4. Program the app hex (the modem update erases the app) and reset the interposer.
  5. Verify: "AT" -> OK, and AT+CGMR reports the expected modem firmware.

Example:
  python3 flash_and_verify.py --port /dev/cu.usbmodemXXXX3 --app app.hex --modem-fw mfw_nrf91x1_2.0.4.zip
"""
import argparse
import json
import re
import subprocess
import sys
import time

import serial

# Modem firmware string the interposer must report in AT+CGMR, e.g. "mfw_nrf91x1_2.0.4" or
# "mfw-pti_nrf91x1_2.3.8". Leave empty to derive it from the --modem-fw file name.
# --expected-modem on the command line overrides this.
EXPECTED_MODEM = "mfw_nrf91x1_2.0.4"
BOOT_WAIT_S = 4.0  # Serial Modem prints "Ready" about 2 s after reset
PROMPT = "~$ "
AT_TIMEOUT_S = 10.0


def run(cmd, timeout=600):
    """Run a command and return CompletedProcess; never raises on non-zero exit."""
    print("  $ " + " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def fail(msg):
    print(f"\nFAILED: {msg}")
    sys.exit(1)


# ---------------------------------------------------------------- nrfutil helpers

def find_dk():
    """Return the serial number of the single J-Link device that is connected."""
    r = run(["nrfutil", "device", "list", "--traits", "jlink", "--json"], 60)
    sns = []
    for line in r.stdout.splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        for dev in ((msg.get("data") or {}).get("data") or {}).get("devices", []):
            sns.append(dev["serialNumber"].lstrip("0"))
    sns = sorted(set(sns))
    if len(sns) != 1:
        fail(f"found {len(sns)} J-Link devices ({', '.join(sns) or 'none'}); "
             "pass the DK's serial number with --sn.")
    print(f"  using the only connected J-Link device: {sns[0]}")
    return sns[0]


def check_target(sn):
    """Refuse to continue unless the probe sees an nRF9151."""
    r = run(["nrfutil", "device", "device-info", "--serial-number", sn, "--family", "nrf91"], 60)
    text = r.stdout + r.stderr
    m = re.search(r"deviceName:\s*(\S+)", text)
    if r.returncode != 0 or not m or m.group(1).lower() != "nrf9151":
        print(text.strip()[-600:])
        fail("the debugger does not see an nRF9151. Check that the DK is in debug-out mode, "
             "the SWD cable is connected and the interposer is powered (3.8 V on the jig).")
    ver = re.search(r"deviceVersion:\s*(\S+)", text)
    print(f"  target: nRF9151 ({ver.group(1) if ver else 'unknown revision'})")


def recover(sn):
    """Unlock and erase the device (also clears AP-Protect) so that programming always works."""
    print("\nRecovering the device (erases the application flash and UICR)")
    r = run(["nrfutil", "device", "recover", "--serial-number", sn, "--family", "nrf91"], 120)
    if r.returncode != 0:
        print((r.stdout + r.stderr).strip()[-800:])
        fail("recover failed.")


def program(sn, firmware, what, core=None):
    print(f"\nProgramming {what}: {firmware}")
    opts = "chip_erase_mode=ERASE_ALL,verify=VERIFY_READ"
    cmd = ["nrfutil", "device", "program", "--firmware", firmware, "--serial-number", sn,
           "--family", "nrf91"]
    if firmware.lower().endswith(".hex"):
        cmd += ["--options", opts]
    if core:
        cmd += ["--core", core]
    r = run(cmd)
    if r.returncode != 0:
        text = (r.stdout + r.stderr).strip()
        print(text[-1200:])
        return False
    return True


def reset_target(sn):
    """Reset the interposer; the freshly programmed app only starts after this."""
    for kind in ("RESET_SYSTEM", "RESET_HARD"):
        r = run(["nrfutil", "device", "reset", "--serial-number", sn, "--family", "nrf91",
                 "--reset-kind", kind], 60)
        if r.returncode == 0:
            return
        print((r.stdout + r.stderr).strip()[-400:])
    fail("could not reset the interposer.")


# ---------------------------------------------------------------- AT over the DK shell

class AtShell:
    """Talks to the bypass app: send one line, read until the final result and the next prompt."""

    def __init__(self, port):
        self.ser = serial.Serial(port, 115200, timeout=0.2)

    def close(self):
        self.ser.close()

    def _read_until_prompt(self, timeout):
        buf = ""
        end = time.time() + timeout
        while time.time() < end:
            buf += self.ser.read(256).decode("utf-8", "replace")
            if buf.endswith(PROMPT) and ("\n" in buf or "OK" in buf or "ERROR" in buf):
                return buf
        return buf

    def sync(self):
        """Clear anything pending and get back to a fresh prompt."""
        self.ser.reset_input_buffer()
        self.ser.write(b"\r")
        self._read_until_prompt(2.0)

    def at(self, cmd, timeout=AT_TIMEOUT_S):
        self.sync()
        self.ser.write(cmd.encode() + b"\r")
        raw = self._read_until_prompt(timeout)
        lines = [ln.strip() for ln in raw.replace("\r", "\n").split("\n")]
        lines = [ln for ln in lines if ln and ln != cmd and ln != PROMPT.strip()
                 and not ln.endswith(PROMPT.strip() + " " + cmd)]
        # The echo of the typed command may be glued to the prompt, e.g. "~$ AT".
        lines = [ln for ln in lines if not ln.endswith(cmd) or ln.startswith(("+", "#", "%"))]
        return lines


def wait_for_modem(shell, timeout=30.0):
    """Poll AT until the Serial Modem app answers OK."""
    end = time.time() + timeout
    while time.time() < end:
        if "OK" in shell.at("AT", 3.0):
            return True
        time.sleep(1.0)
    return False


def read_modem_version(shell):
    lines = shell.at("AT+CGMR")
    for ln in lines:
        if "nrf91" in ln.lower() or ln.lower().startswith("mfw"):
            return ln
    return None


def mfw_id(text):
    """Extract 'mfw[-flavour]_nrf91x1_X.Y.Z' from a version string or file name."""
    m = re.search(r"(mfw[\w-]*?_nrf91x1_\d+\.\d+\.\d+(?:[-\w]*)?)", text, re.I)
    return m.group(1).lower() if m else None


def mfw_numeric(text):
    m = re.search(r"nrf91x1_(\d+\.\d+\.\d+)", text, re.I)
    return m.group(1) if m else None


# ---------------------------------------------------------------- main flow

def verify(shell, label):
    print(f"\nVerifying ({label}) ...")
    if not wait_for_modem(shell):
        fail("no AT response from the interposer. Check the UART jig contact, power, and that the "
             "app started (try `tools/uart_link_test.py`).")
    print("  AT -> OK")
    version = read_modem_version(shell)
    if not version:
        fail("AT+CGMR did not return a modem firmware version.")
    print(f"  modem firmware: {version}")
    return version


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--app", required=True, help="Serial Modem app .hex (merged image) to program")
    ap.add_argument("--modem-fw", required=True,
                    help="modem firmware .zip (mfw_nrf91x1_X.Y.Z.zip); its name defines the expected version")
    ap.add_argument("--expected-modem", help="override the expected AT+CGMR string/version, e.g. "
                    "mfw_nrf91x1_2.0.4 (default: EXPECTED_MODEM in the script, else the --modem-fw file name)")
    ap.add_argument("--sn", help="J-Link serial number of the DK (default: the only connected J-Link device)")
    ap.add_argument("--port", required=True,
                    help="serial port of the DK bypass shell (VCOM1), e.g. /dev/cu.usbmodem0010518475913")
    a = ap.parse_args()

    expected = a.expected_modem or EXPECTED_MODEM or a.modem_fw
    expected_id, expected_ver = mfw_id(expected), mfw_numeric(expected)
    if not expected_ver:
        fail("could not read a version like 'nrf91x1_2.0.4' from the modem firmware name; "
             "pass --expected-modem.")

    print("Step 1: checking the target behind the debugger")
    if not a.sn:
        a.sn = find_dk()
    check_target(a.sn)

    recover(a.sn)

    print("\nStep 2: programming the modem firmware")
    ok = program(a.sn, a.modem_fw, "modem firmware")
    if not ok:
        print("  retrying once with --core modem")
        ok = program(a.sn, a.modem_fw, "modem firmware", core="modem")
    if not ok:
        fail("modem firmware programming failed, see the output above.")

    print("\nStep 3: programming the app")
    if not program(a.sn, a.app, "Serial Modem app"):
        fail("programming the app failed.")
    reset_target(a.sn)

    print("\nStep 4: verifying")
    shell = AtShell(a.port)
    try:
        time.sleep(BOOT_WAIT_S)
        version = verify(shell, "after programming")
    finally:
        shell.close()
    have_id, have_ver = mfw_id(version), mfw_numeric(version)
    if have_ver != expected_ver or (expected_id and have_id != expected_id):
        fail(f"modem firmware is {version}, expected {expected_id or expected_ver}.")

    print("\nPASS: Serial Modem app is running and the modem firmware matches.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
