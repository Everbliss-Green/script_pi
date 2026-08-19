#!/usr/bin/env python3
"""
rftag_cli — talk to an RFTag board over its USB serial console.

Clone the repo onto any Linux box, Raspberry Pi or Mac, plug the board in
over USB, and run:

    ./rftag_cli.py send "hello mesh"

The board is found automatically by its USB VID:PID (1915:520f). It exposes
three CDC-ACM interfaces -- shell, log console and mcumgr -- and this tool
probes them to find the shell rather than assuming a device number, because
the numbering shifts depending on what else is plugged in.

Only dependency is pyserial.
"""

import argparse
import datetime
import re
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit(
        "pyserial is not installed.\n"
        "  Debian/Raspberry Pi OS:  sudo apt install python3-serial\n"
        "  anywhere else:           pip install pyserial"
    )

USB_VID = 0x1915          # Nordic Semiconductor
USB_PID = 0x520F          # RFTag
SHELL_PROMPT = "uart:~$"
BAUD = 115200             # CDC-ACM ignores this, but pyserial wants a number

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

# The Zephyr shell prints no return code, so success/failure has to be read off
# the output itself. Two facts make that reliable:
#   * the prompt only reappears once the command has finished, so it is the
#     synchronisation point;
#   * shell_error() renders bold red and nothing else does, so red is an exact
#     failure signal -- unlike substring matching on "error", which trips over
#     any command whose legitimate output happens to contain the word.
# Verified against the firmware for handler validation errors, wrong parameter
# counts, unknown subcommands and unknown root commands.
ERROR_RE = re.compile(r"\x1b\[1;31m(.*?)\x1b\[m", re.S)


def strip_ansi(text):
    return ANSI_RE.sub("", text)


class RFTagError(Exception):
    pass


class CommandFailed(RFTagError):
    """The device rejected a command (it answered in red)."""

    def __init__(self, command, error, output=""):
        self.command = command
        self.error = error
        self.output = output
        super().__init__(f"{command!r} failed: {error}")


class Result:
    """One command's outcome."""

    def __init__(self, command, ok, output, error=""):
        self.command = command
        self.ok = ok
        self.output = output
        self.error = error

    def __bool__(self):
        return self.ok

    def __str__(self):
        return self.output


