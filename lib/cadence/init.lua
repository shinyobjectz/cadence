-- ellua authoring core. Host-free pure Lua: no love.*, no I/O, no clocks.
--
-- Comp file shape:
--   local e = require("ellua")
--   return e.comp {
--     width = 1280, height = 720, duration = 4, fps = 30,
--     scene = function(s)
--       local box = s:rect{ x=100, y=100, w=200, h=120, color="#4fa8f2" }
--       s:script(function(t)
--         t:tween(box, 1.0, { x = 800 }, "cubicInOut")
--         t:wait(0.5)
--         t:parallel(
--           function() t:tween(box, 0.5, { rotation = math.pi/4 }, "backOut") end,
--           function() t:tween(box, 0.5, { opacity = 0.3 }) end)
--       end)
--     end,
--   }
--
-- Scripts read top-to-bottom like a screenplay but are executed ONCE at compile
-- time, recording absolute-time segments onto a timeline. Rendering evaluates the
-- timeline at arbitrary t — seek, not playback.

local Timeline = require("cadence.timeline")
local color = require("cadence.color")

local M = {}

local ANIMATABLE = {
  x = true, y = true, w = true, h = true, r = true, rx = true,
  rotation = true, scale = true, opacity = true, color = true, size = true,
  progress = true, -- html nodes: {{progress}} substitution, re-rendered on change
  tracking = true, -- text letter-spacing in px (cadence-scene renderer; love ignores it)
  -- Adjustment-layer controls. These stay flat so timeline segments can seek
  -- them independently; `effects = { blur = 8, contrast = 1.1 }` is only
  -- authoring sugar and expands to these properties at node construction.
  effect_blur = true, effect_brightness = true, effect_contrast = true,
  effect_saturate = true, effect_grayscale = true, effect_sepia = true,
  effect_invert = true, effect_opacity = true, effect_hue_rotate = true,
  -- 3D camera on a flat surface (perspective = true). See runtime/persp.lua.
  -- Shared s:camera / s:world look-at. Mesh z is world-space, not screen.
  yaw = true, pitch = true, roll = true, dolly = true, fov = true,
  aperture = true, maxcoc = true, focus_u = true, focus_v = true,
  truck_u = true, truck_v = true,
  look_x = true, look_y = true, look_z = true,
  cam_x = true, cam_y = true, cam_z = true, z = true,
  -- cursor composited onto the surface (page-normalized), so it warps with it
  cursor_u = true, cursor_v = true, cursor_w = true, cursor_opacity = true,
  -- type-on (0..1 of visible characters) and mesh displacement
  reveal = true, amp = true, freq = true,
  -- moonshine-style fx uniforms (s:fx chain)
  fx_bloom = true, fx_vignette = true, fx_chroma = true,
  fx_grain = true, fx_glow = true, fx_blur = true,
  fx_tonemap = true, fx_pixelate = true, fx_posterize = true, fx_worley = true,
  mix = true, -- chart data→data1 lerp
  outline = true, weight = true, -- SDF/coverage type
}

-- ---------- nodes ----------
local Node = {}
Node.__index = Node

function Node:get(prop)
  local v = self.state[prop]
  if v == nil then v = self.initial[prop] end
  return v
end

-- ---------- forced alignment (audio/tts nodes with align = true) ----------
-- resolve populates node.words = { {text=, t0=, t1=}, ... } relative to the clip.
-- These accessors return COMP-ABSOLUTE seconds, so a cue written as
--   at(vo3:word("languages"))
-- stays correct when the copy, the voice, or the clip's start time changes.
local function norm(w) return (w:lower():gsub("[^%w']", "")) end

local function find_word(self, needle, nth)
  assert(self.words, "cadence: node has no alignment — pass align = true to tts{}/audio{}")
  local want, hit = norm(needle), 0
  for _, w in ipairs(self.words) do
    if norm(w.text) == want then
      hit = hit + 1
      if hit == (nth or 1) then return w end
    end
  end
  error(("cadence: word %q not found in aligned narration %q"):format(needle,
    self.initial.text or self.initial.src or "?"), 0)
end

-- start of a spoken word, comp-absolute
function Node:word(needle, nth)
  return self.initial.at + find_word(self, needle, nth).t0
end

-- end of a spoken word, comp-absolute (use to land a beat as the word finishes)
function Node:word_end(needle, nth)
  return self.initial.at + find_word(self, needle, nth).t1
end

-- every aligned word as comp-absolute {text, t0, t1}; drives per-word captions
function Node:word_times()
  assert(self.words, "cadence: node has no alignment — pass align = true")
  local out = {}
  for k, w in ipairs(self.words) do
    out[k] = { text = w.text, t0 = self.initial.at + w.t0, t1 = self.initial.at + w.t1 }
  end
  return out
