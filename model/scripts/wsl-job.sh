#!/usr/bin/env bash
# Runs INSIDE WSL on cuda-box. Launched detached from Windows by
# model/scripts/cuda-box.sh (`start "" /b cmd /c "wsl -e bash /mnt/c/Users/PC/wsl-job.sh <job> ..."`),
# so no command text crosses the cmd → wsl → bash quoting boundary.
#   wsl-job.sh build
#   wsl-job.sh model REPO_ID NAME
#   wsl-job.sh bench NAME [TOKENS]
#   wsl-job.sh generate NAME TASKS_DIR RUN
set -uo pipefail
export PATH="$HOME/micromamba/envs/cu/bin:$HOME/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export CUDA_HOME="$HOME/micromamba/envs/cu"
export LD_LIBRARY_PATH="/usr/lib/wsl/lib:$HOME/micromamba/envs/cu/lib:${LD_LIBRARY_PATH:-}"
cd "$HOME/cadence" || exit 1
job="${1:-}"; shift || true
echo "[wsl-job] $(date -Is) $job $*"
case "$job" in
  build)
    cargo build --release -p cadence-model --features cuda
    ;;
  model)
    repo="${1:?REPO_ID}"; name="${2:-$(basename "$repo")}"
    mkdir -p "$HOME/models"
    command -v huggingface-cli >/dev/null || pip install -q -U huggingface_hub
    HF_XET_HIGH_PERFORMANCE=1 huggingface-cli download "$repo" --local-dir "$HOME/models/$name"
    ;;
  bench)
    name="${1:?NAME}"
    ./target/release/cadence-model bench --backend candle --device cuda --model "$HOME/models/$name" --tokens "${2:-128}"
    ;;
  generate)
    name="${1:?NAME}"; tasks="${2:-model/tasks}"; run="${3:-cuda-$name}"
    ./target/release/cadence-model eval --phase generate --backend candle --device cuda --model "$HOME/models/$name" --tasks "$tasks" --out "model/out/$run"
    ;;
  *)
    echo "unknown job $job"; exit 2 ;;
esac
echo "[wsl-job] $(date -Is) exit=$?"
