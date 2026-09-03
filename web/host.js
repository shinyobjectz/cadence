import { LuaFactory } from "https://cdn.jsdelivr.net/npm/wasmoon@1.16.0/+esm";
import { paint, measureText } from "./painter.js?v=6";
import { MediaBag } from "./media.js?v=4";

const CASES = [
  { id: "primitives", title: "Primitives", file: "../evals/cases/primitives.lua" },
  { id: "eases", title: "Eases", file: "../evals/cases/eases.lua" },
  { id: "type", title: "Type", file: "../evals/cases/type.lua" },
  { id: "fonts", title: "Fonts", file: "../evals/cases/fonts.lua" },
  { id: "type_styles", title: "Type styles", file: "../evals/cases/type_styles.lua" },
  { id: "compositing", title: "Compositing", file: "../evals/cases/compositing.lua" },
  { id: "blend_modes", title: "Blend modes", file: "../evals/cases/blend_modes.lua" },
  { id: "motion", title: "Motion", file: "../evals/cases/motion.lua" },
  { id: "image", title: "Image", file: "../evals/cases/image.lua" },
  { id: "video", title: "Video", file: "../evals/cases/video.lua" },
  { id: "video_layers", title: "Video layers", file: "../evals/cases/video_layers.lua" },
  { id: "svg", title: "SVG", file: "../evals/cases/svg.lua" },
  { id: "lottie", title: "Lottie", file: "../evals/cases/lottie.lua" },
  { id: "audio", title: "Audio mix", file: "../evals/cases/audio.lua" },
  { id: "type_wrap", title: "Wrapped type", file: "../evals/cases/type_wrap.lua" },
  { id: "spritesheet", title: "Spritesheet", file: "../evals/cases/spritesheet.lua" },
  { id: "particles", title: "Particles", file: "../evals/cases/particles.lua" },
  { id: "hsluv", title: "HSLuv", file: "../evals/cases/hsluv.lua" },
  { id: "okhsl", title: "OKHSL", file: "../evals/cases/okhsl.lua" },
  { id: "chart", title: "Charts", file: "../evals/cases/chart.lua" },
  { id: "ornaments", title: "Ornaments", file: "../evals/cases/ornaments.lua" },
  { id: "bounce", title: "Bounce", file: "../evals/cases/bounce.lua" },
  { id: "captions", title: "Captions", file: "../evals/cases/captions.lua" },
  { id: "spine", title: "Spine", file: "../evals/cases/spine.lua" },
  { id: "pulse", title: "Pulse", file: "../evals/cases/pulse.lua" },
];

const LIB = [
  "init.lua", "timeline.lua", "color.lua", "ease.lua", "hsluv.lua", "json.lua",
  "okhsl.lua", "rough.lua", "chart.lua", "ornament.lua", "captions.lua", "spine.lua",
];

const $ = (id) => document.getElementById(id);

const state = {
  lua: null,
  media: null,
  meta: { width: 1280, height: 720, duration: 2, fps: 30 },
  playing: true,
  t0: 0,
  elapsed: 0,
  raf: 0,
};

function setStatus(text, kind = "") {
  const el = $("status");
  el.textContent = text;
  el.dataset.kind = kind;
}

async function fetchText(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`fetch ${path}: ${res.status}`);
  return res.text();
}

// wasmoon leaves return values on the Lua stack. doString every rAF grows
// it until WASM hits "memory access out of bounds".
function callLua(name, ...args) {
  const lua = state.lua;
  const top = lua.global.getTop();
  try {
    const results = lua.global.call(name, ...args);
    return results && results.length ? results[0] : undefined;
  } finally {
    const extra = lua.global.getTop() - top;
    if (extra > 0) lua.global.pop(extra);
  }
}

function runLua(script) {
  const lua = state.lua;
  const top = lua.global.getTop();
  try {
    return lua.doStringSync(script);
  } finally {
    const extra = lua.global.getTop() - top;
    if (extra > 0) lua.global.pop(extra);
  }
}