class RFTag:
    """A connection to the board's Zephyr shell."""

    def __init__(self, port, timeout=2.0, verbose=False):
        self.port = port
        self.timeout = timeout
        self.verbose = verbose
        self.ser = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def open(self):
        try:
            self.ser = serial.Serial(self.port, BAUD, timeout=0.2,
                                     write_timeout=self.timeout)
        except serial.SerialException as e:
            if "Permission denied" in str(e):
                raise RFTagError(
                    f"Permission denied on {self.port}.\n"
                    "Add yourself to the 'dialout' group, then log out and back in:\n"
                    "    sudo usermod -a -G dialout $USER"
                ) from e
            raise RFTagError(f"Could not open {self.port}: {e}") from e
        time.sleep(0.2)
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()

    def _read_until_prompt(self, deadline):
        """Accumulate raw output until the shell prompt reappears.

        Returns (raw_text, saw_prompt). ANSI is deliberately preserved here --
        the colour codes are what carry the success/failure signal.
        """
        buf = ""
        while time.time() < deadline:
            chunk = self.ser.read(1024)
            if chunk:
                buf += chunk.decode("utf-8", errors="replace")
                if SHELL_PROMPT in strip_ansi(buf):
                    return buf, True
            else:
                time.sleep(0.02)
        return buf, False

    def command(self, cmd, timeout=None, check=True):
        """Send one command, wait for it to finish, and report the outcome.

        Waits for the prompt before returning, so callers can safely issue the
        next command knowing this one completed. Raises CommandFailed when the
        device answered in red, unless check=False.
        """
        timeout = timeout or self.timeout
        if self.verbose:
            print(f"  -> {cmd}", file=sys.stderr)
        self.ser.reset_input_buffer()
        self.ser.write(cmd.encode() + b"\r\n")
        self.ser.flush()

        raw, saw_prompt = self._read_until_prompt(time.time() + timeout)
        if not saw_prompt:
            raise RFTagError(
                f"timed out after {timeout}s waiting for the shell prompt "
                f"following {cmd!r}. Partial output:\n{strip_ansi(raw).strip()}")

        errors = [strip_ansi(m).strip() for m in ERROR_RE.findall(raw)]
        error = "; ".join(e for e in errors if e)

        lines = [ln.rstrip("\r") for ln in strip_ansi(raw).split("\n")]
        if lines and lines[0].strip() == cmd.strip():
            lines = lines[1:]          # drop the echoed command
        error_lines = {e for chunk in errors for e in chunk.splitlines()}
        lines = [ln for ln in lines
                 if ln.strip()
                 and not ln.strip().startswith(SHELL_PROMPT)
                 and ln.strip() not in error_lines]
        output = "\n".join(lines).strip()

        result = Result(cmd, not error, output, error)
        if self.verbose:
            print(f"  <- ok={result.ok} {output!r}"
                  + (f" error={error!r}" if error else ""), file=sys.stderr)
        if error and check:
            raise CommandFailed(cmd, error, output)
        return result

    def sync(self):
        """Make sure the shell is responding."""
        for _ in range(3):
            self.ser.write(b"\r\n")
            self.ser.flush()
            _, saw_prompt = self._read_until_prompt(time.time() + 1.0)
            if saw_prompt:
                return True
        return False

    def ensure_test_mode(self):
        """Most of the command tree is hidden until test mode is on."""
        status = self.command("rftag testmode status").output
        if "ENABLED" in status.upper():
            return False
        self.command("rftag testmode enter")
        again = self.command("rftag testmode status").output
        if "ENABLED" not in again.upper():
            raise RFTagError(f"Could not enable test mode (device said: {again!r})")
        return True


def candidate_ports():
    """Every serial interface belonging to an RFTag board."""
    return [p for p in list_ports.comports()
            if p.vid == USB_VID and p.pid == USB_PID]


def find_shell_port(explicit=None, verbose=False):
    """Locate the interface running the Zephyr shell.

    The board presents shell, log console and mcumgr as three CDC-ACM
    interfaces. Only the shell answers a bare newline with a prompt, so we
    probe rather than trusting the enumeration order.
    """
    if explicit:
        return explicit

    ports = candidate_ports()
    if not ports:
        raise RFTagError(
            "No RFTag board found (looking for USB 1915:520f).\n"
            "  - Is the board plugged in and powered?\n"
            "  - Is the USB cable a data cable? Charge-only cables are the\n"
            "    usual culprit: the board looks alive but never enumerates.\n"
            "  - Check with: lsusb   (Linux)  /  system_profiler SPUSBDataType  (macOS)"
        )

    for p in sorted(ports, key=lambda x: x.device):
        if verbose:
            print(f"probing {p.device} ...", file=sys.stderr)
        try:
            with RFTag(p.device, timeout=1.5, verbose=verbose) as dev:
                if dev.sync():
                    return p.device
        except RFTagError:
            continue

    raise RFTagError(
        "Found an RFTag board but none of its interfaces answered with a shell "
        f"prompt.\nInterfaces tried: {', '.join(p.device for p in ports)}\n"
        "The board may still be booting -- wait a second and retry."
    )


# ---------------------------------------------------------------- subcommands

def cmd_ports(args):
    ports = candidate_ports()
    if not ports:
        print("No RFTag board detected (USB 1915:520f).")
        return 1
    print(f"Found {len(ports)} RFTag interface(s):")
    for p in sorted(ports, key=lambda x: x.device):
        print(f"  {p.device:<20} {p.description}  serial={p.serial_number}")
    try:
        shell = find_shell_port(verbose=args.verbose)
        print(f"\nShell interface: {shell}")
    except RFTagError as e:
        print(f"\n{e}")
        return 1
    return 0


