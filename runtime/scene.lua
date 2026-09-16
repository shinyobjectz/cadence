-- cadence-scene bridge: the single vello rasterizer under the timeline.
-- Enabled with CADENCE_SCENE=1. Nodes the scene crate can paint are streamed
-- into one f32 command list (+ string table) instead of love.graphics calls;
-- the painter flushes the list into one premultiplied RGBA layer whenever a
-- node the crate cannot paint yet comes next in z-order, and at frame end.
local ffi = require("ffi")

ffi.cdef([[
int cs_font_load(const char *path);
int cs_text_measure(int font, float size, const char *text, float ls, float wrap,
                    float leading, float *out);
int cs_image_register(const uint8_t *rgba, uint16_t w, uint16_t h);
int cs_image_update(int slot, const uint8_t *rgba, uint16_t w, uint16_t h);
int cs_render(const float *cmds, size_t len, const uint8_t *strings, size_t strings_len,
              uint16_t w, uint16_t h, uint16_t threads, uint8_t *out, size_t out_len);
]])

local S = { available = false, enabled = false }
S.threads = tonumber(os.getenv("CADENCE_SCENE_THREADS") or "0") or 0

local lib
local ok = pcall(function()
  lib = ffi.load(require("native").lib("cadence_scene"))
end)
if ok and lib then S.available = true end
local want = os.getenv("CADENCE_SCENE")
S.enabled = S.available and want ~= nil and want ~= "" and want ~= "0"
if want and want ~= "0" and not S.available then
  io.stderr:write("cadence: CADENCE_SCENE set but scene dylib not found (build scene/)\n")
end

