#!/usr/bin/env bash
#
# Send a text message out over LoRa.
#
#   ./send_message.sh hello
#   ./send_message.sh "hello from the trail"
#   ./send_message.sh hello from the trail     # unquoted words are joined
#
# Safe to run as many times as you like -- each call sends one message.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$#" -eq 0 ]; then
    cat >&2 <<USAGE
Usage: $(basename "$0") <message>

Examples:
  $(basename "$0") hello
  $(basename "$0") "hello from the trail"

The board is found automatically over USB. Run ./setup.sh first if this is a
fresh machine.
USAGE
    exit 1
fi

# "$*" joins every argument, so quoting the message is optional.
MESSAGE="$*"

echo "Sending: $MESSAGE"
exec python3 "$HERE/rftag_cli.py" send "$MESSAGE"
