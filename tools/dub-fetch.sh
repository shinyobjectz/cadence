#!/usr/bin/env bash
# dub-fetch.sh <dubbing_id|latest> <lang> <out.mp3> — poll until dubbed, download audio
set -euo pipefail
: "${ELEVENLABS_API_KEY:?export ELEVENLABS_API_KEY}"
ID="${1:-latest}"
LANG_CODE="${2:-es}"
OUT="${3:-dubbed.mp3}"
if [ "$ID" = "latest" ]; then
  ID=$(curl -fsS "https://api.elevenlabs.io/v1/dubbing" -H "xi-api-key: $ELEVENLABS_API_KEY" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); ds=d.get('dubs') or d.get('dubbing') or d; print(sorted(ds, key=lambda x: x.get('creation_time') or x.get('created_at') or 0)[-1]['dubbing_id'])")
  echo "latest dub: $ID"
fi
for i in $(seq 1 60); do
  ST=$(curl -fsS "https://api.elevenlabs.io/v1/dubbing/$ID" -H "xi-api-key: $ELEVENLABS_API_KEY" \
    | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','?'))")
  echo "[$i] status: $ST"
  [ "$ST" = "dubbed" ] && break
  [ "$ST" = "failed" ] && { echo "dub failed"; exit 1; }
  sleep 10
done
curl -fsS "https://api.elevenlabs.io/v1/dubbing/$ID/audio/$LANG_CODE" \
  -H "xi-api-key: $ELEVENLABS_API_KEY" -o "$OUT"
echo "saved: $OUT ($(wc -c < "$OUT") bytes)"
