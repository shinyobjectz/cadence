-- ThorVG Lottie bridge (LuaJIT FFI, no build step — brew libthorvg).
-- Frame-addressed: tvg_animation_set_frame(t → frame) = seek-safe by construction.
-- Output buffer is ABGR8888 premultiplied == LÖVE rgba8 bytes; draw with
-- blend mode ("alpha", "premultiplied").
local ffi = require("ffi")

ffi.cdef([[
typedef void* Tvg_Canvas;
typedef void* Tvg_Paint;
typedef void* Tvg_Animation;
int tvg_engine_init(unsigned threads);
Tvg_Canvas tvg_swcanvas_create(int op);
int tvg_swcanvas_set_target(Tvg_Canvas canvas, uint32_t* buffer, uint32_t stride,
                            uint32_t w, uint32_t h, int cs);
int tvg_canvas_add(Tvg_Canvas canvas, Tvg_Paint paint);
int tvg_canvas_update(Tvg_Canvas canvas);
int tvg_canvas_draw(Tvg_Canvas canvas, bool clear);
int tvg_canvas_sync(Tvg_Canvas canvas);
int tvg_canvas_destroy(Tvg_Canvas canvas);
Tvg_Animation tvg_animation_new(void);
int tvg_animation_del(Tvg_Animation animation);
Tvg_Paint tvg_animation_get_picture(Tvg_Animation animation);
int tvg_animation_set_frame(Tvg_Animation animation, float no);
int tvg_animation_get_total_frame(Tvg_Animation animation, float* cnt);
int tvg_animation_get_duration(Tvg_Animation animation, float* duration);
int tvg_picture_load(Tvg_Paint picture, const char* path);
int tvg_picture_set_size(Tvg_Paint picture, float w, float h);
]])

local TVG_ENGINE_OPTION_DEFAULT = 1
local TVG_COLORSPACE_ABGR8888 = 0

local L = { available = false }

local lib
local ok = pcall(function()
  lib = ffi.load("/opt/homebrew/lib/libthorvg-1.dylib")
  if lib.tvg_engine_init(2) ~= 0 then error("engine init") end
end)
if ok and lib then L.available = true end

-- returns instance { set_time(t), imgdata-compatible buffer ptr owner side }
function L.open(path, w, h, imgdata_ptr)
  local anim = lib.tvg_animation_new()
  assert(anim ~= nil, "ellua-lottie: animation_new failed")
  local pic = lib.tvg_animation_get_picture(anim)
  if lib.tvg_picture_load(pic, path) ~= 0 then
    lib.tvg_animation_del(anim)
    error("ellua-lottie: cannot load " .. path)
  end
  lib.tvg_picture_set_size(pic, w, h)

  local canvas = lib.tvg_swcanvas_create(TVG_ENGINE_OPTION_DEFAULT)
  assert(canvas ~= nil, "ellua-lottie: swcanvas failed")
  assert(lib.tvg_swcanvas_set_target(canvas, ffi.cast("uint32_t*", imgdata_ptr),
    w, w, h, TVG_COLORSPACE_ABGR8888) == 0, "ellua-lottie: set_target failed")
  assert(lib.tvg_canvas_add(canvas, pic) == 0, "ellua-lottie: canvas_add failed")

  local total = ffi.new("float[1]")
  local dur = ffi.new("float[1]")
  lib.tvg_animation_get_total_frame(anim, total)
  lib.tvg_animation_get_duration(anim, dur)

  return {
    anim = anim,
    canvas = canvas,
    total_frames = total[0],
    duration = dur[0] > 0 and dur[0] or 1,
    -- render animation state at local time tl (seconds) into the target buffer
    render = function(self, tl, loop)
      local d = self.duration
      if loop then tl = tl % d else tl = math.min(math.max(tl, 0), d) end
      local frame = math.min(tl / d * self.total_frames, self.total_frames - 0.001)
      lib.tvg_animation_set_frame(self.anim, frame)
      lib.tvg_canvas_update(self.canvas)
      lib.tvg_canvas_draw(self.canvas, true)
      lib.tvg_canvas_sync(self.canvas)
    end,
    close = function(self)
      lib.tvg_canvas_destroy(self.canvas)
    end,
  }
end

return L
