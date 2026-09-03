#!/usr/bin/env python3
"""Tier-3 perceptual probe for the ellua video framework.

Usage:
    python probe.py <video.mp4> <manifest.json> [--out findings.json]
                    [--sidecar sidecar.npz] [--stride 4]

Pipeline:
  1. ffmpeg: frames at `stride` fps -> temp dir; audio -> 48k mono wav.
  2. Frame embeddings: MobileCLIP2-S2 (open_clip). If the installed open_clip
     has no MobileCLIP2, falls back to SigLIP2 (google/siglip2-base-patch16-256,
     transformers) and records the substitution in output meta.
  3. Temporal window embeddings: X-CLIP (microsoft/xclip-base-patch32),
     8 frames per ~2s window.
  4. Audio window embeddings: CLAP (laion/larger_clap_general), 10s windows,
     5s hop.
  5. Derived findings: cut_detect, freeze_detect, black_frame, cut_unplanned,
     vo_sync; manifest assertion evaluation (visible/mood/motion/audible).
  6. Sidecar .npz with all embeddings + times for later semantic query
     (see query.py).

Manifest format:
  {"duration": s,
   "assertions": [{"kind": "visible"|"mood"|"audible"|"motion",
                   "text": "...", "from": s, "to": s, "min": 0.0-1.0?}],
   "scene_boundaries": [s, ...],                     # optional
   "audio_windows": [{"kind": "tts", "at": s, "duration": s}]}  # optional

Everything is runnable offline after the first model download (HF cache).
"""

import os

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import argparse
import json
import subprocess
import sys
import tempfile
import time

import numpy as np

NEGATIVE_PROMPTS = ["a blank screen", "random noise", "unrelated content"]
# CLAP's text space treats visual phrases like "a blank screen" / "unrelated
# content" as universal attractors for any audio (empirically cos +0.2..0.36
# vs any real positive ~0.1), so audible assertions use the same softmax
# scheme with audio-domain generic negatives + CLAP's zero-shot prompt
# template ensemble (both recorded in output meta).
CLAP_NEGATIVE_PROMPTS = ["silence", "random noise", "unrelated sounds"]
CLAP_TEMPLATES = ["{}", "This is a sound of {}.", "the sound of {}"]

FRAME_WINDOW_S = 2.0        # X-CLIP window length / hop
XCLIP_NUM_FRAMES = 8
AUDIO_WINDOW_S = 10.0
AUDIO_HOP_S = 5.0
AUDIO_MIN_S = 2.0           # skip trailing audio windows shorter than this
FREEZE_DELTA = 0.005        # cosine-distance floor for "frozen"
FREEZE_MIN_S = 2.0
BLACK_STD = 8.0             # pixel std threshold (0-255 scale)
CUT_SIGMA = 3.0             # spike threshold: mean + 3*sigma
CUT_PLANNED_TOL_S = 0.4     # detected cut must be within this of a boundary
DEFAULT_MIN_SCORE = 0.5
MAX_LOGIT_SCALE = 100.0     # cap learned temperature for the softmax scheme

XCLIP_MODEL_ID = "microsoft/xclip-base-patch32"
CLAP_MODEL_ID = "laion/larger_clap_general"
SIGLIP2_MODEL_ID = "google/siglip2-base-patch16-256"
MOBILECLIP2_NAME = "MobileCLIP2-S2"


def log(msg):
    print(f"[probe] {msg}", file=sys.stderr, flush=True)


def pick_device():
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


# --------------------------------------------------------------------------
# ffmpeg extraction
# --------------------------------------------------------------------------

def extract_frames(video, outdir, fps):
    pattern = os.path.join(outdir, "f_%06d.jpg")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", video, "-vf", f"fps={fps}",
         "-q:v", "2", "-start_number", "0", pattern],
        check=True,
    )
    files = sorted(f for f in os.listdir(outdir) if f.startswith("f_"))
    paths = [os.path.join(outdir, f) for f in files]
    times = np.arange(len(paths), dtype=np.float64) / float(fps)
    return paths, times


