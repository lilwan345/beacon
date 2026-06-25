#!/bin/zsh
# Beacon launcher — single instance.
if pgrep -f "[a]naconda3/bin/python3 .*beacon.py" > /dev/null; then
  echo "Beacon is already running."
  exit 0
fi
exec /opt/anaconda3/bin/python3 "$(dirname "$0")/beacon.py"
