# script_pi

Scripts for driving an **RFTag** board over USB from **any Linux host** — or a
Mac. A Raspberry Pi is the example throughout, but nothing here is Pi-specific:
the scripts need only `bash`, Python 3.6+ and pyserial, and the board is found
by its USB ID rather than a hardcoded device path.

Plug the board into the Pi with a USB cable, clone this repo, and you can put
the board into a group and send LoRa messages from the shell.

```bash
git clone git@github.com:Everbliss-Green/script_pi.git
cd script_pi
./setup.sh                          # once per machine
./set_group.sh                      # once per board
./send_message.sh hello             # as often as you like
```

## What you need on the Pi

Verified against Raspberry Pi OS Lite (64-bit), Debian 13 "trixie", on a Pi 3B.

| Tool | Version here | Needed for | On a fresh Pi OS Lite | If missing |
|---|---|---|---|---|
| `bash` | 5.2.37 | running `*.sh` | **Always** — Debian `Priority: required` | n/a |
| `python3` | 3.13.5 | `rftag_cli.py` (needs **3.6+**) | Yes, but `Priority: optional` — not guaranteed on every image | `sudo apt install python3` |
| `pyserial` | 3.5 | talking to the serial port | **No — this is the one you must install** | `./setup.sh`, or `sudo apt install python3-serial` |
| `git` | 2.47.3 | cloning this repo | Yes, but `Priority: optional` | `sudo apt install git` |
| `sudo` | — | `setup.sh` installs packages | Yes, and the first user gets passwordless sudo | n/a |
| `dialout` group | — | permission to open `/dev/ttyACM*` | Yes — the image's first user is already a member | `sudo usermod -a -G dialout $USER`, then log out and back in |

`pyserial` is the only real dependency. Everything else ships with a standard
Raspberry Pi OS image; the table lists them because a minimal Debian or a
custom image can omit `python3` and `git`, both of which are `optional`
priority rather than `required`.

`rftag_cli.py` imports only `argparse`, `re`, `sys` and `time` from the
standard library, plus `pyserial`. There is no virtualenv, no build step, no
`requirements.txt` and no compiler involved.

Check everything at once:

```bash
bash --version | head -1
python3 -V
git --version
python3 -c "import serial; print('pyserial', serial.__version__)"
id -nG | tr ' ' '\n' | grep -qx dialout && echo "dialout: ok" || echo "dialout: MISSING"
```

### Hardware

| Item | Notes |
|---|---|
| RFTag board | Enumerates as USB `1915:520f`. Must be powered and running the app firmware. |
| USB cable | **Must be a data cable.** A charge-only cable is the most common failure — the board lights up but never enumerates. |
| Host USB power | On a Pi 3B all four ports share a ~1.2 A budget. If the board enumerates then vanishes, that is the brownout signature. |

> **Step-by-step procedure with real captured terminal output: [SOP.md](SOP.md)**

## The scripts

| Script | What it does |
|---|---|
| `./setup.sh` | One-time machine setup. Installs pyserial and grants serial-port access. |
| `./set_group.sh [profile]` | Puts the board in a group and pins its whole radio config. Verifies every value. |
| `./send_message.sh <message>` | Sends one text message over LoRa. Run it as many times as you want. |
| `./receive_messages.sh` | Reads messages this board has received. `--watch` to keep listening. |
| `./rftag_cli.py` | The engine the two scripts call. Use directly for anything else — `info`, `monitor`, `shell`, `raw`. |

### `./setup.sh`

Run once on a new Pi. It installs `python3-serial` via apt (falling back to pip
elsewhere) and adds you to the `dialout` group so you can open the serial port.

If it adds you to `dialout`, **log out and back in** before continuing —
group membership is only picked up at login.

### `./set_group.sh`

```bash
./set_group.sh                  # apply the default 'mountaineering' profile
./set_group.sh --dry-run        # print the commands, send nothing
./set_group.sh --help           # list profiles and the exact commands
./set_group.sh --keep-going     # don't stop at the first rejected command
```

The `mountaineering` profile — Taiwan / AS923 channel 41, long range, 2 minute beacon:

| Parameter | Value | Firmware command |
|---|---|---|
| Group ID | 31690054 (`0x01E38D46`) | `rftag settings groupid set 31690054` |
| Frequency | 922.500 MHz | `rftag settings lora freq 922500000` |
| Bandwidth | 500 kHz | `rftag settings lora bw 500` |
| Spreading factor | SF10 | `rftag settings lora sf 10` |
| Coding rate | 4/5 | `rftag settings lora cr 5` |
| Preamble | 16 symbols | `rftag settings lora preamble 16` |
| TX power | 22 dBm | `rftag settings lora power 22` |
| Location interval | 120 s | `rftag settings timing interval 120` |

**Every device in a group must agree on all of these** or they will not hear
each other, which is why the profile pins every parameter rather than just the
interesting ones.

Two things that are easy to get wrong:

- **Coding rate is the denominator.** `cr 5` means 4/5. The firmware accepts
  `5|6|7|8`, not `4/5`.
- **"Channel 41" and "Taiwan" are our labels, not the firmware's.** The
  firmware has no notion of channels or regions — only a raw frequency in Hz.

**Changing the group ID clears device data.** The firmware clears stored
members, incoming and outgoing messages, and location history on every group
change. `set_group.sh` sets the group ID first, so the seven settings applied
afterwards survive the wipe.

To add a profile, edit `PROFILES` near the top of `rftag_cli.py`.

### `./send_message.sh`

```bash
./send_message.sh hello
./send_message.sh "hello from the trail"
./send_message.sh hello from the trail      # unquoted words are joined
```

