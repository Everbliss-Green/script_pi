#!/usr/bin/env bash
# One-time setup for a fresh Raspberry Pi / Debian box.
# Installs pyserial and grants serial port access.
set -euo pipefail

echo "==> Installing pyserial"
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y python3-serial
else
    python3 -m pip install --user pyserial
fi

echo "==> Checking serial port access"
if id -nG "$USER" | tr ' ' '\n' | grep -qx dialout; then
    echo "    '$USER' is already in the dialout group."
else
    echo "    Adding '$USER' to the dialout group."
    sudo usermod -a -G dialout "$USER"
    echo
    echo "    !! Log out and back in for this to take effect."
fi

echo
echo "==> Done. Plug the board in and try:"
echo "    ./rftag_cli.py ports"
