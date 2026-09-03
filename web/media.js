const PATH_RE = /evals\/assets\/[A-Za-z0-9_./-]+/g;

export function assetUrl(src) {
  return new URL(`../${src}`, import.meta.url).href;
}

export function fontFamily(src) {
  if (!src) return "Inter, ui-sans-serif, system-ui, sans-serif";
  return `"ellua_${src.replace(/[^A-Za-z0-9]/g, "_")}"`;
}

export function cssFont(size, src) {
  return `${Math.round(size)}px ${fontFamily(src)}`;
}

function collectPaths(luaSrc) {
  return [...new Set(luaSrc.match(PATH_RE) || [])];
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.decoding = "async";
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`image ${url}`));
    img.src = url;
  });
}

function attachVideo(video) {
  video.muted = true;
  video.defaultMuted = true;
  video.playsInline = true;
  video.preload = "auto";
  video.setAttribute("playsinline", "");
  video.setAttribute("muted", "");
  if (!video.isConnected) {
    video.style.cssText =
      "position:fixed;left:-4096px;top:0;width:480px;height:270px;pointer-events:none;border:0";
    document.body.appendChild(video);
  }
  return video;
}

function loadVideoSrc(url) {
  return new Promise((resolve, reject) => {
    const video = attachVideo(document.createElement("video"));
    video.addEventListener("loadeddata", () => resolve(video), { once: true });
    video.addEventListener("error", () => reject(new Error(`video ${url}`)), { once: true });
    video.src = url;
    video.load();
  });
}

function makeVideo(url) {
  const video = attachVideo(document.createElement("video"));
  video.src = url;
  video.load();
  return video;
}

function makeAudio(url) {
  const audio = new Audio();
  audio.preload = "auto";
  audio.src = url;
  return audio;
}

export class MediaBag {
  constructor() {
    this.images = new Map();
    this.sheets = new Map();
    this.lottieData = new Map();
    this.lotties = new Map();
    this.videos = new Map();
    this.audios = new Map();
    this._lottieMod = null;
    this.playing = false;
    this.masterVolume = 1;
    /** Optional override for asset URL resolution (Tauri preview). */
    this.resolveUrl = null;
  }

  dispose() {
    for (const el of this.videos.values()) {
      el.pause();
      el.remove();
    }
    for (const rec of this.audios.values()) {
      rec.el.pause();
      rec.el.removeAttribute("src");
    }
    for (const rec of this.lotties.values()) {
      rec.anim?.destroy();
      rec.wrap?.remove();
    }
    for (const [key, val] of this.images) {
      if (key.startsWith("video:") && val instanceof HTMLVideoElement) {
        val.pause();
        val.remove();
      }
    }
    this.images.clear();
    this.sheets.clear();
    this.lottieData.clear();
    this.lotties.clear();
    this.videos.clear();
    this.audios.clear();
  }

  async loadFromComp(luaSrc) {
    const paths = collectPaths(luaSrc);
    const jobs = [];
    for (const src of paths) {
      const url = assetUrl(src);
      const ext = src.split(".").pop().toLowerCase();
      if (ext === "ttf" || ext === "otf" || ext === "woff" || ext === "woff2") {
        jobs.push(this._font(src, url));
      } else if (ext === "jpg" || ext === "jpeg" || ext === "png" || ext === "svg" || ext === "webp") {
        jobs.push(loadImage(url).then((img) => this.images.set(src, img)));
      } else if (ext === "webm" || ext === "mp4") {
        jobs.push(loadVideoSrc(url).then((video) => this.images.set(`video:${src}`, video)));
      } else if (ext === "ogg" || ext === "mp3" || ext === "wav") {
        jobs.push(Promise.resolve());
      } else if (ext === "json") {
        jobs.push(this._json(src, url));
      }
    }
    const results = await Promise.allSettled(jobs);
    const failed = results.filter((r) => r.status === "rejected");
    if (failed.length) {
      console.warn("ellua web: some assets failed", failed.map((r) => r.reason));
    }
  }

  async _font(src, url) {
    const face = new FontFace(fontFamily(src).replaceAll('"', ""), `url(${url})`);
    await face.load();
    document.fonts.add(face);
  }

  async _json(src, url) {
    const data = await fetch(url).then((res) => {
      if (!res.ok) throw new Error(`json ${url}`);
      return res.json();
    });
    if (data.v && data.fr && data.layers) {
      this.lottieData.set(src, data);
      return;
    }
    if (data.frames && data.meta) {
      const dir = src.replace(/\/[^/]+$/, "");
      const image = data.meta.image.startsWith("/")
        ? data.meta.image.slice(1)
        : `${dir}/${data.meta.image}`;
      const frames = (Array.isArray(data.frames) ? data.frames : Object.values(data.frames)).map((fr) => ({
        x: fr.frame.x,
        y: fr.frame.y,
        w: fr.frame.w,
        h: fr.frame.h,
        duration: (fr.duration || 100) / 1000,
      }));
      const img = await loadImage(assetUrl(image));
      this.images.set(image, img);
      this.sheets.set(src, { image, frames });
    }
  }

  image(src) {
    return this.images.get(src);
  }

  sheet(src) {
    return this.sheets.get(src);
  }

  _ensureVideo(node) {
    let el = this.videos.get(node.id);
    if (el) return el;
    const pre = this.images.get(`video:${node.src}`);
    if (pre && !pre._claimed) {
      pre._claimed = true;
      el = pre;
    } else {
      el = makeVideo(assetUrl(node.src));
    }
    attachVideo(el);
    this.videos.set(node.id, el);
    return el;
  }

