-- P0 spike: prove offscreen render, ffmpeg pipe, determinism, out-of-order seek.
-- Modes (env):
--   MODE=hash            print "FRAME <i> <md5>" per frame (S3/S4/S5)
--   MODE=render          pipe raw RGBA to ffmpeg -> out.mp4 (S1/S2)
--   ORDER=shuffled       render frames out of order (S4); hashes still tagged by i

local W, H, FPS, N = 1280, 720, 30, 90

-- comp: pure function of t. No clocks, no RNG, no I/O.
local function draw(t)
  love.graphics.clear(0.08, 0.09, 0.12, 1)
  -- orbiting circle
  local cx = W / 2 + math.cos(t * 2 * math.pi * 0.5) * 300
  local cy = H / 2 + math.sin(t * 2 * math.pi * 0.5) * 180
  love.graphics.setColor(0.95, 0.55, 0.20, 1)
  love.graphics.circle("fill", cx, cy, 48)
  -- sliding bar
  love.graphics.setColor(0.25, 0.65, 0.95, 1)
  love.graphics.rectangle("fill", (t / 3) * W - 100, H - 120, 200, 60, 12, 12)
  -- scaling square
  local s = 60 + 40 * math.sin(t * 2 * math.pi)
  love.graphics.setColor(0.55, 0.90, 0.45, 1)
  love.graphics.rectangle("fill", 120 - s / 2, 160 - s / 2, s, s)
  love.graphics.setColor(1, 1, 1, 1)
  love.graphics.print(string.format("ellua p0  t=%.3f", t), 24, 24)
end

function love.run()
  local mode = os.getenv("MODE") or "hash"
  local order = os.getenv("ORDER") or "sequential"

  local canvas = love.graphics.newCanvas(W, H)
  local frames = {}
  for i = 0, N - 1 do frames[#frames + 1] = i end
  if order == "shuffled" then
    frames = {}
    for i = 60, N - 1 do frames[#frames + 1] = i end
    for i = 0, 29 do frames[#frames + 1] = i end
    for i = 30, 59 do frames[#frames + 1] = i end
  end

  local pipe
  if mode == "render" then
    local cmd = string.format(
      "ffmpeg -hide_banner -loglevel error -y -f rawvideo -pixel_format rgba" ..
      " -video_size %dx%d -framerate %d -i - -c:v libx264 -pix_fmt yuv420p" ..
      " -crf 18 -colorspace bt709 %s/out.mp4",
      W, H, FPS, love.filesystem.getSourceBaseDirectory() .. "/p0")
    pipe = io.popen(cmd, "w")
    assert(pipe, "ffmpeg pipe failed")
  end

  local t0 = love.timer.getTime()
  for _, i in ipairs(frames) do
    local t = i / FPS -- fixed dt: time derived from frame index only
    love.graphics.setCanvas(canvas)
    draw(t)
    love.graphics.setCanvas()
    local img = canvas:newImageData()
    local raw = img:getString()
    if mode == "hash" then
      local md5 = love.data.encode("string", "hex", love.data.hash("md5", raw))
      io.write(string.format("FRAME %d %s\n", i, md5))
    elseif pipe then
      pipe:write(raw)
    end
    img:release()
  end
  local dt = love.timer.getTime() - t0

  if pipe then pipe:close() end
  io.write(string.format("DONE frames=%d wall=%.2fs fps=%.1f mode=%s order=%s\n",
    N, dt, N / dt, mode, order))
  io.flush()
  return function() return 0 end
end