def cmd_info(args):
    with connect(args) as dev:
        fields = [
            ("Firmware",   "rftag app version"),
            ("Build",      "rftag app build-version"),
            ("MAC",        "rftag bt mac"),
            ("Battery",    "rftag pmic soc"),
            ("Group ID",   "rftag settings groupid get"),
            ("Username",   "rftag settings username get"),
            ("Status",     "rftag settings status get"),
        ]
        for label, cmd in fields:
            print(f"{label:<10} {dev.command(cmd).output}")
        print("\nRadio:")
        print(dev.command("rftag proto lora get").output)
    return 0


def cmd_send(args):
    with connect(args) as dev:
        # Quote the text so multi-word messages arrive as a single argument.
        res = dev.command(f'rftag proto send_text "{args.text}"', timeout=6.0)
        print(res.output or "(queued, no output)")
    return 0


def cmd_direct(args):
    with connect(args) as dev:
        res = dev.command(f'rftag proto send_direct {args.mac} "{args.text}"', timeout=6.0)
        print(res.output or "(queued, no output)")
    return 0


def cmd_location(args):
    with connect(args) as dev:
        res = dev.command(
            f"rftag proto send_location {args.battery} {args.lat} {args.lon}",
            timeout=6.0)
        print(res.output or "(queued, no output)")
    return 0


def cmd_join(args):
    with connect(args) as dev:
        res = dev.command(f"rftag proto send_join {args.username}", timeout=6.0)
        print(res.output or "(queued, no output)")
    return 0


def cmd_raw(args):
    with connect(args) as dev:
        # Re-quote any argument containing spaces. The shell that invoked us
        # already stripped the quotes, so joining on " " would turn
        #   raw rftag msg incoming store MAC "hello there" 123
        # into five arguments instead of four and silently mis-parse them.
        parts = [f'"{w}"' if (" " in w or w == "") else w for w in args.words]
        res = dev.command(" ".join(parts), timeout=args.timeout, check=False)
        print(res.output)
        if not res.ok:
            print(f"error: {res.error}", file=sys.stderr)
            return 1
    return 0