  _syncVideo(el, want) {
    if (el.seeking) return;
    const drift = Math.abs((el.currentTime || 0) - want);
    if (this.playing) {
      if (drift > 0.3) el.currentTime = want;
      if (el.paused) el.play().catch(() => {});
    } else {
      if (!el.paused) el.pause();
      if (drift > 1 / 30) el.currentTime = want;
    }
  }

  video(node, t) {
    const from = node.from || 0;
    const duration = node.duration ?? Infinity;
    const rel = t - from;
    if (rel < 0 || rel >= duration) {
      const el = this.videos.get(node.id);
      if (el && !el.paused) el.pause();
      return null;
    }
    const el = this._ensureVideo(node);
    const want = (node.media_start || 0) + rel;
    if (Number.isFinite(el.duration) && el.duration > 0) {
      this._syncVideo(el, Math.min(Math.max(0, want), el.duration - 0.001));
    } else {
      this._syncVideo(el, Math.max(0, want));
    }
    return el.videoWidth > 0 ? el : null;
  }

  pauseAudio() {
    for (const rec of this.audios.values()) rec.el.pause();
  }

  pauseVideos() {
    for (const el of this.videos.values()) el.pause();
  }

  audioGain(clip, t) {
    const at = clip.at || 0;
    const rel = t - at;
    if (rel < 0 || rel >= clip.duration) return 0;
    let g = clip.volume ?? 1;
    if (clip.fade_in && rel < clip.fade_in) g *= rel / clip.fade_in;
    if (clip.fade_out && rel > clip.duration - clip.fade_out) {
      g *= (clip.duration - rel) / clip.fade_out;
    }
    return Math.max(0, Math.min(1, g));
  }

  syncAudio(clips, t, playing) {
    const resolve = this.resolveUrl || assetUrl;
    for (const clip of clips || []) {
      let rec = this.audios.get(clip.src);
      if (!rec) {
        rec = { el: makeAudio(resolve(clip.src)) };
        this.audios.set(clip.src, rec);
      }
      const gain = this.audioGain(clip, t) * (this.masterVolume ?? 1);
      rec.el.volume = gain;
      const want = (clip.media_start || 0) + (t - (clip.at || 0));
      if (gain > 0 && playing) {
        if (Math.abs(rec.el.currentTime - want) > 0.25) rec.el.currentTime = Math.max(0, want);
        if (rec.el.paused) rec.el.play().catch(() => {});
      } else if (!rec.el.paused) {
        rec.el.pause();
      }
    }
  }

  async lottie(node, t) {
    const data = this.lottieData.get(node.src);
    if (!data) return null;
    let rec = this.lotties.get(node.id);
    if (!rec) {
      const lottie = await this._lottie();
      if (!lottie) return null;
      const wrap = document.createElement("div");
      wrap.style.cssText = `position:absolute;left:-9999px;top:0;width:${node.w}px;height:${node.h}px;overflow:hidden;`;
      document.body.appendChild(wrap);
      const anim = lottie.loadAnimation({
        container: wrap,
        renderer: "canvas",
        loop: false,
        autoplay: false,
        animationData: data,
        rendererSettings: { clearCanvas: true },
      });
      rec = { wrap, anim };
      this.lotties.set(node.id, rec);
    }
    const from = node.from || 0;
    const duration = node.duration ?? Infinity;
    const rel = t - from;
    if (rel < 0 || rel >= duration) return null;
    rec.anim.goToAndStop(this._lottieMs(node, t, data), false);
    return rec.wrap.querySelector("canvas");
  }

  lottieCanvas(node, t) {
    const rec = this.lotties.get(node.id);
    if (!rec) {
      this.lottie(node, t);
      return null;
    }
    const from = node.from || 0;
    const duration = node.duration ?? Infinity;
    const rel = t - from;
    if (rel < 0 || rel >= duration) return null;
    rec.anim.goToAndStop(this._lottieMs(node, t, this.lottieData.get(node.src)), false);
    return rec.wrap.querySelector("canvas");
  }

  _lottieMs(node, t, data) {
    const rel = t - (node.from || 0);
    let ms = ((node.media_start || 0) + rel * (node.speed || 1)) * 1000;
    const total = data && data.fr ? ((data.op - data.ip) / data.fr) * 1000 : 0;
    if (node.loop !== false && total > 0) ms = ms % total;
    return ms;
  }

  async warm(frame) {
    const jobs = [];
    for (const node of frame.nodes || []) {
      if (node.kind === "video") {
        const el = this._ensureVideo(node);
        if (el.readyState < 2) {
          jobs.push(new Promise((resolve) => {
            el.addEventListener("loadeddata", resolve, { once: true });
            el.addEventListener("error", resolve, { once: true });
          }));
        }
      } else if (node.kind === "lottie") {
        jobs.push(this.lottie(node, frame.t || 0));
      }
    }
    await Promise.all(jobs);
    this.playing = true;
    for (const node of frame.nodes || []) {
      if (node.kind === "video") this.video(node, frame.t || 0);
    }
  }

  async _lottie() {
    if (this._lottieMod) return this._lottieMod;
    try {
      const mod = await import("https://cdn.jsdelivr.net/npm/lottie-web@5.12.2/+esm");
      this._lottieMod = mod.default?.loadAnimation ? mod.default : mod;
      if (typeof this._lottieMod.loadAnimation !== "function") this._lottieMod = null;
      return this._lottieMod;
    } catch (err) {
      console.warn("ellua web: lottie-web failed to load", err);
      return null;
    }
  }
}