def extract_audio(video, outdir, sr=48000):
    wav = os.path.join(outdir, "audio.wav")
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", video, "-vn", "-ac", "1",
         "-ar", str(sr), wav],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not os.path.exists(wav):
        return None  # video may have no audio stream
    return wav


# --------------------------------------------------------------------------
# Image-text backends (frame embeddings + text for visible/mood + query.py)
# --------------------------------------------------------------------------

class OpenClipImageText:
    """MobileCLIP2-S2 via open_clip."""

    backend = "open_clip"

    def __init__(self, device, model_name=MOBILECLIP2_NAME, pretrained=None):
        import open_clip
        import torch

        self.torch = torch
        self.device = device
        if pretrained is None:
            tags = [t for (n, t) in open_clip.list_pretrained()
                    if n == model_name]
            if not tags:
                raise KeyError(f"no pretrained tags for {model_name}")
            pretrained = "dfndr2b" if "dfndr2b" in tags else tags[0]
        self.model_name = model_name
        self.pretrained = pretrained
        model, _, preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=pretrained)
        self.model = model.to(device).eval()
        self.preprocess = preprocess
        self.tokenizer = open_clip.get_tokenizer(model_name)
        with torch.no_grad():
            self.logit_scale = float(
                min(self.model.logit_scale.exp().item(), MAX_LOGIT_SCALE))

    def encode_images(self, pil_images, batch_size=32):
        torch = self.torch
        chunks = []
        with torch.no_grad():
            for i in range(0, len(pil_images), batch_size):
                batch = torch.stack(
                    [self.preprocess(im) for im in
                     pil_images[i:i + batch_size]]).to(self.device)
                feats = self.model.encode_image(batch)
                feats = feats / feats.norm(dim=-1, keepdim=True)
                chunks.append(feats.float().cpu().numpy())
        return np.concatenate(chunks, axis=0)

    def encode_texts(self, texts):
        torch = self.torch
        with torch.no_grad():
            toks = self.tokenizer(texts).to(self.device)
            feats = self.model.encode_text(toks)
            feats = feats / feats.norm(dim=-1, keepdim=True)
            return feats.float().cpu().numpy()


class Siglip2ImageText:
    """Fallback: SigLIP2 via transformers."""

    backend = "transformers_siglip2"

    def __init__(self, device, model_id=SIGLIP2_MODEL_ID):
        import torch
        from transformers import AutoModel, AutoProcessor

        self.torch = torch
        self.device = device
        self.model_name = model_id
        self.pretrained = model_id
        self.processor = AutoProcessor.from_pretrained(model_id)
        self.model = AutoModel.from_pretrained(model_id).to(device).eval()
        with torch.no_grad():
            self.logit_scale = float(
                min(self.model.logit_scale.exp().item(), MAX_LOGIT_SCALE))

    def encode_images(self, pil_images, batch_size=32):
        torch = self.torch
        chunks = []
        with torch.no_grad():
            for i in range(0, len(pil_images), batch_size):
                inputs = self.processor(
                    images=pil_images[i:i + batch_size],
                    return_tensors="pt").to(self.device)
                feats = self.model.get_image_features(**inputs)
                feats = feats / feats.norm(dim=-1, keepdim=True)
                chunks.append(feats.float().cpu().numpy())
        return np.concatenate(chunks, axis=0)

    def encode_texts(self, texts):
        torch = self.torch
        with torch.no_grad():
            inputs = self.processor(
                text=texts, padding="max_length", max_length=64,
                truncation=True, return_tensors="pt").to(self.device)
            feats = self.model.get_text_features(**inputs)
            feats = feats / feats.norm(dim=-1, keepdim=True)
            return feats.float().cpu().numpy()


def build_image_text_backend(device, prefer=None, substitutions=None):
    """prefer: None (auto), 'open_clip', or 'transformers_siglip2'."""
    if prefer in (None, "open_clip"):
        try:
            return OpenClipImageText(device)
        except Exception as e:  # model config missing / no pretrained tag
            if prefer == "open_clip":
                raise
            if substitutions is not None:
                substitutions.append(
                    f"{MOBILECLIP2_NAME} via open_clip unavailable "
                    f"({type(e).__name__}: {e}); using {SIGLIP2_MODEL_ID} "
                    f"via transformers")
    return Siglip2ImageText(device)


