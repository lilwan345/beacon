#!/bin/zsh
# Beacon launcher — runs the first Python that has PySide6. Single instance.
if pgrep -f "[b]eacon\.py" >/dev/null 2>&1; then
  echo "Beacon is already running."
  exit 0
fi
HERE="$(cd "$(dirname "$0")" && pwd)"

# Try, in order: an explicit override, the active conda env, whatever's on
# PATH, then the usual anaconda/miniconda install spots.
candidates=(
  "$BEACON_PYTHON"
  "$CONDA_PREFIX/bin/python3"
  python3 python
  "$HOME/anaconda3/bin/python3" "$HOME/miniconda3/bin/python3"
  /opt/anaconda3/bin/python3 /opt/miniconda3/bin/python3
)
for PY in "${candidates[@]}"; do
  [ -n "$PY" ] || continue
  if command -v "$PY" >/dev/null 2>&1 && "$PY" -c 'import PySide6' >/dev/null 2>&1; then
    exec "$PY" "$HERE/beacon.py"
  fi
done

echo "Beacon needs PySide6, but no Python with it was found." >&2
echo "Install it:  pip install -r \"$HERE/requirements.txt\"" >&2
echo "or set BEACON_PYTHON to a Python that already has PySide6." >&2
exit 1
