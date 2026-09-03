#!/usr/bin/env bash
# P1 gate: determinism, golden frames, encode validity, guard rails.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
ELLUA=bin/ellua
TMP="${TMPDIR:-/tmp}/ellua-p1-$$"
mkdir -p "$TMP" tests/golden
pass=0; fail=0
ok()   { echo "PASS $1"; pass=$((pass+1)); }
bad()  { echo "FAIL $1"; fail=$((fail+1)); }

# 1. render-twice determinism
$ELLUA hash examples/basics/hello.lua > "$TMP/a.txt" 2>&1
$ELLUA hash examples/basics/hello.lua > "$TMP/b.txt" 2>&1
diff <(grep FRAME "$TMP/a.txt") <(grep FRAME "$TMP/b.txt") >/dev/null \
  && ok "render-twice identical" || bad "render-twice identical"

# 2. out-of-order == in-order
$ELLUA hash examples/basics/hello.lua --shuffle > "$TMP/c.txt" 2>&1
diff <(grep FRAME "$TMP/a.txt" | sort) <(grep FRAME "$TMP/c.txt" | sort) >/dev/null \
  && ok "out-of-order identical" || bad "out-of-order identical"

# 3. golden hashes (same-machine; regenerate: rm tests/golden/hello.md5)
if [[ -f tests/golden/hello.md5 ]]; then
  diff <(grep FRAME "$TMP/a.txt") tests/golden/hello.md5 >/dev/null \
    && ok "golden frames" || bad "golden frames (regenerate if change intended)"
else
  grep FRAME "$TMP/a.txt" > tests/golden/hello.md5
  ok "golden frames (created)"
fi

# 4. encode validity
$ELLUA render examples/basics/hello.lua -o "$TMP/hello.mp4" >/dev/null 2>&1
frames=$(ffprobe -v error -select_streams v -count_frames \
  -show_entries stream=nb_read_frames -of csv=p=0 "$TMP/hello.mp4" 2>/dev/null)
[[ "$frames" == "120" ]] && ok "mp4 frame count exact (120)" || bad "mp4 frame count (got: $frames)"

# 5. guard rails — these comps must fail loudly
for f in overrun banned_clock overlap; do
  if $ELLUA hash "tests/fixtures/$f.lua" >/dev/null 2>&1; then
    bad "guard: $f should have errored"
  else
    ok "guard: $f rejected"
  fi
done

# 6. optional inputs — bound sidecar compiles; missing required input errors
if $ELLUA hash tests/fixtures/inputs_bound.lua --inputs tests/fixtures/inputs_bound.inputs.json >/dev/null 2>&1; then
  ok "inputs bound"
else
  bad "inputs bound should compile"
fi
missing_out=$($ELLUA hash tests/fixtures/inputs_missing.lua 2>&1) || true
if echo "$missing_out" | grep -q 'is not bound'; then
  ok "guard: inputs_missing rejected"
else
  bad "guard: inputs_missing should error 'is not bound'"
fi

rm -rf "$TMP"
echo "----"
echo "P1: $pass passed, $fail failed"
exit $((fail > 0 ? 1 : 0))