def build_backend_from_meta(meta, device):
    """Rebuild the probe's text encoder for query.py from sidecar meta."""
    info = meta["image_text_model"]
    if info["backend"] == "open_clip":
        return OpenClipImageText(device, model_name=info["model"],
                                 pretrained=info["pretrained"])
    return Siglip2ImageText(device, model_id=info["model"])


# --------------------------------------------------------------------------
# X-CLIP (temporal windows)
# --------------------------------------------------------------------------

class XClipVideo:
    def __init__(self, device, model_id=XCLIP_MODEL_ID):
        import torch
        from transformers import XCLIPModel, XCLIPProcessor

        self.torch = torch
        self.device = device
        self.model_name = model_id
        self.processor = XCLIPProcessor.from_pretrained(model_id)
        self.model = XCLIPModel.from_pretrained(model_id).to(device).eval()
        with torch.no_grad():
            self.logit_scale = float(
                min(self.model.logit_scale.exp().item(), MAX_LOGIT_SCALE))

    def encode_windows(self, windows_of_frames, batch_size=8):
        """windows_of_frames: list of lists of exactly 8 PIL images."""
        torch = self.torch
        chunks = []
        with torch.no_grad():
            for i in range(0, len(windows_of_frames), batch_size):
                batch = windows_of_frames[i:i + batch_size]
                inputs = self.processor(
                    videos=[list(w) for w in batch],
                    return_tensors="pt").to(self.device)
                feats = self.model.get_video_features(
                    pixel_values=inputs["pixel_values"])
                feats = feats / feats.norm(dim=-1, keepdim=True)
                chunks.append(feats.float().cpu().numpy())
        return np.concatenate(chunks, axis=0)

    def encode_texts(self, texts):
        torch = self.torch
        with torch.no_grad():
            inputs = self.processor(
                text=texts, padding=True, return_tensors="pt").to(self.device)
            feats = self.model.get_text_features(
                input_ids=inputs["input_ids"],
                attention_mask=inputs["attention_mask"])
            feats = feats / feats.norm(dim=-1, keepdim=True)
            return feats.float().cpu().numpy()


# --------------------------------------------------------------------------
# CLAP (audio windows)
# --------------------------------------------------------------------------

class ClapAudio:
    def __init__(self, device, model_id=CLAP_MODEL_ID):
        import torch
        from transformers import ClapModel, ClapProcessor

        self.torch = torch
        self.device = device
        self.model_name = model_id
        self.processor = ClapProcessor.from_pretrained(model_id)
        self.model = ClapModel.from_pretrained(model_id).to(device).eval()
        self.sr = self.processor.feature_extractor.sampling_rate
        with torch.no_grad():
            self.logit_scale = float(
                min(self.model.logit_scale_a.exp().item(), MAX_LOGIT_SCALE))

    def _audio_feats(self, clips, device):
        torch = self.torch
        inputs = self.processor(
            audios=clips, sampling_rate=self.sr,
            return_tensors="pt").to(device)
        model = self.model.to(device)
        feats = model.get_audio_features(**inputs)
        return (feats / feats.norm(dim=-1, keepdim=True)).float().cpu().numpy()

    def encode_audio(self, clips, batch_size=4):
        """clips: list of 1-D float32 numpy arrays at self.sr."""
        torch = self.torch
        chunks = []
        with torch.no_grad():
            for i in range(0, len(clips), batch_size):
                feats = self._audio_feats(clips[i:i + batch_size], self.device)
                if np.isnan(feats).any() and self.device != "cpu":
                    # MPS numerical fallback insurance
                    feats = self._audio_feats(clips[i:i + batch_size], "cpu")
                    self.model = self.model.to(self.device)
                chunks.append(feats)
        return np.concatenate(chunks, axis=0)

    def encode_texts(self, texts):
        torch = self.torch
        with torch.no_grad():
            inputs = self.processor(
                text=texts, padding=True, return_tensors="pt").to(self.device)
            feats = self.model.get_text_features(**inputs)
            feats = feats / feats.norm(dim=-1, keepdim=True)
            return feats.float().cpu().numpy()

    def encode_texts_ensembled(self, texts):
        """Mean text embedding over CLAP_TEMPLATES, renormalized."""
        out = []
        for t in texts:
            e = self.encode_texts([tpl.format(t) for tpl in CLAP_TEMPLATES])
            m = e.mean(axis=0)
            out.append(m / np.linalg.norm(m))
        return np.stack(out)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def softmax_vs_negatives(pos_cos, neg_cos, scale):
    """P(positive | positive + negative prompts) at temperature `scale`."""
    logits = np.asarray([pos_cos] + list(neg_cos), dtype=np.float64) * scale
    logits -= logits.max()
    e = np.exp(logits)
    return float(e[0] / e.sum())


