#!/usr/bin/env bash
# Run the editor model on cuda-box (RTX 4070) from the Mac.
#
#   model/scripts/cuda-box.sh sync            # ship the repo (git archive) to ~/cadence in WSL
#   model/scripts/cuda-box.sh build           # cargo build --release -p cadence-model --features cuda (WSL)
#   model/scripts/cuda-box.sh model REPO_ID   # huggingface-cli download into ~/models/<name> on the box
#   model/scripts/cuda-box.sh bench NAME      # tokens/s on the GPU
#   model/scripts/cuda-box.sh generate NAME [TASKS_DIR] [RUN]
#                                             # phase 1 on the GPU: replies for every task → RUN dir
#   model/scripts/cuda-box.sh fetch RUN       # pull RUN/replies back to model/out/RUN on the Mac
#   model/scripts/cuda-box.sh validate RUN [TASKS_DIR]
#                                             # phase 2 on the Mac (LÖVE + ffmpeg): report.json / report.md
#
# The box has no LÖVE, so generation runs there and validation runs here. The
# split is the same one `cadence-model eval --phase generate|validate` exposes.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
WSL='wsl -e bash -lc'
# micromamba env `cu` carries nvcc for candle's CUDA kernels; libcuda comes from /usr/lib/wsl/lib
# absolute PATH: the quoting through cmd.exe → wsl → bash loses $PATH expansions
PREFIX='export PATH=$HOME/micromamba/envs/cu/bin:$HOME/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; export CUDA_HOME=$HOME/micromamba/envs/cu; export LD_LIBRARY_PATH=/usr/lib/wsl/lib:$HOME/micromamba/envs/cu/lib; cd ~/cadence'
run() { cuda-box run "$WSL \"$PREFIX; $*\""; }
# detached jobs: a Windows scheduled task (the only launch that survives the
# ssh session here — `start /b` from an ssh command dies with it). The task
# runs a tiny .py that execs the WSL job script with its arguments baked in,
# so no command text crosses the cmd → wsl → bash quoting boundary.
# Logs: C:\Users\PC\runs\cm-<job>.log (`cuda-box logs cm-<job>` / `log`).
JOB_LOCAL="$ROOT/model/scripts/wsl-job.sh"
spawn() { # spawn <job> [args...]
  local job="$1"; local py="/tmp/cm-$job.py"
  cuda-box put "$JOB_LOCAL" 'C:\Users\PC\wsl-job.sh' >/dev/null
  python3 - "$py" "$@" <<'PY'
import sys, json
py, args = sys.argv[1], sys.argv[2:]
open(py, "w").write(
    "import subprocess, sys\n"
    f"args = {json.dumps(args)}\n"
    "sys.exit(subprocess.call(['wsl', '-e', 'bash', '/mnt/c/Users/PC/wsl-job.sh', *args]))\n")
PY
  cuda-box put "$py" "C:\Users\PC\runs\cm-$job.py" >/dev/null
  cuda-box run "schtasks /Create /TN cm-$job /TR \"cmd /c python C:\\Users\\PC\\runs\\cm-$job.py > C:\\Users\\PC\\runs\\cm-$job.log 2>&1\" /SC ONCE /ST 00:00 /F >NUL & schtasks /Run /TN cm-$job" | grep -v WARNING
}
cmd="${1:-}"; shift || true
case "$cmd" in
  sync)
    # tracked + untracked (not ignored) files, so uncommitted work ships too
    git ls-files -co --exclude-standard -z | tar czf /tmp/cadence.tar.gz --null -T -
    cuda-box put /tmp/cadence.tar.gz 'C:\Users\PC\cadence.tar.gz'
    cuda-box run "$WSL \"mkdir -p ~/cadence && tar xzf /mnt/c/Users/PC/cadence.tar.gz -C ~/cadence && echo synced\""
    ;;
  build)
    # detached: the CUDA kernels take a while; poll with `log`
    spawn build; echo launched
    ;;
  log)
    cuda-box logs "cm-${1:-build}" 2>&1 | tail -n "${2:-8}"
    ;;
  jobs)
    run "ps aux | grep -E 'cargo|huggingface|cadence-model' | grep -v grep | cut -c1-140"
    ;;
  model)
    repo="${1:?REPO_ID}"; name="${2:-$(basename "$repo")}"
    # detached; progress in ~/models/<name>.log
    spawn model "$repo" "$name"; echo downloading
    ;;
  models)
    run "du -sh ~/models/*/ 2>/dev/null"; cuda-box logs cm-model 2>&1 | tail -n 3
    ;;
  bench)
    name="${1:?model name under ~/models}"
    spawn bench "$name" "${2:-128}"; echo "launched; model/scripts/cuda-box.sh log bench"
    ;;
  generate)
    name="${1:?model name}"; tasks="${2:-model/tasks}"; runname="${3:-cuda-$name}"
    spawn generate "$name" "$tasks" "$runname"; echo "launched; model/scripts/cuda-box.sh log generate"
    ;;
  fetch)
    runname="${1:?RUN}"
    run "cd ~/cadence/model/out && tar czf /mnt/c/Users/PC/$runname.tar.gz $runname"
    mkdir -p model/out
    cuda-box get "C:\\Users\\PC\\$runname.tar.gz" "/tmp/$runname.tar.gz"
    tar xzf "/tmp/$runname.tar.gz" -C model/out
    echo "replies in model/out/$runname/replies"
    ;;
  validate)
    runname="${1:?RUN}"; tasks="${2:-model/tasks}"
    cargo run -q -p cadence-model -- eval --phase validate --backend candle --tasks "$tasks" --out "model/out/$runname"
    ;;
  *)
    sed -n 2,20p "$0"; exit 1 ;;
esac
