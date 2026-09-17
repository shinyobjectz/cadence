#!/usr/bin/env bash
# Mac lane: prove the harness with the mock backend, then a small real model on Metal.
#   model/scripts/mac-smoke.sh              # mock: every hand task must pass
#   model/scripts/mac-smoke.sh metal DIR    # candle on Metal with a Qwen2.5-Coder dir
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
case "${1:-mock}" in
  mock)
    cargo test -q -p cadence-model
    cargo run -q -p cadence-model -- doctor
    cargo run -q -p cadence-model -- eval --backend mock --tasks model/tasks/hand --out model/out/mock-hand
    CADENCE_MOCK=echo cargo run -q -p cadence-model -- eval --backend mock --tasks model/tasks/hand --out model/out/mock-echo || echo "echo baseline fails as it should"
    ;;
  metal)
    dir="${2:-${CADENCE_MODEL_DIR:-$HOME/.cache/cadence/models/Qwen2.5-Coder-0.5B-Instruct}}"
    cargo build -q --release -p cadence-model --features metal
    ./target/release/cadence-model bench --backend candle --device metal --model "$dir" --tokens 64
    ./target/release/cadence-model eval --backend candle --device metal --model "$dir" --tasks model/tasks/hand --out "model/out/metal-$(basename "$dir")" || true
    ;;
esac