-- fonts: path -> id (0 = bundled NotoSans, matching love's default)
local font_ids = {}
function S.font_id(path)
  if not path then return 0 end
  local id = font_ids[path]
  if id then return id end
  local p = path:match("^/") and path
    or ((os.getenv("CADENCE_CWD") or os.getenv("ELLUA_CWD") or ".") .. "/" .. path)
  id = lib.cs_font_load(p)
  assert(id >= 0, "cadence-scene: font not found: " .. path)
  font_ids[path] = id
  return id
end

local measure_cache = {}
local mbuf = ffi.new("float[2]")
function S.measure(font, size, text, ls, wrap, leading)
  local key = table.concat({ font, size, text, ls or 0, wrap or 0, leading or 0 }, "\1")
  local m = measure_cache[key]
  if m then return m[1], m[2] end
  assert(lib.cs_text_measure(font, size, text, ls or 0, wrap or 0, leading or 0, mbuf) == 0,
    "cadence-scene: measure failed")
  m = { mbuf[0], mbuf[1] }
  measure_cache[key] = m
  return m[1], m[2]
end

-- builder: growable f32 command buffer + byte string table, reused per frame
local Builder = {}
Builder.__index = Builder

function S.new_builder()
  return setmetatable({
    cap = 4096, len = 0, buf = ffi.new("float[?]", 4096),
    scap = 4096, slen = 0, sbuf = ffi.new("uint8_t[?]", 4096),
    pending = false,
  }, Builder)
end

function Builder:reset() self.len, self.slen, self.pending = 0, 0, false end

local function push(b, ...)
  local n = select("#", ...)
  if b.len + n > b.cap then
    local ncap = b.cap * 2
    while b.len + n > ncap do ncap = ncap * 2 end
    local nbuf = ffi.new("float[?]", ncap)
    ffi.copy(nbuf, b.buf, b.len * 4)
    b.buf, b.cap = nbuf, ncap
  end
  for i = 1, n do
    b.buf[b.len] = select(i, ...)
    b.len = b.len + 1
  end
end

function Builder:str(s)
  local n = #s
  if self.slen + n > self.scap then
    local ncap = self.scap * 2
    while self.slen + n > ncap do ncap = ncap * 2 end
    local nbuf = ffi.new("uint8_t[?]", ncap)
    ffi.copy(nbuf, self.sbuf, self.slen)
    self.sbuf, self.scap = nbuf, ncap
  end
  local off = self.slen
  ffi.copy(self.sbuf + off, s, n)
  self.slen = self.slen + n
  return off, n
end

local function col(c)
  if type(c) == "string" then c = require("ellua.color").parse(c) end
  c = c or { 1, 1, 1, 1 }
  return c[1], c[2], c[3], c[4] or 1
end

-- absolute affine [a b c d e f]: x' = a*x + c*y + e ; y' = b*x + d*y + f
function Builder:transform(m) push(self, 100, m[1], m[2], m[3], m[4], m[5], m[6]); self.pending = true end

-- runs: array of { text=, color=, bold=, italic= }; opts: ls, wrap, leading, align(0/1/2),
-- outline (px), outline_color, embolden (px), opacity
function Builder:text(font, size, x, y, runs, opts)
  opts = opts or {}
  local op = opts.opacity or 1
  local orr, og, ob, oa = col(opts.outline_color or { 0, 0, 0, 1 })
  push(self, 101, font, size, x, y, opts.ls or 0, opts.wrap or 0, opts.leading or 0,
    opts.align or 0, opts.outline or 0, orr, og, ob, oa * op, opts.embolden or 0, #runs)
  for _, r in ipairs(runs) do
    local off, n = self:str(r.text)
    local cr, cg, cb, ca = col(r.color)
    push(self, off, n, cr, cg, cb, ca * op, r.bold and 1 or 0, r.italic and 1 or 0)
  end
  self.pending = true
end

-- vector-compatible primitives (same opcodes as ellua-vector)
function Builder:rect(x, y, w, h, c, rad)
  if rad and rad > 0 then push(self, 1, x, y, w, h, rad, col(c)) else push(self, 0, x, y, w, h, col(c)) end
  self.pending = true
end
function Builder:circle(cx, cy, r, c) push(self, 2, cx, cy, r, col(c)); self.pending = true end
function Builder:clear(c) push(self, 102, col(c)); self.pending = true end
function Builder:image(id, x, y, w, h, rx, alpha) push(self, 103, id, x, y, w, h, rx or 0, alpha or 1); self.pending = true end
function Builder:clip_push(x, y, w, h, rx) push(self, 104, x, y, w, h, rx or 0) end
function Builder:clip_pop() push(self, 105) end
function Builder:pop() push(self, 105) end
local BLEND = { alpha = { 0, 0 }, multiply = { 1, 0 }, screen = { 2, 0 }, darken = { 4, 0 }, lighten = { 5, 0 }, add = { 0, 1 } }
S.BLEND = BLEND
function Builder:blend_push(mode) local b = BLEND[mode] or BLEND.alpha; push(self, 107, b[1], b[2]) end
local FILTER = { effect_blur = 0, effect_brightness = 1, effect_contrast = 2, effect_saturate = 3, effect_grayscale = 4,
  effect_sepia = 5, effect_invert = 6, effect_opacity = 7, effect_hue_rotate = 8 }
S.FILTER = FILTER
function Builder:filter_push(kind, amount) push(self, 108, kind, amount) end
function Builder:opacity_push(a) push(self, 110, a) end
-- vector-node builder surface (same verbs as runtime/vector.lua)
function Builder:move(x, y) push(self, 3, x, y) end
function Builder:line(x, y) push(self, 4, x, y) end
function Builder:curve(x1, y1, x2, y2, x, y) push(self, 5, x1, y1, x2, y2, x, y) end
function Builder:fill(c) push(self, 6, col(c)); self.pending = true end
function Builder:stroke(width, c) push(self, 7, width, col(c)); self.pending = true end
function Builder:polyline(pts, width, c)
  if #pts < 4 then return end
  self:move(pts[1], pts[2])
  for i = 3, #pts, 2 do self:line(pts[i], pts[i + 1]) end
  self:stroke(width or 2, c)
end
function Builder:gradient(x, y, w, h, x0, y0, x1, y1, c0, c1)
  push(self, 8, x, y, w, h, x0, y0, x1, y1); push(self, col(c0)); push(self, col(c1)); self.pending = true
end
function Builder:radial(cx, cy, r, c0, c1) push(self, 9, cx, cy, r); push(self, col(c0)); push(self, col(c1)); self.pending = true end
-- grain is scoped to the current vector node's box (set by the painter)
function Builder:grain(amount, seed)
  local b = self.grain_box
  if b then push(self, 106, amount, seed or 1, b[1], b[2], b[3], b[4]) end
end

-- dynamic images (html textures, frames): one slot per node, updated on change
function S.image_slot(node, imagedata, changed)
  local w, h = imagedata:getDimensions()
  local ptr = ffi.cast("const uint8_t*", imagedata:getFFIPointer())
  if node._scene_slot == nil then
    node._scene_slot = lib.cs_image_register(ptr, w, h)
    assert(node._scene_slot >= 0, "cadence-scene: image register failed")
  elseif changed then
    assert(lib.cs_image_update(node._scene_slot, ptr, w, h) == 0, "cadence-scene: image update failed")
  end
  return node._scene_slot
end

-- images: registered once per file path from a love ImageData
local image_ids = {}
function S.image_id(path, imagedata)
  local id = image_ids[path]
  if id then return id end
  local w, h = imagedata:getDimensions()
  id = lib.cs_image_register(ffi.cast("const uint8_t*", imagedata:getFFIPointer()), w, h)
  assert(id >= 0, "cadence-scene: image register failed: " .. path)
  image_ids[path] = id
  return id
end

function S.render(b, w, h, out_ptr, out_len)
  local rc = lib.cs_render(b.buf, b.len, b.sbuf, b.slen, w, h, S.threads,
    ffi.cast("uint8_t*", out_ptr), out_len)
  if rc ~= 0 then error("cadence-scene: render failed") end
end

return S
