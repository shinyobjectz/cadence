#!/usr/bin/env bash
# Creates the probe venv OUTSIDE the repo and installs deps.
#   bash setup.sh
# Venv location: ~/.cache/ellua/probe-venv (override with ELLUA_PROBE_VENV)
set -euo pipefail

VENV="${ELLUA_PROBE_VENV:-$HOME/.cache/ellua/probe-venv}"
HERE="$(cd "$(dirname "$0")" && pwd)"

PY="$(command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3)"
echo "Using python: $PY ($("$PY" --version 2>&1))"

command -v ffmpeg >/dev/null || { echo "WARNING: ffmpeg not found on PATH — probe.py needs it (brew install ffmpeg)"; }

mkdir -p "$(dirname "$VENV")"
if [ ! -d "$VENV" ]; then
  "$PY" -m venv "$VENV"
fi

"$VENV/bin/pip" install --upgrade pip >/dev/null
"$VENV/bin/pip" install -r "$HERE/requirements.txt"

echo
echo "OK. Run probes with:"
echo "  $VENV/bin/python $HERE/probe.py <video.mp4> <manifest.json> [--out findings.json] [--sidecar sidecar.npz] [--stride 4]"
echo "  $VENV/bin/python $HERE/query.py <sidecar.npz> \"text query\""