async function boot() {
  setStatus("loading Lua WASM…");
  const factory = new LuaFactory();
  for (const name of LIB) {
    await factory.mountFile(`ellua/${name}`, await fetchText(`../lib/ellua/${name}`));
  }
  await factory.mountFile("bridge.lua", await fetchText("./bridge.lua"));

  const lua = await factory.createEngine();
  state.lua = lua;
  runLua(`
    unpack = table.unpack
    math.atan2 = math.atan2 or math.atan
    package.path = "?.lua;?/init.lua;" .. package.path
    math.randomseed(0)
    require("bridge")
  `);
  lua.global.set("__measure_text", (text, size, font) =>
    measureText(String(text ?? ""), Number(size) || 32, font ? String(font) : null));
}

async function loadCase(file) {
  setStatus(`loading ${file}…`);
  const src = await fetchText(file);
  state.media?.dispose();
  state.media = new MediaBag();
  await state.media.loadFromComp(src);
  const meta = callLua("compile_comp", src);
  state.meta = {
    width: Number(meta.width) || 1280,
    height: Number(meta.height) || 720,
    duration: Number(meta.duration) || 2,
    fps: Number(meta.fps) || 30,
  };
  const preview = JSON.parse(callLua("snapshot", 0));
  await state.media.warm(preview);
  state.elapsed = 0;
  state.t0 = performance.now();
  $("scrub").max = String(state.meta.duration);
  $("scrub").step = "0.001";
  resizeCanvas(state.meta.width, state.meta.height);
  setStatus(`wasm · ${state.meta.width}×${state.meta.height} · ${state.meta.duration}s`, "ok");
}

function resizeCanvas(w, h) {
  const canvas = $("stage");
  const maxW = Math.min(1280, window.innerWidth - 48);
  const scale = Math.min(1, maxW / w);
  canvas.width = w;
  canvas.height = h;
  canvas.style.width = `${Math.round(w * scale)}px`;
  canvas.style.height = `${Math.round(h * scale)}px`;
}

function draw(t) {
  if (state.media) state.media.playing = state.playing;
  const json = callLua("snapshot", t);
  const frame = JSON.parse(json);
  state.media?.syncAudio(frame.audios, t, state.playing);
  const ctx = $("stage").getContext("2d");
  paint(ctx, frame, t, state.media);
  $("time").textContent = `${t.toFixed(2)}s`;
  $("scrub").value = String(t);
}

function tick(now) {
  if (!state.playing) return;
  try {
    const t = ((now - state.t0) / 1000 + state.elapsed) % state.meta.duration;
    draw(t);
  } catch (err) {
    pause();
    setStatus(String(err.message || err), "err");
    console.error(err);
    return;
  }
  state.raf = requestAnimationFrame(tick);
}

function play() {
  state.playing = true;
  state.t0 = performance.now();
  $("play").textContent = "Pause";
  state.raf = requestAnimationFrame(tick);
}

function pause() {
  state.playing = false;
  cancelAnimationFrame(state.raf);
  state.elapsed = Number($("scrub").value);
  $("play").textContent = "Play";
  state.media?.pauseAudio();
  state.media?.pauseVideos();
}

function populateCases() {
  const sel = $("case");
  for (const item of CASES) {
    const opt = document.createElement("option");
    opt.value = item.file;
    opt.textContent = item.title;
    sel.appendChild(opt);
  }
}

async function main() {
  populateCases();
  try {
    await boot();
    await loadCase(CASES[0].file);
    play();
  } catch (err) {
    console.error(err);
    setStatus(String(err.message || err), "err");
  }

  $("case").addEventListener("change", async (ev) => {
    pause();
    try {
      await loadCase(ev.target.value);
      play();
    } catch (err) {
      console.error(err);
      setStatus(String(err.message || err), "err");
    }
  });
  $("play").addEventListener("click", () => {
    if (state.playing) pause();
    else {
      state.elapsed = Number($("scrub").value);
      play();
    }
  });
  $("scrub").addEventListener("input", (ev) => {
    pause();
    try {
      draw(Number(ev.target.value));
    } catch (err) {
      setStatus(String(err.message || err), "err");
    }
  });
}

main();
