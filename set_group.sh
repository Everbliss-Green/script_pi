#!/usr/bin/env bash
#
# Put this board into a group and pin its whole radio configuration.
#
#   ./set_group.sh                  # applies the default 'mountaineering' profile
#   ./set_group.sh mountaineering
#   ./set_group.sh --dry-run        # show what would be sent, send nothing
#
# Every value is read back from the device afterwards. The script only exits 0
# if all of them come back correct.
#
# Heads up: changing the group ID makes the firmware clear stored members,
# messages and location history. That is firmware behaviour, not this script.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${1:-}" in
    -h|--help)
        cat <<USAGE
Usage: $(basename "$0") [profile] [--dry-run] [--keep-going]

Profiles available:
USAGE
        python3 "$HERE/rftag_cli.py" profiles
        exit 0
        ;;
esac

exec python3 "$HERE/rftag_cli.py" provision "$@"
