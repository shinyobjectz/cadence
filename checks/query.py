#!/usr/bin/env python3
"""Semantic query against a probe sidecar.

Usage:
    python query.py <sidecar.npz> "text query" [--topk 5] [--nms 0.5]

Encodes the query with the SAME text encoder the probe used (recorded in the
sidecar's meta), scores it against the per-frame embeddings, and prints the
top-k timestamped cosine hits (greedy suppression of hits closer than --nms
seconds so the results span distinct moments).
"""

import os

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import argparse
import json
import sys

import numpy as np

from probe import build_backend_from_meta, pick_device


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sidecar")
    ap.add_argument("query")
    ap.add_argument("--topk", type=int, default=5)
    ap.add_argument("--nms", type=float, default=0.5,
                    help="min seconds between reported hits (default 0.5)")
    args = ap.parse_args()

    data = np.load(args.sidecar, allow_pickle=False)
    meta = json.loads(str(data["meta"]))
    frame_embeds = data["frame_embeds"]
    frame_times = data["frame_times"]

    device = pick_device()
    backend = build_backend_from_meta(meta, device)
    print(f"[query] text encoder: {backend.model_name} "
          f"({backend.backend}, pretrained={backend.pretrained}) "
          f"on {device}", file=sys.stderr)

    text = backend.encode_texts([args.query])[0]
    cos = frame_embeds @ text

    order = np.argsort(-cos)
    hits = []
    for i in order:
        t = float(frame_times[i])
        if any(abs(t - h[0]) < args.nms for h in hits):
            continue
        hits.append((t, float(cos[i])))
        if len(hits) >= args.topk:
            break

    print(f"query: {args.query!r}  "
          f"({meta['video']}, {len(frame_times)} frames)")
    for rank, (t, c) in enumerate(hits, 1):
        print(f"  {rank}. t={t:7.2f}s  cosine={c:+.4f}")


if __name__ == "__main__":
    main()