def cmd_shell(args):
    with connect(args) as dev:
        print("Interactive RFTag shell. Ctrl-D or 'exit' to quit.")
        print("Commands are passed through verbatim, e.g. 'rftag settings show'.\n")
        while True:
            try:
                line = input("rftag> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line in ("exit", "quit"):
                break
            res = dev.command(line, timeout=args.timeout, check=False)
            if res.output:
                print(res.output)
            if not res.ok:
                print(f"error: {res.error}", file=sys.stderr)
    return 0


def cmd_monitor(args):
    """Tail the log console -- a different interface from the shell."""
    shell = find_shell_port(args.port, verbose=args.verbose)
    others = [p.device for p in candidate_ports() if p.device != shell]
    if not others:
        raise RFTagError("No log console interface found alongside the shell.")
    target = args.log_port or sorted(others)[0]
    print(f"Tailing {target} (Ctrl-C to stop)\n", file=sys.stderr)
    with serial.Serial(target, BAUD, timeout=0.5) as ser:
        try:
            while True:
                chunk = ser.read(4096)
                if chunk:
                    sys.stdout.write(strip_ansi(chunk.decode("utf-8", errors="replace")))
                    sys.stdout.flush()
        except KeyboardInterrupt:
            print("\nstopped", file=sys.stderr)
    return 0


def connect(args):
    """Open the shell interface and get it ready to take commands."""
    port = find_shell_port(args.port, verbose=args.verbose)
    dev = RFTag(port, timeout=args.timeout, verbose=args.verbose)
    dev.open()
    if not dev.sync():
        dev.close()
        raise RFTagError(f"{port} opened but the shell did not respond.")
    if not args.no_test_mode:
        if dev.ensure_test_mode() and args.verbose:
            print("test mode enabled", file=sys.stderr)
    return dev


# ---------------------------------------------------------------- profiles

# Named radio/network presets. Every device in a group must agree on all of
# these or they will not hear each other, so a profile pins every parameter
# rather than only the interesting ones.
#
# "Channel 41", "Taiwan" and the profile names are our own labels -- the
# firmware has no notion of channels or regions, only a raw frequency in Hz.
PROFILES = {
    "mountaineering": {
        "description": "Taiwan / AS923 channel 41, long range, 2 min beacon",
        "groupid":      31690054,      # 0x01E38D46
        "freq_hz":      922500000,     # channel 41, 922.500 MHz
        "bw_khz":       500,
        "sf":           10,
        "cr":           5,             # coding rate 4/5 -- firmware wants the denominator
        "preamble":     16,
        "tx_power_dbm": 22,
        "interval_s":   120,
    },
}


def profile_steps(prof):
    """Each parameter as (set command, read-back command, expected pattern).

    Group ID goes first: changing it makes the firmware clear the location and
    message repositories, so anything set afterwards is unaffected by the wipe.
    These are `settings ...` commands, which persist to flash -- unlike
    `proto lora ...`, which only overrides the live radio until reboot.

    The patterns are matched against the device's own `get` output, so a value
    that is silently clamped, rejected or not persisted is caught even when the
    `set` reported success.
    """
    return [
        dict(name="group id",
             set=f"rftag settings groupid set {prof['groupid']}",
             get="rftag settings groupid get",
             pattern=rf"\({prof['groupid']}\)"),
        dict(name="frequency",
             set=f"rftag settings lora freq {prof['freq_hz']}",
             get="rftag settings lora get",
             pattern=rf"Frequency:\s+{prof['freq_hz']} Hz"),
        dict(name="bandwidth",
             set=f"rftag settings lora bw {prof['bw_khz']}",
             get="rftag settings lora get",
             pattern=rf"Bandwidth:\s+{prof['bw_khz']} kHz"),
        dict(name="spreading factor",
             set=f"rftag settings lora sf {prof['sf']}",
             get="rftag settings lora get",
             pattern=rf"Spreading:\s+SF{prof['sf']}\b"),
        dict(name="coding rate",
             set=f"rftag settings lora cr {prof['cr']}",
             get="rftag settings lora get",
             pattern=rf"Coding Rate:\s+4/{prof['cr']}\b"),
        dict(name="preamble",
             set=f"rftag settings lora preamble {prof['preamble']}",
             get="rftag settings lora get",
             pattern=rf"Preamble:\s+{prof['preamble']} symbols"),
        dict(name="tx power",
             set=f"rftag settings lora power {prof['tx_power_dbm']}",
             get="rftag settings lora get",
             pattern=rf"TX Power:\s+{prof['tx_power_dbm']} dBm"),
        dict(name="location interval",
             set=f"rftag settings timing interval {prof['interval_s']}",
             get="rftag settings timing get",
             pattern=rf"location_update_interval:\s+{prof['interval_s']} sec"),
    ]


def profile_commands(prof):
    return [step["set"] for step in profile_steps(prof)]


def cmd_profiles(args):
    for name, prof in PROFILES.items():
        print(f"{name}  --  {prof['description']}")
        for step in profile_steps(prof):
            print(f"    {step['set']}")
            print(f"        verify: {step['get']}  =~  {step['pattern']}")
    return 0


def cmd_provision(args):
    prof = PROFILES.get(args.profile)
    if prof is None:
        raise RFTagError(
            f"unknown profile {args.profile!r}. "
            f"Available: {', '.join(PROFILES)}")

    steps = profile_steps(prof)
    if args.dry_run:
        print(f"Would apply profile '{args.profile}':")
        for step in steps:
            print(f"  {step['set']}")
            print(f"      then verify with: {step['get']}  =~  {step['pattern']}")
        return 0

    print(f"Applying profile '{args.profile}' -- {prof['description']}")
    print("Note: changing the group ID clears stored members, messages "
          "and location history.\n")

    rejected, mismatched, attempted = [], [], []
    with connect(args) as dev:
        for step in steps:
            attempted.append(step["name"])
            # command() waits for the prompt before returning, so each set is
            # known to have completed before the next is sent.
            res = dev.command(step["set"], timeout=4.0, check=False)
            if not res.ok:
                print(f"  [FAIL] {step['set']}")
                print(f"         error: {res.error}")
                rejected.append(step["name"])
                if not args.keep_going:
                    print("\nStopping: the device rejected that command and "
                          "the profile is only partly applied.")
                    print("Re-run with --keep-going to apply the rest anyway.")
                    break
                continue
            print(f"  [set ] {step['set']}")
            for line in (res.output or "").splitlines():
                print(f"         {line}")

        # Read back what actually landed. Each distinct get runs once.
        print("\nVerifying (reading values back from the device):")
        cache = {}
        for step in steps:
            if step["name"] in rejected:
                continue
            if step["name"] not in attempted:
                print(f"  [skip] {step['name']:<18} not attempted")
                continue
            if step["get"] not in cache:
                cache[step["get"]] = dev.command(step["get"], timeout=4.0,
                                                 check=False).output
            readback = cache[step["get"]]
            if re.search(step["pattern"], readback):
                actual = next((ln.strip() for ln in readback.splitlines()
                               if re.search(step["pattern"], ln)), "")
                print(f"  [ok  ] {step['name']:<18} {actual}")
            else:
                print(f"  [FAIL] {step['name']:<18} expected /{step['pattern']}/")
                for line in readback.splitlines():
                    print(f"         got: {line.strip()}")
                mismatched.append(step["name"])

    if rejected or mismatched:
        print()
        if rejected:
            print(f"Rejected by the device: {', '.join(rejected)}")
        if mismatched:
            print(f"Set but read back wrong: {', '.join(mismatched)}")
        return 1

    print(f"\nProfile '{args.profile}' applied and verified "
          f"({len(steps)}/{len(steps)} values read back correctly).")
    return 0


# ---------------------------------------------------------------- receiving

# From rftag_protocol_serializer.h. The firmware's own read command only names
# 0x03 and 0x04 and calls everything else "unknown", so we decode the rest here.
MSG_TYPES = {
    0x01: "join",
    0x02: "location",
    0x03: "group text",
    0x04: "direct text",
    0x05: "delivery receipt",
    0x06: "targeted resend",
}

# Receipts carry an 11-byte binary payload, not text (BLE_APP_SERVICE_SPEC.md).
# Rendering those bytes as a string produces mojibake, so show hex instead.
BINARY_MSG_TYPES = {0x05}

# The repo prints this via shell_print, not shell_error -- an empty inbox is
# not a failure, so it has to be recognised by text rather than by colour.
NO_MESSAGES = "No messages available"

_FIELD_RE = {
    "mac":       re.compile(r"^\s*MAC:\s*(\S+)", re.M),
    "timestamp": re.compile(r"^\s*Timestamp:\s*(\d+)", re.M),
    "status":    re.compile(r"^\s*Status:\s*0x([0-9A-Fa-f]+)", re.M),
    "msgtype":   re.compile(r"^\s*MsgType:\s*0x([0-9A-Fa-f]+)", re.M),
    "text":      re.compile(r"^\s*Text:\s*'(.*)'\s*$", re.M),
    "length":    re.compile(r"^\s*Length:\s*(\d+)", re.M),
}


def fetch_status_flags(dev):
    """Ask the device for its own status-flag table.

    Reading it off the device rather than hardcoding it means the decode can
    never drift from the firmware.
    """
    out = dev.command("rftag settings status list", check=False).output
    table = {}
    for line in out.splitlines():
        m = re.match(r"\s*(\w+)\s+(\d+)\s+0x([0-9A-Fa-f]{4})\s*$", line)
        if m:
            table[int(m.group(3), 16)] = m.group(1)
    return table


def decode_status(value, table):
    """Names for the bits we know, plus whatever is left over.

    The device's flag table only covers the bits the firmware names. Silently
    dropping the rest would misreport a status like 0x4201 as plain "leader",
    so unknown bits are surfaced rather than hidden.
    """
    names = [name for bit, name in sorted(table.items()) if value & bit]
    known = 0
    for bit in table:
        known |= bit
    leftover = value & ~known
    if leftover:
        names.append(f"unknown bits 0x{leftover:04X}")
    return names


def render_text(text, msgtype):
    """Show printable payloads as text and binary ones as hex."""
    if msgtype in BINARY_MSG_TYPES or any(
            ch < " " or ch == "\x7f" for ch in text):
        raw = text.encode("utf-8", errors="surrogateescape")
        return "hex " + " ".join(f"{b:02X}" for b in raw)
    return f'"{text}"' 


def format_timestamp(raw):
    """Render a device timestamp, flagging an obviously unset RTC."""
    try:
        ts = int(raw)
        dt = datetime.datetime.fromtimestamp(ts)
    except (ValueError, OverflowError, OSError):
        return str(raw), ""
    stamp = dt.strftime("%Y-%m-%d %H:%M:%S")
    # The board ships with its RTC unset; messages then carry a stale default.
    note = "  (device RTC looks unset)" if dt.year < 2025 else ""
    return stamp, note


def parse_message(output):
    """Turn one `msg incoming read` response into a dict, or None if empty."""
    if NO_MESSAGES in output:
        return None
    msg = {}
    for key, pattern in _FIELD_RE.items():
        m = pattern.search(output)
        msg[key] = m.group(1) if m else ""
    return msg if msg.get("mac") else None


def render_message(msg, flags, index=None):
    stamp, note = format_timestamp(msg["timestamp"])
    try:
        status_val = int(msg["status"], 16)
    except ValueError:
        status_val = 0
    names = decode_status(status_val, flags)
    try:
        type_val = int(msg["msgtype"], 16)
    except ValueError:
        type_val = -1
    type_name = MSG_TYPES.get(type_val, f"unknown (0x{msg['msgtype']})")

    head = f"[{index}] " if index is not None else ""
    lines = [
        f"{head}from {msg['mac']}   {type_name}   {stamp}{note}",
        f"     status 0x{msg['status']}" + (f"  [{', '.join(names)}]" if names else ""),
        f"     {render_text(msg['text'], type_val)}  ({msg['length']} bytes)",
    ]
    return "\n".join(lines)


def message_count(dev):
    out = dev.command("rftag msg incoming count", check=False).output
    m = re.search(r"count:\s*(-?\d+)", out)
    return int(m.group(1)) if m else 0


def drain_messages(dev, flags, start_index=1):
    """Read every pending message. Each read consumes one, so print as we go."""
    shown = 0
    while True:
        res = dev.command("rftag msg incoming read", timeout=4.0, check=False)
        if not res.ok:
            print(f"error reading: {res.error}", file=sys.stderr)
            break
        msg = parse_message(res.output)
        if msg is None:
            break
        print(render_message(msg, flags, start_index + shown))
        shown += 1
    return shown


def cmd_receive(args):
    with connect(args) as dev:
        if args.clear:
            res = dev.command("rftag msg incoming clear", check=False)
            print(res.output or "cleared")
            return 0 if res.ok else 1

        flags = fetch_status_flags(dev)

        if args.count:
            print(f"Incoming messages waiting: {message_count(dev)}")
            return 0

        if not args.watch:
            pending = message_count(dev)
            if pending <= 0:
                print("No messages waiting.")
                return 0
            print(f"{pending} message(s) waiting:\n")
            drain_messages(dev, flags)
            return 0

        # Watch mode: poll the count, and drain whenever it goes positive.
        print(f"Watching for messages on {dev.port} -- polling every "
              f"{args.interval}s. Ctrl-C to stop.")
        total = 0
        pending = message_count(dev)
        if pending > 0:
            print(f"\n{pending} already waiting:\n")
            total += drain_messages(dev, flags)
        print("\nWaiting...")
        try:
            while True:
                time.sleep(args.interval)
                if message_count(dev) > 0:
                    stamp = datetime.datetime.now().strftime("%H:%M:%S")
                    print(f"\n--- {stamp} ---")
                    total += drain_messages(dev, flags, total + 1)
        except KeyboardInterrupt:
            print(f"\nStopped. {total} message(s) received.")
        return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="rftag_cli.py",
        description="Talk to an RFTag board over USB serial.",
        epilog="Examples:\n"
               "  ./rftag_cli.py ports\n"
               "  ./rftag_cli.py info\n"
               '  ./rftag_cli.py send "hello mesh"\n'
               '  ./rftag_cli.py direct AABBCCDDEEFF "private note"\n'
               "  ./rftag_cli.py location 87 14.5995 120.9842\n"
               "  ./rftag_cli.py receive --watch\n"
               "  ./rftag_cli.py provision mountaineering\n"
               "  ./rftag_cli.py monitor\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-p", "--port", help="serial port (default: auto-detect)")
    p.add_argument("-t", "--timeout", type=float, default=2.0,
                   help="per-command timeout in seconds (default: 2)")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="show the raw traffic")
    p.add_argument("--no-test-mode", action="store_true",
                   help="skip the automatic 'rftag testmode enter'")

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ports", help="list detected RFTag interfaces").set_defaults(func=cmd_ports)
    sub.add_parser("info", help="show firmware, MAC, battery and radio config").set_defaults(func=cmd_info)

    s = sub.add_parser("send", help="broadcast a text message over LoRa")
    s.add_argument("text")
    s.set_defaults(func=cmd_send)

    s = sub.add_parser("direct", help="send a direct message to one MAC")
    s.add_argument("mac")
    s.add_argument("text")
    s.set_defaults(func=cmd_direct)

    s = sub.add_parser("location", help="send a location update")
    s.add_argument("battery", type=int)
    s.add_argument("lat")
    s.add_argument("lon")
    s.set_defaults(func=cmd_location)

    s = sub.add_parser("join", help="send a join announcement")
    s.add_argument("username")
    s.set_defaults(func=cmd_join)

    s = sub.add_parser("raw", help="run any shell command verbatim")
    s.add_argument("words", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_raw)

    sub.add_parser("shell", help="interactive prompt").set_defaults(func=cmd_shell)

    s = sub.add_parser("provision", help="apply a named LoRa/network profile")
    s.add_argument("profile", nargs="?", default="mountaineering",
                   choices=sorted(PROFILES))
    s.add_argument("--dry-run", action="store_true",
                   help="print the commands without sending them")
    s.add_argument("--keep-going", action="store_true",
                   help="continue after a rejected command instead of stopping")
    s.set_defaults(func=cmd_provision)

    sub.add_parser("profiles", help="list available profiles and their commands").set_defaults(func=cmd_profiles)

    s = sub.add_parser("receive", help="read incoming LoRa messages")
    s.add_argument("-w", "--watch", action="store_true",
                   help="keep polling and print messages as they arrive")
    s.add_argument("-n", "--interval", type=float, default=2.0,
                   help="seconds between polls in watch mode (default: 2)")
    s.add_argument("-c", "--count", action="store_true",
                   help="just print how many are waiting, read nothing")
    s.add_argument("--clear", action="store_true",
                   help="discard all pending messages without reading them")
    s.set_defaults(func=cmd_receive)

    s = sub.add_parser("monitor", help="tail the device log console")
    s.add_argument("--log-port", help="log interface (default: auto)")
    s.set_defaults(func=cmd_monitor)

    return p


def main():
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except RFTagError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except serial.SerialException as e:
        print(f"error: serial failure: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