def eval_embedding_assertion(text, embeds, times, encode_fn, scale,
                             negatives=NEGATIVE_PROMPTS):
    """Max-over-items softmax-vs-negatives score. times: (K,) or (K,2)."""
    text_embeds = encode_fn([text] + list(negatives))
    cos = embeds @ text_embeds.T          # (K, 1+num_neg)
    scores = np.array([
        softmax_vs_negatives(cos[k, 0], cos[k, 1:], scale)
        for k in range(cos.shape[0])
    ])
    best = int(np.argmax(scores))
    t = times[best]
    best_t = float(t[0]) if np.ndim(t) else float(t)
    return {
        "raw_cosine": float(cos[:, 0].max()),
        "score": float(scores[best]),
        "best_t": best_t,
    }


def overlapping(times2d, t_from, t_to):
    """Indices of [start,end] windows overlapping [t_from, t_to]."""
    s, e = times2d[:, 0], times2d[:, 1]
    return np.nonzero((s < t_to) & (e > t_from))[0]


# --------------------------------------------------------------------------
# Derived findings
# --------------------------------------------------------------------------

def contiguous_runs(mask):
    """Yield (start_idx, end_idx) inclusive runs of True."""
    idx = np.nonzero(mask)[0]
    if len(idx) == 0:
        return
    start = prev = idx[0]
    for i in idx[1:]:
        if i == prev + 1:
            prev = i
            continue
        yield start, prev
        start = prev = i
    yield start, prev


def derive_findings(frame_delta, delta_times, frame_std, frame_times,
                    scene_boundaries):
    findings = []
    cut_times = []

    if len(frame_delta) >= 2:
        mean, std = float(frame_delta.mean()), float(frame_delta.std())
        thr = mean + CUT_SIGMA * std
        for a, b in contiguous_runs(frame_delta > thr):
            k = a + int(np.argmax(frame_delta[a:b + 1]))
            t = float(delta_times[k])
            cut_times.append(t)
            findings.append({
                "code": "cut_detect", "severity": "info",
                "t0": float(delta_times[a]), "t1": float(delta_times[b]),
                "measured": float(frame_delta[k]), "threshold": thr,
                "detail": f"frame-embedding delta spike at {t:.2f}s "
                          f"(mean+{CUT_SIGMA:.0f}sigma)",
            })

        for a, b in contiguous_runs(frame_delta < FREEZE_DELTA):
            t0 = float(frame_times[a])         # delta k covers frames k..k+1
            t1 = float(frame_times[b + 1])
            if t1 - t0 > FREEZE_MIN_S:
                findings.append({
                    "code": "freeze_detect", "severity": "warn",
                    "t0": t0, "t1": t1,
                    "measured": float(frame_delta[a:b + 1].max()),
                    "threshold": FREEZE_DELTA,
                    "detail": f"static frames for {t1 - t0:.2f}s",
                })

    for a, b in contiguous_runs(frame_std < BLACK_STD):
        findings.append({
            "code": "black_frame", "severity": "warn",
            "t0": float(frame_times[a]), "t1": float(frame_times[b]),
            "measured": float(frame_std[a:b + 1].min()),
            "threshold": BLACK_STD,
            "detail": f"{b - a + 1} near-black frame(s) "
                      f"(pixel std < {BLACK_STD:g}/255)",
        })

    if scene_boundaries:
        bounds = np.asarray(scene_boundaries, dtype=np.float64)
        for t in cut_times:
            dist = float(np.abs(bounds - t).min())
            if dist > CUT_PLANNED_TOL_S:
                findings.append({
                    "code": "cut_unplanned", "severity": "error",
                    "t0": t, "t1": t,
                    "measured": dist, "threshold": CUT_PLANNED_TOL_S,
                    "detail": f"detected cut at {t:.2f}s is {dist:.2f}s from "
                              f"nearest declared scene boundary",
                })

    return findings, cut_times