One message per call, so run it as often as you like. It prints the serialized
packet — length, MAC, timestamp, group ID, status flags, and the encrypted
payload as hex and base64 — then `[QUEUED] Message stored for LoRa transmission`.

Peers only receive it if they are on the **same group ID and the same radio
settings**, so run `./set_group.sh` on every board first.

### `./receive_messages.sh`

```bash
./receive_messages.sh              # print everything waiting, then exit
./receive_messages.sh --watch      # keep listening, print as they arrive
./receive_messages.sh --count      # how many are waiting, read nothing
./receive_messages.sh --clear      # discard what's waiting, unread
./receive_messages.sh --watch --interval 5
```

```
2 message(s) waiting:

[1] from D1:D7:E6:13:AD:1A   group text   2026-08-18 14:09:25
     status 0x4201  [leader, unknown bits 0x4200]
     "hello from the trail"  (20 bytes)
[2] from AA:BB:CC:DD:EE:FF   delivery receipt   2026-08-18 14:09:25
     status 0x0000
     hex D4 B5 2C 61 A9 E0 00 01 6A 03 00  (11 bytes)
```

**Reading a message deletes it from the board.** The firmware pops it off the
queue — there is no peek. So everything read is printed here and nowhere else;
if you only want to know how many are waiting, use `--count`, which reads none.

`--watch` drains anything already queued, then polls until you Ctrl-C, printing
each batch with a timestamp. Only one program can hold the serial port at a
time, so nothing else can talk to the board while it runs.

Three things the display does that the raw firmware output does not:

- **Names every message type.** The firmware's own read command only labels
  `0x03` and `0x04` and calls everything else "unknown". Delivery receipts
  (`0x05`), join, location and targeted resends are named here.
- **Shows binary payloads as hex.** A delivery receipt carries 11 binary bytes,
  not text — printing it as a string produces mojibake.
- **Surfaces status bits it cannot name.** The device's flag table is fetched
  from the device itself (`rftag settings status list`) so it can never drift
  from the firmware, but a status like `0x4201` contains bits the table does
  not cover. Those are reported as `unknown bits 0x4200` rather than dropped,
  which would misreport the status as plain `leader`.

Timestamps come from the sender. If the sending board's RTC is unset the time
will be wrong, and the display flags obviously-bogus values with
`(device RTC looks unset)`.

## How the board is found

The board enumerates over USB as `1915:520f` ("Everbliss Green / RFTag") and
presents **three** CDC-ACM interfaces, not one:

| Interface | Purpose |
|---|---|
| first | the Zephyr shell — where commands go |
| second | log output (`<inf> pmic: ...`) |
| third | mcumgr / firmware update, silent |

Which one lands on `/dev/ttyACM0` depends on what else is plugged in, so
hardcoding a device number is unreliable. The scripts probe each interface and
pick the one that answers with a `uart:~$` prompt. Override with `--port` if
you need to.

It is USB CDC-ACM, **not** a UART bridge — no FTDI adapter is involved and the
baud rate is ignored.

## How success is determined

The Zephyr shell prints no return code, so success is read off the output in
two stages.

**1. The device's response.** `shell_error()` renders in bold red and nothing
else does, so red is an exact failure signal — for handler validation errors,
wrong parameter counts, unknown subcommands and unknown root commands alike.
The prompt reappearing marks the command as finished, so each command is
confirmed complete before the next is sent.

**2. Read-back.** After the writes, `set_group.sh` reads every value back with
its `get` command and compares it to what was requested. A value that is
silently clamped, rejected or not persisted is caught even when the `set`
reported success. The script exits non-zero unless all eight read back
correctly:

```
Verifying (reading values back from the device):
  [ok  ] group id           Group ID: 0x01E38D46 (31690054)
  [ok  ] spreading factor   Spreading:    SF10
  ...
Profile 'mountaineering' applied and verified (8/8 values read back correctly).
```

If the device rejects a command, `set_group.sh` stops rather than leaving a
half-configured radio. Steps it never reached are reported `[skip]`, never
counted as passing.

## Other things `rftag_cli.py` can do

```bash
./rftag_cli.py info                     # firmware, MAC, battery, radio config
./rftag_cli.py ports                    # list interfaces, identify the shell
./rftag_cli.py monitor                  # tail the device log console
./rftag_cli.py shell                    # interactive prompt
./rftag_cli.py raw rftag settings show  # run any firmware command verbatim
./rftag_cli.py direct AABBCCDDEEFF "private note"
./rftag_cli.py location 87 14.5995 120.9842
```

## Test mode

Only the four `rftag testmode ...` commands work when test mode is off. The
scripts run `rftag testmode enter` automatically; pass `--no-test-mode` to
suppress that.

## Troubleshooting

**Nothing detected.** Most often a **charge-only USB cable** — the board powers
up and looks alive but never enumerates. Confirm with `lsusb` (Linux) or
`system_profiler SPUSBDataType` (macOS); you want `1915:520f`.

**Permission denied.** You are not in `dialout`, or you have not logged out
since being added. Run `./setup.sh`, then log out and back in.

**Opens but no prompt.** The board may still be booting. Wait a second and
retry. If it persists, check `./rftag_cli.py monitor` for a crash loop.

**Enumerates then vanishes.** On a Raspberry Pi 3B all four USB ports share a
~1.2 A budget. Drop other peripherals or use a powered hub.

## Note on the firmware command name

The firmware subcommand is **`rftag proto`**, not `rftag protocol`. Some older
docs and the firmware's own usage strings say `protocol`, which the shell
rejects with `Unknown rftag command: protocol`.

## Relationship to rftag_firmware

`rftag_cli.py` also lives in the firmware repo at `tools/rftag_cli/`. This repo
is the standalone copy, so a Pi can be set up without cloning the whole
firmware tree. If you change one, change both.
