#!/usr/bin/env bash
#
# Read LoRa messages this board has received.
#
#   ./receive_messages.sh            # show everything waiting, then exit
#   ./receive_messages.sh --watch    # keep listening and print as they arrive
#   ./receive_messages.sh --count    # just say how many are waiting
#   ./receive_messages.sh --clear    # throw away what's waiting, unread
#
# Reading a message REMOVES it from the board -- the firmware pops it off the
# queue. So everything read is printed here and nowhere else; run --count first
# if you only want to peek at how many there are.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${1:-}" in
    -h|--help)
        cat <<USAGE
Usage: $(basename "$0") [--watch] [--count] [--clear] [--interval SECONDS]

  (no options)   read and print every message waiting, then exit
  --watch, -w    keep polling and print messages as they arrive (Ctrl-C stops)
  --count, -c    print how many are waiting without reading any
  --clear        discard everything waiting, without printing it
  --interval N   seconds between polls in watch mode (default 2)

Reading consumes messages: once shown, they are gone from the board.
USAGE
        exit 0
        ;;
esac

exec python3 "$HERE/rftag_cli.py" receive "$@"
