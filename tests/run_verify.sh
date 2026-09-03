#!/usr/bin/env bash
# Verification gate for CI and agents — doctor + wasm compile on launch-spot.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="$ROOT/evals/projects/launch-spot"

echo "== cadence doctor =="
node "$ROOT/scripts/cadence-doctor.mjs" --json --project "$PROJECT" | tail -1

echo "== cadence verify wasm comps/launch.lua =="
cd "$PROJECT"
node "$ROOT/scripts/cadence-verify.mjs" comps/launch.lua --json --wasm-only --skip-lint