end

-- find a phoneme or viseme within aligned narration
function Node:phoneme(needle, nth)
  assert(self.phonemes, "cadence: node has no phoneme alignment")
  local want, hit = needle:lower(), 0
  for _, p in ipairs(self.phonemes) do
    if p.text:lower() == want then
      hit = hit + 1
      if hit == (nth or 1) then return self.initial.at + p.t0 end
    end
  end
  error(("cadence: phoneme %q not found in aligned narration"):format(needle), 0)
end

-- find an extracted feature event (e.g. "beat", "drop", "cut", "motion_peak")
function Node:event(name, nth)
  assert(self.events, "cadence: node has no extracted events — pass analyze = true")
  local want, hit = name:lower(), 0
  for _, ev in ipairs(self.events) do
    if ev.name:lower() == want or ev.type:lower() == want then
      hit = hit + 1
      if hit == (nth or 1) then return self.initial.at + ev.t0 end
    end
  end
  error(("cadence: event %q not found on node %s"):format(name, self.id), 0)
end

-- SMPTE timecode helper: converts seconds to "HH:MM:SS:FF" or "HH:MM:SS;FF" (drop frame)
function Node:smpte(t, fps, drop_frame)
  fps = fps or 30
  t = t or (self.initial and self.initial.at or 0)
  local total_frames = math.floor(t * fps + 0.5)
  local f = total_frames % fps
  local total_sec = math.floor(total_frames / fps)
  local s = total_sec % 60
  local total_min = math.floor(total_sec / 60)
  local m = total_min % 60
  local h = math.floor(total_min / 60)
  local sep = drop_frame and ";" or ":"
  return string.format("%02d:%02d:%02d%s%02d", h, m, s, sep, f)
end

local node_defaults = {
  opacity = 1, rotation = 0, scale = 1, anchor = "topleft",
  effect_blur = 0, effect_brightness = 1, effect_contrast = 1,
  effect_saturate = 1, effect_grayscale = 0, effect_sepia = 0,
  effect_invert = 0, effect_opacity = 1, effect_hue_rotate = 0,
}

local function make_node(kind, id, props)
  local initial = {}
  for k, v in pairs(node_defaults) do initial[k] = v end
  for k, v in pairs(props) do initial[k] = v end
  if type(props.effects) == "table" then
    for key, value in pairs(props.effects) do
      local prop = "effect_" .. key
      assert(ANIMATABLE[prop], "cadence: unknown effect " .. tostring(key))
      initial[prop] = value
    end
  end
  -- s:fx { bloom = 0.5, vignette = 0.4 } sugar → fx_bloom / fx_vignette
  for alias, prop in pairs({
    bloom = "fx_bloom", vignette = "fx_vignette", chroma = "fx_chroma",
    grain = "fx_grain", glow = "fx_glow", blur = "fx_blur",
    tonemap = "fx_tonemap", pixelate = "fx_pixelate", posterize = "fx_posterize",
    worley = "fx_worley",
  }) do
    if props[alias] ~= nil and initial[prop] == nil then
      initial[prop] = props[alias]
    end
  end
  if initial.color ~= nil then initial.color = color.parse(initial.color) end
  if initial.outline_color ~= nil then initial.outline_color = color.parse(initial.outline_color) end
  return setmetatable({ kind = kind, id = id, initial = initial, state = {} }, Node)
end

-- ---------- scene builder ----------
local Scene = {}
Scene.__index = Scene