def vo_sync_finding(clap, audio_embeds, audio_times, tts_windows):
    spans = [(w["at"], w["at"] + w["duration"]) for w in tts_windows]
    speech = clap.encode_texts_ensembled(["speech"])[0]
    cos = audio_embeds @ speech

    def overlap_frac(k):
        s, e = audio_times[k]
        if e <= s:
            return 0.0
        ov = sum(max(0.0, min(e, b) - max(s, a)) for a, b in spans)
        return ov / (e - s)

    fracs = np.array([overlap_frac(k) for k in range(len(audio_times))])
    inside = cos[fracs >= 0.5]
    outside = cos[fracs <= 0.1]
    detail = {"inside_windows": int(len(inside)),
              "outside_windows": int(len(outside))}
    if len(inside) == 0:
        return {"code": "vo_sync", "severity": "warn", "t0": spans[0][0],
                "t1": spans[-1][1], "measured": None, "threshold": 0.0,
                "detail": "no audio window mostly inside declared tts spans; "
                          "vo_sync not measurable " + json.dumps(detail)}
    mean_in = float(inside.mean())
    mean_out = float(outside.mean()) if len(outside) else 0.0
    margin = mean_in - mean_out
    detail.update({"clap_speech_cos_inside": round(mean_in, 4),
                   "clap_speech_cos_outside_baseline": round(mean_out, 4)})
    return {
        "code": "vo_sync",
        "severity": "info" if margin > 0 else "warn",
        "t0": float(min(a for a, _ in spans)),
        "t1": float(max(b for _, b in spans)),
        "measured": round(margin, 4), "threshold": 0.0,
        "detail": "CLAP 'speech' margin inside-vs-outside declared tts "
                  "windows " + json.dumps(detail),
    }


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("video")
    ap.add_argument("manifest")
    ap.add_argument("--out", default="findings.json")
    ap.add_argument("--sidecar", default="sidecar.npz")
    ap.add_argument("--stride", type=float, default=4,
                    help="frame sampling rate in fps (default 4)")
    ap.add_argument("--image-backend", choices=["auto", "open_clip",
                                                "siglip2"], default="auto")
    args = ap.parse_args()

    with open(args.manifest) as f:
        manifest = json.load(f)

    t_start = time.time()
    timings = {}
    substitutions = []

    from PIL import Image  # after arg parsing; heavy deps stay lazy

    device = pick_device()
    log(f"device: {device}")

    tmpdir = tempfile.mkdtemp(prefix="ellua_probe_")
    try:
        # ---- extraction ------------------------------------------------
        t0 = time.time()
        frame_paths, frame_times = extract_frames(args.video, tmpdir,
                                                  args.stride)
        wav_path = extract_audio(args.video, tmpdir)
        timings["extract_ffmpeg"] = round(time.time() - t0, 2)
        if not frame_paths:
            log("ERROR: no frames extracted"); sys.exit(2)
        log(f"extracted {len(frame_paths)} frames @ {args.stride}fps, "
            f"audio={'yes' if wav_path else 'NO'}")

        duration = float(manifest.get(
            "duration", frame_times[-1] + 1.0 / args.stride))

        frames = [Image.open(p).convert("RGB") for p in frame_paths]
        frame_std = np.array(
            [float(np.asarray(im, dtype=np.float32).std()) for im in frames])

        # ---- frame embeddings -----------------------------------------
        t0 = time.time()
        prefer = {"auto": None, "open_clip": "open_clip",
                  "siglip2": "transformers_siglip2"}[args.image_backend]
        img_backend = build_image_text_backend(device, prefer, substitutions)
        timings["load_image_model"] = round(time.time() - t0, 2)
        log(f"image-text model: {img_backend.model_name} "
            f"({img_backend.backend}, pretrained={img_backend.pretrained})")

        t0 = time.time()
        frame_embeds = img_backend.encode_images(frames)
        timings["embed_frames"] = round(time.time() - t0, 2)

        frame_delta = 1.0 - np.sum(
            frame_embeds[:-1] * frame_embeds[1:], axis=1)
        delta_times = frame_times[1:]

        # ---- X-CLIP windows -------------------------------------------
        t0 = time.time()
        xclip = XClipVideo(device)
        timings["load_xclip"] = round(time.time() - t0, 2)

        window_frames, window_times = [], []
        w = 0.0
        while w < duration - 1e-6:
            end = min(w + FRAME_WINDOW_S, duration)
            idx = np.nonzero((frame_times >= w) & (frame_times < end))[0]
            if len(idx):
                pick = idx[np.linspace(0, len(idx) - 1, XCLIP_NUM_FRAMES)
                           .round().astype(int)]
                window_frames.append([frames[i] for i in pick])
                window_times.append([w, end])
            w += FRAME_WINDOW_S
        window_times = np.array(window_times, dtype=np.float64).reshape(-1, 2)

        t0 = time.time()
        window_embeds = (xclip.encode_windows(window_frames)
                         if window_frames else np.zeros((0, 512), np.float32))
        timings["embed_windows"] = round(time.time() - t0, 2)
        log(f"x-clip windows: {len(window_frames)}")

        # ---- CLAP audio windows ---------------------------------------
        audio_embeds = np.zeros((0, 512), dtype=np.float32)
        audio_times = np.zeros((0, 2), dtype=np.float64)
        clap = None
        if wav_path:
            t0 = time.time()
            clap = ClapAudio(device)
            timings["load_clap"] = round(time.time() - t0, 2)

            import soundfile as sf
            audio, sr = sf.read(wav_path, dtype="float32", always_2d=False)
            assert sr == clap.sr, f"wav sr {sr} != clap sr {clap.sr}"
            clips, spans = [], []
            s = 0.0
            while duration - s >= AUDIO_MIN_S:
                e = min(s + AUDIO_WINDOW_S, duration)
                seg = audio[int(s * sr):int(e * sr)]
                if len(seg) >= int(AUDIO_MIN_S * sr):
                    clips.append(seg)
                    spans.append([s, e])
                s += AUDIO_HOP_S
            if clips:
                t0 = time.time()
                audio_embeds = clap.encode_audio(clips)
                audio_times = np.array(spans, dtype=np.float64)
                timings["embed_audio"] = round(time.time() - t0, 2)
            log(f"clap audio windows: {len(clips)}")

        # ---- derived findings -----------------------------------------
        scene_boundaries = manifest.get("scene_boundaries") or []
        findings, cut_times = derive_findings(
            frame_delta, delta_times, frame_std, frame_times,
            scene_boundaries)

        tts_windows = [w for w in (manifest.get("audio_windows") or [])
                       if w.get("kind") == "tts"]
        if tts_windows and clap is not None and len(audio_embeds):
            findings.append(vo_sync_finding(clap, audio_embeds, audio_times,
                                            tts_windows))

        # ---- assertions -----------------------------------------------
        t0 = time.time()
        assertion_results = []
        for a in manifest.get("assertions", []):
            kind = a["kind"]
            t_from, t_to = float(a["from"]), float(a["to"])
            min_score = float(a.get("min", DEFAULT_MIN_SCORE))
            res = {"text": a["text"], "kind": kind,
                   "window": [t_from, t_to], "min": min_score}

            if kind in ("visible", "mood"):
                idx = np.nonzero((frame_times >= t_from)
                                 & (frame_times <= t_to))[0]
                if len(idx):
                    r = eval_embedding_assertion(
                        a["text"], frame_embeds[idx], frame_times[idx],
                        img_backend.encode_texts, img_backend.logit_scale)
                else:
                    r = None
            elif kind == "motion":
                idx = overlapping(window_times, t_from, t_to) \
                    if len(window_times) else []
                if len(idx):
                    r = eval_embedding_assertion(
                        a["text"], window_embeds[idx], window_times[idx],
                        xclip.encode_texts, xclip.logit_scale)
                else:
                    r = None
            elif kind == "audible":
                idx = overlapping(audio_times, t_from, t_to) \
                    if len(audio_times) else []
                if len(idx) and clap is not None:
                    r = eval_embedding_assertion(
                        a["text"], audio_embeds[idx], audio_times[idx],
                        clap.encode_texts_ensembled, clap.logit_scale,
                        negatives=CLAP_NEGATIVE_PROMPTS)
                else:
                    r = None
            else:
                r = None
                res["note"] = f"unknown assertion kind: {kind}"

            if r is None:
                res.update({"score": None, "raw_cosine": None,
                            "passed": False,
                            "note": res.get("note",
                                            "assertion_unevaluated")})
                findings.append({
                    "code": "assertion_unevaluated", "severity": "error",
                    "t0": t_from, "t1": t_to, "measured": None,
                    "threshold": min_score,
                    "detail": f"{kind} assertion {a['text']!r} had no "
                              f"frames/windows in [{t_from}, {t_to}]",
                })
            else:
                res.update(r)
                res["passed"] = bool(r["score"] >= min_score)
            assertion_results.append(res)
        timings["assertions"] = round(time.time() - t0, 2)

        # ---- outputs ---------------------------------------------------
        timings["total"] = round(time.time() - t_start, 2)
        meta = {
            "video": os.path.abspath(args.video),
            "duration": duration,
            "stride_fps": args.stride,
            "device": device,
            "models": {
                "image_text": {"backend": img_backend.backend,
                               "model": img_backend.model_name,
                               "pretrained": img_backend.pretrained},
                "video": XCLIP_MODEL_ID,
                "audio": CLAP_MODEL_ID if clap is not None else None,
            },
            "substitutions": substitutions,
            "negative_prompts": {"image_video": NEGATIVE_PROMPTS,
                                 "audio": CLAP_NEGATIVE_PROMPTS,
                                 "audio_templates": CLAP_TEMPLATES},
            "counts": {"frames": len(frames),
                       "xclip_windows": int(len(window_times)),
                       "audio_windows": int(len(audio_times))},
            "timings_s": timings,
        }

        sidecar_meta = {
            "image_text_model": meta["models"]["image_text"],
            "video_model": XCLIP_MODEL_ID,
            "audio_model": meta["models"]["audio"],
            "stride_fps": args.stride,
            "video": meta["video"],
            "duration": duration,
        }
        np.savez_compressed(
            args.sidecar,
            frame_embeds=frame_embeds.astype(np.float32),
            frame_times=frame_times,
            window_embeds=window_embeds.astype(np.float32),
            window_times=window_times,
            audio_embeds=audio_embeds.astype(np.float32),
            audio_times=audio_times,
            frame_delta=frame_delta.astype(np.float32),
            frame_std=frame_std.astype(np.float32),
            meta=json.dumps(sidecar_meta),
        )

        out = {"meta": meta, "findings": findings,
               "assertions": assertion_results, "query_ready": True}
        with open(args.out, "w") as f:
            json.dump(out, f, indent=2)

        log(f"wrote {args.out} ({len(findings)} findings, "
            f"{len(assertion_results)} assertions) and {args.sidecar}")
        print(json.dumps(out, indent=2))
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