local function add(s, kind, props)
  s.count = s.count + 1
  local id = props.id or (kind .. s.count)
  local n = make_node(kind, id, props)
  s.nodes[#s.nodes + 1] = n
  return n
end

function Scene:rect(p) return add(self, "rect", p) end
-- Surface is a named visual backplate: visually a rect, semantically a parent
-- for a UI assembly. It supports shadow and blend without making authoring code
-- overload `rect`. It does not yet flatten children into an offscreen texture;
-- child effects remain per raster-backed node.
function Scene:surface(p)
  assert(p.w and p.h, "cadence: surface{w=,h=} required")
  return add(self, "surface", p)
end
function Scene:circle(p) return add(self, "circle", p) end
function Scene:text(p) return add(self, "text", p) end
-- Video layer (v0: resolve phase extracts frames at comp fps, cover-cropped to w×h).
-- Structural (not animatable): src, from (comp-time start), duration, media_start.
function Scene:video(p)
  assert(type(p.src) == "string", "cadence: video{src=...} required")
  return add(self, "video", p)
end
function Scene:image(p)
  assert(type(p.src) == "string", "cadence: image{src=...} required")
  return add(self, "image", p)
end
-- Kinetic text: splits text into per-char nodes (positions measured by the host
-- at compile via post_scene). Returns the container; container.chars = array of
-- char nodes for t:stagger / per-char tweens. anchor: "center" on p.x.
function Scene:kinetic(p)
  assert(type(p.text) == "string" and #p.text > 0, "cadence: kinetic{text=...} required")
  local container = add(self, "kinetic", {
    text = p.text, x = p.x or 0, y = p.y or 0, size = p.size or 64,
    spacing = p.spacing or 0, font = p.font,
  })
  container.chars = {}
  for ch in p.text:gmatch(".") do
    if ch ~= " " then
      local node = add(self, "text", {
        text = ch, size = p.size or 64, color = p.color or "#ffffff",
        anchor = "center", x = 0, y = p.y or 0, font = p.font,
        opacity = p.opacity, rotation = p.rotation, scale = p.scale,
      })
      node._kinetic_char = ch
      node._group = container -- lint: chars of one word are one motion unit
      container.chars[#container.chars + 1] = node
    end
  end
  return container
end

-- Audio clips: never rendered by the engine — resolved pre-render, mixed by
-- ffmpeg filter_complex at encode (canon §1.5). Props: at (comp seconds),
-- duration (default = clip length), media_start, volume, fade_in, fade_out.
function Scene:audio(p)
  assert(type(p.src) == "string", "cadence: audio{src=...} required")
  return add(self, "audio", p)
end
-- ElevenLabs TTS: resolve phase generates + caches the clip; after resolve,
-- node.initial.duration holds the real length so scripts can pace to narration.
function Scene:tts(p)
  assert(type(p.text) == "string", "cadence: tts{text=...} required")
  return add(self, "tts", p)
end
-- ElevenLabs sound effect from a text prompt.
function Scene:sfx(p)
  assert(type(p.prompt) == "string", "cadence: sfx{prompt=...} required")
  return add(self, "sfx", p)
end
-- Eleven Music track from a prompt (paid-plan API; free tier gets a clear error).
-- gen_duration in seconds (3-300). Same content-hash caching as tts/sfx.
function Scene:music(p)
  assert(type(p.prompt) == "string", "cadence: music{prompt=...} required")
  return add(self, "music", p)
end
-- HTML/CSS layer (Blitz: Stylo + Taffy + vello_cpu — no Chrome). Static content
-- renders once and is cached; tweening `progress` substitutes {{progress}} in the
-- markup and re-renders per change. Transforms animate like any node.
function Scene:html(p)
  assert(type(p.html) == "string" or type(p.src) == "string",
    "cadence: html{html=...} or html{src=...} required")
  assert(p.w and p.h, "cadence: html{w=,h=} required")
  return add(self, "html", p)
end
-- Full HTML document painted to a viewport (linked CSS/images/fonts). Rasterized
-- once at resolve — fetch and optional script bake live there, never in evaluate(t).
function Scene:page(p)
  assert(type(p.html) == "string" or type(p.src) == "string",
    "cadence: page{html=...} or page{src=...} required")
  return add(self, "page", p)
end
-- Procedural vector layer (vello_cpu): draw fn(v, t) builds a display list each
-- frame — antialiased paths/beziers/gradients love.graphics lacks. fn must be
-- pure f(t): no clocks, no state, no I/O.
function Scene:vector(p)
  assert(type(p.draw) == "function", "cadence: vector{draw=function(v,t) ...} required")
  assert(p.w and p.h, "cadence: vector{w=,h=} required")
  return add(self, "vector", p)
end
-- Group: a transform parent. Children declare `parent = group` and inherit the
-- group's x/y/scale/rotation/opacity, so a whole UI assembly moves as one unit.
-- `clip_node` on children provides a live mask; `blend` accepts LÖVE blend modes.
-- Effects are available on raster-backed child nodes (`html` and `vector`) and
-- accept CSS-style sugar, e.g. effects={blur=8, contrast=1.1}.
function Scene:group(p)
  return add(self, "group", p or {})
end
-- Flex container (Taffy): positions its item nodes at compile/resolve time.
-- Structural: x,y,w,h,dir("row"|"column"),justify,align,gap,pad,wrap,items={nodes}.
-- Solved once before render — layout is static; item x/y get overwritten.
function Scene:flex(p)
  assert(type(p.items) == "table" and #p.items > 0, "cadence: flex{items={...}} required")
  return add(self, "flex", p)
end
-- SVG layer (resvg): rasterized at resolve time to exactly w×h, cached.
function Scene:svg(p)
  assert(type(p.src) == "string", "cadence: svg{src=...} required")
  assert(p.w and p.h, "cadence: svg{w=,h=} required")
  return add(self, "svg", p)
end
-- Lottie layer (ThorVG): frame-addressed vector animation. Structural: src, from,
-- duration, loop, speed. w/h define the raster target size.
function Scene:lottie(p)
  assert(type(p.src) == "string", "cadence: lottie{src=...} required")
  assert(p.w and p.h, "cadence: lottie{w=,h=} required")
  return add(self, "lottie", p)
end
-- Post-FX group: children parented here are flattened to an offscreen canvas,
-- then a moonshine-style shader chain runs. Uniforms (bloom, vignette, chroma,
-- grain, glow, blur) are timeline-animatable. Optional `shadertoy` GLSL is
-- converted to a LÖVE effect (iTime = t). Seek-safe: shaders + uniforms only.
function Scene:fx(p)
  assert(p.w and p.h, "cadence: fx{w=,h=} required")
  p.chain = p.chain or { "bloom", "vignette" }
  p.fx_bloom = p.fx_bloom or p.bloom or 0.45
  p.fx_vignette = p.fx_vignette or p.vignette or 0.35
  p.fx_chroma = p.fx_chroma or p.chroma or 1.2
  p.fx_grain = p.fx_grain or p.grain or 0.06
  p.fx_glow = p.fx_glow or p.glow or 0.4
  p.fx_blur = p.fx_blur or p.blur or 0
  p.fx_tonemap = p.fx_tonemap or p.tonemap or 0
  p.fx_pixelate = p.fx_pixelate or p.pixelate or 0
  p.fx_posterize = p.fx_posterize or p.posterize or 0
  p.fx_worley = p.fx_worley or p.worley or 0
  return add(self, "fx", p)
end
-- Frame-addressed spritesheet. `src` is a PNG (optionally with Aseprite JSON
-- via `json=` or a .json src whose meta.image points at the sheet). Frame =
-- floor((t-from)*fps) % count, or Aseprite per-frame durations if fps is nil.
function Scene:spritesheet(p)
  assert(type(p.src) == "string", "cadence: spritesheet{src=...} required")
  return add(self, "spritesheet", p)
end
-- Closed-form particles. Particle i = f(t − birth_i) from hash(seed, i).
-- No love.graphics.newParticleSystem — any-order seek is identical.
function Scene:particles(p)
  assert(type(p.n) == "number" and p.n > 0, "cadence: particles{n=} required")
  p.seed = p.seed or 1
  p.life = p.life or 1.2
  p.emit = p.emit or 0
  p.emit_window = p.emit_window or 0.2
  p.spread = p.spread or math.pi
  p.heading = p.heading or -math.pi / 2
  p.speed = p.speed or 240
  p.gravity = p.gravity or 520
  p.r = p.r or 3.5
  return add(self, "particles", p)
end
-- Data motion (d3-shape port). type = line|area|bar|stack|pie|arc.
-- reveal clips path length; mix lerps data→data1; stroke="rough" is seeded.
function Scene:chart(p)
  assert(type(p.data) == "table" and #p.data > 0, "cadence: chart{data=} required")
  p.type = p.type or "bar"
  p.w = p.w or 420
  p.h = p.h or 240
  p.reveal = p.reveal or 1
  p.mix = p.mix or 0
  p.stroke = p.stroke or "clean"
  p.seed = p.seed or 1
  if p.colors then
    for i, c in ipairs(p.colors) do
      if type(c) == "string" then p.colors[i] = color.parse(c) end
    end
  end
  return add(self, "chart", p)
end
-- Motion-bumper ornaments: star, compass, egg, linker. Polylines; optional rough.
function Scene:ornament(p)
  p.kind = p.kind or p.type or "star"
  p.w = p.w or 180
  p.h = p.h or 180
  p.reveal = p.reveal or 1
  p.stroke = p.stroke or "clean"
  p.seed = p.seed or 1
  p.width = p.width or 2.5
  return add(self, "ornament", p)
end
-- Captions: inline cues or SRT/CSV src (resolved to cues). Text steps on the timeline.
-- Build cues from aligned words via require("cadence.captions").from_lines / from_words
-- (opts.mode: word | phrase | rolling | line, opts.window for phrase size).
function Scene:captions(p)
  p.size = p.size or 36
  p.text = p.text or ""
  if p.cues then p.cues = require("cadence.captions").normalize(p.cues) end
  return add(self, "text", p)
end
-- Seek-safe skeletal pose. skeleton = Spine-like table; animation name; t drives keys.
function Scene:spine(p)
  assert(p.skeleton or p.src, "cadence: spine{skeleton= or src=} required")
  p.w = p.w or 240
  p.h = p.h or 320
  p.animation = p.animation or "default"
  return add(self, "spine", p)
end
-- Frame-addressed Rive (.riv). Same clock as Lottie: set time from t, no update(dt).
function Scene:rive(p)
  assert(type(p.src) == "string", "cadence: rive{src=} required")
  assert(p.w and p.h, "cadence: rive{w=,h=} required")
  return add(self, "rive", p)
end
-- Shared plane camera. Perspective nodes set `camera = cam` and keep their own dolly.
function Scene:camera(p)
  p.yaw = p.yaw or 0
  p.pitch = p.pitch or 0
  p.roll = p.roll or 0
  p.dolly = p.dolly or 1
  p.fov = p.fov or 0.62
  p.aperture = p.aperture or 0
  return add(self, "camera", p)
end
-- Real 3D layer: offscreen RGBA, same contract as s:lottie. Children are mesh/light.
-- Pose is written from t; the native crate rasterizes one frame. No engine loop.
function Scene:world(p)
  assert(p.w and p.h, "cadence: world{w=,h=} required")
  p.fov = p.fov or 0.7
  p.cam_x = p.cam_x or 0
  p.cam_y = p.cam_y or 0.35
  p.cam_z = p.cam_z or 3.2
  p.look_x = p.look_x or 0
  p.look_y = p.look_y or 0
  p.look_z = p.look_z or 0
  p.yaw = p.yaw or 0
  p.pitch = p.pitch or 0
  p.roll = p.roll or 0
  p.near = p.near or 0.05
  p.far = p.far or 80
  return add(self, "world", p)
end
-- glTF instance or unit primitive, parented to a world. Transforms are f(t).
function Scene:mesh(p)
  assert(p.parent and p.parent.kind == "world", "cadence: mesh{parent=world} required")
  assert(p.src or p.primitive, "cadence: mesh{src= or primitive=} required")
  p.primitive = p.primitive or (p.src and "gltf" or "cube")
  p.x = p.x or 0
  p.y = p.y or 0
  p.z = p.z or 0
  p.yaw = p.yaw or 0
  p.pitch = p.pitch or 0
  p.roll = p.roll or 0
  p.scale = p.scale or 1
  return add(self, "mesh", p)
end
function Scene:light(p)
  assert(p.parent and p.parent.kind == "world", "cadence: light{parent=world} required")
  p.dir = p.dir or { 0.4, -1, 0.25 }
  p.intensity = p.intensity or 1
  p.color = p.color or "#ffffff"
  return add(self, "light", p)
end
-- Image or rasterized text as a grid mesh, displaced by a vertex shader.
-- amp/freq are animatable; time comes from t. Seek-safe.
function Scene:displace(p)
  assert(p.src or p.text, "cadence: displace{src= or text=} required")
  assert(p.w and p.h, "cadence: displace{w=,h=} required")
  p.cols = p.cols or 24
  p.rows = p.rows or 16
  p.amp = p.amp or 14
  p.freq = p.freq or 1
  return add(self, "displace", p)
end
-- Escape hatch: pure custom draw fn(t, g) where g is the host graphics API.
function Scene:draw(fn) return add(self, "draw", { fn = fn }) end
function Scene:script(fn) self.scripts[#self.scripts + 1] = fn end

-- ---------- script recorder ----------
local Rec = {}
Rec.__index = Rec

function Rec:wait(d)
  assert(type(d) == "number" and d >= 0, "cadence: wait(seconds >= 0)")
  self.cursor = self.cursor + d
end

-- Relational cursor jump: jumps or waits until a target event occurs.
-- `target` can be:
--   - number: absolute timestamp in seconds
--   - Node: jumps to node.initial.at or node completion
--   - table/event: e.g. node:word("key"), { node = n, event = "beat", nth = 1 }, or conceptual cut
function Rec:at(target)
  local t
  if type(target) == "number" then
    t = target
  elseif type(target) == "table" and target.kind and target.initial then
    t = target.initial.at or 0
  elseif type(target) == "table" and target.t0 ~= nil then
    t = target.t0
  elseif type(target) == "table" and target.at ~= nil then
    t = target.at
  else
    error("cadence: at() expects number timestamp, node, or relational event table", 2)
  end
  assert(t >= 0, "cadence: cannot jump to negative time")
  self.cursor = t
end

local function resolve_from(self, node, prop)
  local sim = self.sim[node]
  if sim and sim[prop] ~= nil then return sim[prop] end
  local v = node.initial[prop]
  if v == nil then
    error(("cadence: tween of %s.%s but node has no initial value for it"):format(node.id, prop), 0)
  end
  return v
end

local function record(self, node, dur, props, easename)
  local t0, t1 = self.cursor, self.cursor + dur
  for prop, target in pairs(props) do
    if not ANIMATABLE[prop] then
      error(("cadence: %q is not animatable"):format(prop), 0)
    end
    local to = target
    if prop == "color" then to = color.parse(target) end
    local from = resolve_from(self, node, prop)
    self.timeline:record(node, prop, t0, t1, from, to, easename)
    self.sim[node] = self.sim[node] or {}
    self.sim[node][prop] = to
  end
  self.cursor = t1
end

function Rec:tween(node, dur, props, easename)
  assert(type(dur) == "number" and dur > 0, "cadence: tween duration must be > 0")
  record(self, node, dur, props, easename)
end

-- Structural (non-animatable) props that a `set` may switch instantly at a
-- point in time — text swaps, source swaps. Recorded as step segments.
local SETTABLE = { text = true, src = true, font = true }

function Rec:set(node, props)
  local anim, struct = {}, nil
  for k, v in pairs(props) do
    if SETTABLE[k] and not ANIMATABLE[k] then
      struct = struct or {}
      struct[k] = v
    else
      anim[k] = v
    end
  end
  if struct then
    for prop, val in pairs(struct) do
      self.timeline:record_step(node, prop, self.cursor, val)
    end
  end
  if next(anim) then record(self, node, 0, anim, "linear") end
end

-- Organic drift on one prop: value = base + noise(t·freq)·amp. Does NOT advance
-- the cursor (runs alongside whatever follows). seed defaults per-node so
-- staggered wiggles don't move in lockstep.
function Rec:wiggle(node, prop, opts)
  assert(ANIMATABLE[prop], "cadence: wiggle prop not animatable: " .. tostring(prop))
  local duration = assert(opts.duration, "cadence: wiggle{duration=} required")
  local base = resolve_from(self, node, prop)
  self.timeline:record_wiggle(node, prop, self.cursor, self.cursor + duration,
    base, opts.amp or 10, opts.freq or 1.5, opts.seed or (#node.id * 7.31), opts.ramp or 0.25)
end

-- Motion along a smooth path through waypoints (Catmull-Rom, constant-speed).
-- Starts from the node's current position; updates sim so later tweens chain.
function Rec:path(node, dur, points, easename)
  assert(type(points) == "table" and #points >= 1, "cadence: path needs waypoints {{x,y},...}")
  local sx = resolve_from(self, node, "x")
  local sy = resolve_from(self, node, "y")
  local P = { { sx, sy } }
  for _, p in ipairs(points) do P[#P + 1] = { p[1] or p.x, p[2] or p.y } end
  -- Catmull-Rom sampling, 24 samples/segment, cumulative arc length
  local pts, total = { { x = sx, y = sy, len = 0 } }, 0
  local function cr(p0, p1, p2, p3, u)
    local u2, u3 = u * u, u * u * u
    return 0.5 * ((2 * p1) + (-p0 + p2) * u + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u2
      + (-p0 + 3 * p1 - 3 * p2 + p3) * u3)
  end
  for i = 1, #P - 1 do
    local p0 = P[math.max(i - 1, 1)]
    local p1, p2 = P[i], P[i + 1]
    local p3 = P[math.min(i + 2, #P)]
    for s = 1, 24 do
      local u = s / 24
      local x = cr(p0[1], p1[1], p2[1], p3[1], u)
      local y = cr(p0[2], p1[2], p2[2], p3[2], u)
      local prev = pts[#pts]
      total = total + math.sqrt((x - prev.x) ^ 2 + (y - prev.y) ^ 2)
      pts[#pts + 1] = { x = x, y = y, len = total }
    end
  end
  self.timeline:record_path(node, self.cursor, self.cursor + dur,
    { pts = pts, total = total }, easename)
  self.sim[node] = self.sim[node] or {}
  self.sim[node].x = P[#P][1]
  self.sim[node].y = P[#P][2]
  self.cursor = self.cursor + dur
end

-- GSAP-style stagger: same tween across many nodes, each offset by `each`.
-- from = "start" (default) | "end" | "center" changes the launch order.
function Rec:stagger(nodes, dur, props, opts)
  opts = opts or {}
  local each = opts.each or 0.06
  local n = #nodes
  local t0 = self.cursor
  self.timeline.staggers = self.timeline.staggers or {}
  self.timeline.staggers[#self.timeline.staggers + 1] =
    { t0 = t0, n = n, each = each, span = each * math.max(0, n - 1) }
  local t_end = t0
  for idx, node in ipairs(nodes) do
    local k = idx - 1
    if opts.from == "end" then
      k = n - idx
    elseif opts.from == "center" then
      k = math.abs(idx - (n + 1) / 2)
    end
    self.cursor = t0 + k * each
    local p = type(props) == "function" and props(node, idx) or props
    record(self, node, dur, p, opts.ease)
    if self.cursor > t_end then t_end = self.cursor end
  end
  self.cursor = t_end
end

function Rec:parallel(...)
  local fns = { ... }
  local t0 = self.cursor
  local t_end = t0
  for _, fn in ipairs(fns) do
    self.cursor = t0
    fn()
    if self.cursor > t_end then t_end = self.cursor end
  end
  self.cursor = t_end
end

-- Compile-phase Box2D: colliding bodies sampled onto x/y/rotation. Render is
-- ordinary nodes. Host fills the bake in post_script (needs love.physics).
function Rec:drop(nodes, opts)
  assert(type(nodes) == "table" and #nodes > 0, "cadence: drop needs a node list")
  opts = opts or {}
  local t0 = self.cursor
  local dur = opts.duration or 1.4
  self.timeline.drops = self.timeline.drops or {}
  self.timeline.drops[#self.timeline.drops + 1] = {
    nodes = nodes, t0 = t0, t1 = t0 + dur, opts = opts,
  }
  self.cursor = t0 + dur
end

-- Audio-reactive: bake a 0..1 curve onto a prop. source is an audio node with
-- follow=true (resolve fills initial.energy) or a raw samples table.
-- Does NOT advance the cursor (runs alongside). duration defaults to source clip.
function Rec:follow(node, prop, source, opts)
  assert(ANIMATABLE[prop], "cadence: follow prop not animatable: " .. tostring(prop))
  opts = opts or {}
  local samples
  if type(source) == "table" and source.kind then
    samples = source.initial.energy or source.energy
    assert(samples and #samples >= 2,
      "cadence: follow needs audio{ follow=true } (energy baked at resolve)")
  else
    samples = source
  end
  assert(type(samples) == "table" and #samples >= 2, "cadence: follow needs samples")
  local gain = opts.gain or 1
  local base = opts.base
  if base == nil then base = resolve_from(self, node, prop) end
  local scaled = {}
  for i, v in ipairs(samples) do scaled[i] = base + v * gain end
  local t0 = self.cursor
  local dur = opts.duration
  if not dur then
    if type(source) == "table" and source.kind then
      dur = source.initial.duration or (#samples / 50)
    else
      dur = #samples / 50
    end
  end
  self.timeline:record_bake(node, prop, t0, t0 + dur, scaled)
  self.sim[node] = self.sim[node] or {}
  self.sim[node][prop] = scaled[#scaled]
end

-- ---------- comp ----------
local Comp = {}
Comp.__index = Comp

function M.comp(def)
  assert(type(def) == "table", "cadence: comp{} takes a table")
  for _, k in ipairs({ "width", "height", "duration" }) do
    assert(type(def[k]) == "number" and def[k] > 0, "cadence: comp." .. k .. " required (number > 0)")
  end
  assert(type(def.scene) == "function", "cadence: comp.scene required (function)")
  if def.inputs ~= nil then
    assert(type(def.inputs) == "table", "cadence: comp.inputs must be a table")
    for name, spec in pairs(def.inputs) do
      assert(type(name) == "string", "cadence: input name must be a string")
      assert(type(spec) == "table", ('cadence: input "%s" must be a table'):format(name))
      assert(type(spec.kind) == "string", ('cadence: input "%s" needs kind'):format(name))
      if spec.default ~= nil then
        assert(type(spec.default) == "string",
          ('cadence: input "%s" default must be a string'):format(name))
      end
    end
  end
  return setmetatable({
    width = def.width, height = def.height,
    duration = def.duration, fps = def.fps or 30,
    background = color.parse(def.background or "#000000"),
    brand = def.brand,           -- optional path to a captured brand.json
    lint_allow = def.lint_allow, -- comp-wide lint escape hatch
    color_space = def.color_space, -- "oklab" (default) | "hsluv" | "okhsl" | "rgb"
    inputs = def.inputs,         -- optional { name = { kind, default? } }
    _scene_fn = def.scene,
  }, Comp)
end

-- Compile pass: bind s.input, build scene, execute scripts once, produce
-- timeline. Pure — the host injects a string-path table as `inputs_map`
-- (lib/ellua never reads disk). hooks.post_scene runs AFTER the scene is
-- built but BEFORE scripts record (layout solving writes x/y there, so tweens
-- see final positions).
-- Merge: def.inputs defaults < inputs_map (host already merged sibling JSON,
-- --inputs file, and --input flags).
function Comp:compile(hooks, inputs_map)
  local bound = {}
  if self.inputs then
    for name, spec in pairs(self.inputs) do
      if spec.default then bound[name] = spec.default end
    end
  end
  if inputs_map then
    for name, path in pairs(inputs_map) do
      if type(path) == "string" then bound[name] = path end
    end
  end
  if self.inputs then
    for name, _ in pairs(self.inputs) do
      if bound[name] == nil then
        error(('cadence: input "%s" is not bound'):format(name), 0)
      end
    end
  end
  local s = setmetatable({ nodes = {}, scripts = {}, count = 0, input = bound }, Scene)
  self._scene_fn(s)
  for _, n in ipairs(s.nodes) do
    if n.kind == "flex" then
      local i = n.initial
      i.x, i.y = i.x or 0, i.y or 0
      i.w, i.h = i.w or self.width, i.h or self.height
    end
    if n.kind == "audio" or n.kind == "tts" or n.kind == "sfx" or n.kind == "music" then
      local i = n.initial
      i.at = i.at or 0
      i.media_start = i.media_start or 0
      i.volume = i.volume or 1
      i.fade_in = i.fade_in or 0
      i.fade_out = i.fade_out or 0
      -- narration is the voice bus by default: it is what everything else ducks
      -- under. Opt out with bus = false on a tts node.
      if n.kind == "tts" and i.bus == nil then i.bus = "vo" end
      i.opacity = 0 -- never drawn
    end
    if n.kind == "vector" or n.kind == "html" or n.kind == "group" or n.kind == "fx"
      or n.kind == "particles" or n.kind == "chart" or n.kind == "ornament"
      or n.kind == "spine" or n.kind == "rive" or n.kind == "world"
      or n.kind == "camera" or n.kind == "mesh" or n.kind == "light" then
      local i = n.initial
      i.x, i.y = i.x or 0, i.y or 0
      if n.kind == "html" then i.progress = i.progress or 0 end
    end
    if self.color_space and n.initial.color_space == nil then
      n.initial.color_space = self.color_space
    end
    if n.kind == "video" or n.kind == "image" or n.kind == "lottie" or n.kind == "svg"
      or n.kind == "spritesheet" or n.kind == "displace" or n.kind == "rive"
      or n.kind == "page" then
      local i = n.initial
      i.x, i.y = i.x or 0, i.y or 0
      i.w, i.h = i.w or self.width, i.h or self.height
      if n.kind == "video" or n.kind == "lottie" then
        i.from = i.from or 0
        i.duration = i.duration or (self.duration - i.from)
        i.media_start = i.media_start or 0
      end
      if n.kind == "lottie" then
        i.loop = i.loop ~= false
      end
    end
  end
  if hooks and hooks.post_scene then hooks.post_scene(s.nodes, self) end
  local tl = Timeline.new()
  local rec = setmetatable({ cursor = 0, timeline = tl, sim = {} }, Rec)
  for _, fn in ipairs(s.scripts) do fn(rec) end
  if rec.cursor > self.duration + 1e-9 then
    error(("cadence: script runs to %.3fs but comp.duration is %.3fs (duration is static)")
      :format(rec.cursor, self.duration), 0)
  end
  self.nodes, self.timeline = s.nodes, tl
  if hooks and hooks.post_script then hooks.post_script(self, rec) end
  for _, n in ipairs(s.nodes) do
    if n.initial.cues then
      -- A cue's t1 only means something if something clears the text at it. Recording
      -- the start alone makes a cue hold until the next one begins, and the last cue
      -- hold to the end of the comp -- so a readout from one chapter stayed on screen
      -- through the next. Contiguous cues are unaffected: the clear is only recorded
      -- when a real gap follows, so nothing that already abutted changes.
      local cues = n.initial.cues
      for i, cue in ipairs(cues) do
        tl:record_step(n, "text", cue.t0, cue.text)
        local nxt = cues[i + 1]
        if cue.t1 and cue.t1 > cue.t0 and (not nxt or nxt.t0 > cue.t1 + 1e-6) then
          tl:record_step(n, "text", cue.t1, "")
        end
      end
    end
  end
  return self
end

function Comp:evaluate(t)
  self.timeline:evaluate(t)
end

function M.palette(opts)
  return color.palette(opts)
end

package.loaded["cadence"] = M
package.loaded["ellua"] = M

return M
