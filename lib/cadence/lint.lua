-- Tier-1 static analyzer: rules over the compiled timeline + scene graph.
-- Pure Lua, no rendering — frame-grid evaluation of the timeline (the same
-- pure seek the renderer uses) plus segment metadata. See CHECKS.md.
--
-- lint.run(comp, opts) -> findings[]
--   opts.measure_text = function(text, size, font) -> w, h   (host-provided)
--   opts.brand = { palette = {"#hex", ...}, fonts = true }   (optional)
--   opts.fps = sampling rate (default 30)
--
-- finding = { code, severity ("error"|"warn"|"info"), node, t0, t1,
--             measured, threshold, detail, suggestion? }

local color = require("cadence.color")

local L = {}

local TH = {
  motion_density = 3.0,
  competing_amp = 0.4,
  ease_repeat = 2,
  overshoot_budget = 0.20,
  stagger_span = 0.5,
  flash_rate = 3,
  wall_span = 4.0,
  frozen_span = 2.0,
  frozen_error = 3.0,
  text_min = 30,
  safe_x = 80,
  safe_y = 100,
  contrast_normal = 4.5,
  contrast_large = 3.0,
  large_size = 48,
  palette_max = 8,
  brand_de = 0.12,
  duck_vol = 0.30,
  gap_span = 2.0,
  subpixel = 1.0,
}

local AUDIO_KINDS = { audio = true, tts = true, sfx = true, music = true }
local BOXED = { rect = true, html = true, page = true, image = true, svg = true, vector = true, lottie = true, video = true, spritesheet = true, displace = true, fx = true, surface = true, world = true }

local function rel_lum(c)
  local function lin(v) if v <= 0.03928 then return v / 12.92 end return ((v + 0.055) / 1.055) ^ 2.4 end
  return 0.2126 * lin(c[1]) + 0.7152 * lin(c[2]) + 0.0722 * lin(c[3])
end

local function contrast_ratio(a, b)
  local la, lb = rel_lum(a), rel_lum(b)
  if la < lb then la, lb = lb, la end
  return (la + 0.05) / (lb + 0.05)
end

local function oklab_dist(a, b)
  -- coarse perceptual distance via rgb -> approx: reuse color.lerp space by
  -- comparing midpoints; cheap proxy: euclidean in linearized rgb
  local d = 0
  for i = 1, 3 do d = d + (a[i] - b[i]) ^ 2 end
  return math.sqrt(d)
end

local function allowed(node, code, comp)
  local la = node and node.initial and node.initial.lint_allow
  if la then for _, c in ipairs(la) do if c == code then return true end end end
  if comp.lint_allow then
    for _, c in ipairs(comp.lint_allow) do if c == code then return true end end
  end
  return false
end

function L.run(comp, opts)
  opts = opts or {}
  local fps = opts.fps or 30
  local dur = comp.duration
  local n_samples = math.floor(dur * fps) + 1
  local findings = {}
  local function add(code, sev, node, t0, t1, measured, threshold, detail, suggestion)
    if allowed(node, code, comp) then return end
    findings[#findings + 1] = { code = code, severity = sev,
      node = node and node.id or nil, t0 = t0, t1 = t1,
      measured = measured, threshold = threshold, detail = detail,
      suggestion = suggestion }
  end

  -- classify nodes
  local visual, audio = {}, {}
  for _, n in ipairs(comp.nodes) do
    if AUDIO_KINDS[n.kind] then audio[#audio + 1] = n
    elseif n.kind ~= "flex" and n.kind ~= "kinetic" and n.kind ~= "draw" then
      visual[#visual + 1] = n
    end
    -- GLSL escape hatches: the pass is opaque to lint, like s:draw
    if n.kind == "fx" then
      for _, name in ipairs(n.initial.chain or {}) do
        if name == "worley" or name == "shadertoy" then
          add("fx_opaque", "info", n, 0, dur, nil, nil,
            "fx pass '" .. name .. "' is GLSL: lint cannot see what it does to the picture",
            "keep the beat readable without it, or express the look as bloom/glow/vignette/chroma/grain")
        end
      end
    end
  end

  -- text measurement cache
  local tdims = {}
  if opts.measure_text then
    for _, n in ipairs(visual) do
      if n.kind == "text" then
        local w, h = opts.measure_text(n.initial.text or "", n.initial.size or 32, n.initial.font)
        tdims[n] = { w = w, h = h, size0 = n.initial.size or 32 }
      end
    end
  end

  -- Recording builder: same verbs as runtime/scene.lua and runtime/vector.lua,
  -- but it only accumulates the numbers. Calling draw(v, t) with it at each
  -- sample makes the callback's motion measurable (diff of command streams).
  local Rec = {}
  Rec.__index = Rec
  local function rec_col(c)
    if type(c) == "string" then c = require("cadence.color").parse(c) end
    c = c or { 1, 1, 1, 1 }
    return c[1], c[2], c[3], c[4] or 1
  end
  -- one entry per verb: { verb, n1, n2, ... } so streams align by command,
  -- not by raw index (a draw-on that appends a point must not shift the rest)
  local function cmd(self, verb, ...)
    local e = { verb }
    for i = 1, select("#", ...) do
      local v = select(i, ...)
      if type(v) == "number" then e[#e + 1] = v end
    end
    self.n[#self.n + 1] = e
  end
  function Rec:reset() self.n = {} end
  function Rec:rect(x, y, w, h, c, rad) cmd(self, "rect", x, y, w, h, rad or 0, rec_col(c)) end
  function Rec:circle(cx, cy, r, c) cmd(self, "circle", cx, cy, r, rec_col(c)) end
  function Rec:move(x, y) cmd(self, "move", x, y) end
  function Rec:line(x, y) cmd(self, "line", x, y) end
  function Rec:curve(x1, y1, x2, y2, x, y) cmd(self, "curve", x1, y1, x2, y2, x, y) end
  function Rec:fill(c) cmd(self, "fill", rec_col(c)) end
  function Rec:stroke(w, c) cmd(self, "stroke", w, rec_col(c)) end
  function Rec:polyline(pts, w, c)
    for i = 1, #pts - 1, 2 do cmd(self, i == 1 and "move" or "line", pts[i], pts[i + 1]) end
    cmd(self, "stroke", w or 2, rec_col(c))
  end
  function Rec:gradient(x, y, w, h, x0, y0, x1, y1, c0, c1)
    local r0, g0, b0, a0 = rec_col(c0); cmd(self, "gradient", x, y, w, h, x0, y0, x1, y1, r0, g0, b0, a0, rec_col(c1))
  end
  function Rec:radial(cx, cy, r, c0, c1)
    local r0, g0, b0, a0 = rec_col(c0); cmd(self, "radial", cx, cy, r, r0, g0, b0, a0, rec_col(c1))
  end
  function Rec:grain(amount, seed) cmd(self, "grain", amount, seed or 1) end
  function Rec:image(id, x, y, w, h, rx, a) cmd(self, "image", id, x, y, w, h, rx or 0, a or 1) end
  function Rec:clip_push(x, y, w, h, rx) cmd(self, "clip", x, y, w, h, rx or 0) end
  function Rec:clip_pop() end
  function Rec:pop() end
  function Rec:transform(m) cmd(self, "transform", unpack(m)) end
  function Rec:clear(c) cmd(self, "clear", rec_col(c)) end
  local function record_draw(n, t)
    local fn = n.initial.draw
    if type(fn) ~= "function" then return nil end
    local b = setmetatable({ n = {} }, Rec)
    local ok = pcall(fn, b, t, n)
    if not ok then return nil end
    return b.n
  end
  -- amplitude between two recorded streams: mean per-command displacement over
  -- the node box (a box moving 7px reads like a rect node moving 7px), plus a
  -- structural term when the command sequence itself changes shape.
  local function stream_delta(p, q, box)
    if not p or not q then return 0 end
    local m = math.min(#p, #q)
    local peak, mismatch = 0, 0
    for i = 1, m do
      local a, b = p[i], q[i]
      if a[1] ~= b[1] then
        mismatch = mismatch + 1
      else
        local k = math.min(#a, #b)
        local acc = 0
        for j = 2, k do acc = acc + math.abs(b[j] - a[j]) end
        if k > 1 and acc / (k - 1) > peak then peak = acc / (k - 1) end
      end
    end
    -- the most-moved command sets the amplitude: "is anything moving, how fast"
    local d = peak / box
    local ln = math.max(#p, #q)
    if ln > 0 then d = d + ((math.abs(#p - #q) + mismatch) / ln) * 0.25 end
    if d > 1 then d = 1 end
    return d
  end

  -- ============ frame-grid sampling ============
  -- states[s][node] = {x,y,op,scale,size,w,h,r, visible, bx0,by0,bx1,by1}
  local states = {}
  for s = 0, n_samples - 1 do
    local t = s / fps
    comp.timeline:evaluate(t)
    local row = {}
    for _, n in ipairs(visual) do
      local i = n.initial
      local g = function(p) local v = n.state[p]; if v == nil then v = i[p] end; return v end
      local op = g("opacity") or 1
      local vis = op > 0.01
      if (n.kind == "video" or n.kind == "lottie") and vis then
        vis = t >= (i.from or 0) and t < (i.from or 0) + (i.duration or dur)
      end
      local sc = g("scale") or 1
      local x, y = g("x") or 0, g("y") or 0
      local rot = g("rotation") or 0
      local w, h
      if n.kind == "circle" then
        local r = (g("r") or 0) * sc
        w, h = r * 2, r * 2
        row[n] = { x = x, y = y, op = op, vis = vis, sc = sc, rot = rot,
          bx0 = x - r, by0 = y - r, bx1 = x + r, by1 = y + r }
      elseif n.kind == "text" then
        local size = g("size") or 32
        local td = tdims[n]
        local tw = td and (td.w * size / td.size0) or (#(i.text or "") * size * 0.55)
        local th = td and (td.h * size / td.size0) or size * 1.2
        tw, th = tw * sc, th * sc
        local ox = i.anchor == "center" and -tw / 2 or 0
        local oy = i.anchor == "center" and -th / 2 or 0
        row[n] = { x = x, y = y, op = op, vis = vis, sc = sc, size = size, rot = rot,
          bx0 = x + ox, by0 = y + oy, bx1 = x + ox + tw, by1 = y + oy + th }
      elseif BOXED[n.kind] then
        w, h = (g("w") or 0) * sc, (g("h") or 0) * sc
        local ox = i.anchor == "center" and -w / 2 or 0
        local oy = i.anchor == "center" and -h / 2 or 0
        row[n] = { x = x, y = y, op = op, vis = vis, sc = sc, w = w, h = h, rot = rot,
          bx0 = x + ox, by0 = y + oy, bx1 = x + ox + w, by1 = y + oy + h }
      else
        row[n] = { x = x, y = y, op = op, vis = vis, sc = sc, rot = rot }
      end
      local st = row[n]
      st.reveal = g("reveal")
      st.progress = g("progress")
      st.outline, st.weight, st.tracking = g("outline"), g("weight"), g("tracking")
      if n.kind == "vector" and vis then st.stream = record_draw(n, t) end
    end
    states[s] = row
  end

  -- normalized motion amplitude per sample step, aggregated by motion GROUP
  -- (kinetic chars = one unit; energy-style sqrt scaling within a group)
  local amp = {}     -- amp[s][group] normalized
  local raw_amp = {} -- raw per-node (frozen detection)
  for s = 1, n_samples - 1 do
    local by_group = {} -- group -> {sum, n}
    local raw = {}
    for _, n in ipairs(visual) do
      local p, q = states[s - 1][n], states[s][n]
      if p and q and (p.vis or q.vis) then
        local d = math.abs(q.x - p.x) / comp.width + math.abs(q.y - p.y) / comp.height
          + math.abs((q.sc or 1) - (p.sc or 1)) + math.abs(q.op - p.op)
          + math.abs((q.rot or 0) - (p.rot or 0)) / math.pi
          + (q.size and p.size and math.abs(q.size - p.size) / 100 or 0)
        -- reveal (type-on) is motion: glyphs typed this step × glyph width, over frame width
        if q.reveal and p.reveal and q.reveal ~= p.reveal then
          local nchars = #((n.initial.text or ""):gsub("{[^}]*}", ""))
          local gw = (q.size or n.initial.size or 32) * 0.55
          d = d + math.abs(q.reveal - p.reveal) * nchars * gw / comp.width
        end
        -- stroked type breathing (outline/weight) and tracking are motion too
        if q.outline and p.outline then d = d + math.abs(q.outline - p.outline) end
        if q.weight and p.weight then d = d + math.abs(q.weight - p.weight) end
        if q.tracking and p.tracking and q.tracking ~= p.tracking then
          local nchars = #((n.initial.text or ""):gsub("{[^}]*}", ""))
          d = d + math.abs(q.tracking - p.tracking) * nchars / comp.width
        end
        if q.progress and p.progress and q.progress ~= p.progress then
          d = d + math.abs(q.progress - p.progress) / 100
        end
        -- vector draw callbacks: diff of the recorded command streams
        if n.kind == "vector" and (q.stream or p.stream) then
          local box = math.max(n.initial.w or comp.width, n.initial.h or comp.height, 1)
          d = d + stream_delta(p.stream, q.stream, box)
        end
        raw[n] = d
        local key = n._group or n
        local e = by_group[key] or { sum = 0, n = 0 }
        e.sum = e.sum + d
        e.n = e.n + 1
        by_group[key] = e
      end
    end
    local a = {}
    for key, e in pairs(by_group) do
      a[key] = e.sum / math.sqrt(e.n)
    end
    amp[s] = a
    raw_amp[s] = raw
  end

  -- ============ motion rules ============
  local bucket = math.floor(fps / 2) -- 0.5s buckets
  local worst_density, worst_bt = 0, 0
  local rest_run, max_no_rest_start = 0, nil
  local no_rest_since = 0
  for b = 0, math.floor((n_samples - 2) / bucket) do
    local sum = 0
    local big = {}
    for s = b * bucket + 1, math.min((b + 1) * bucket, n_samples - 1) do
      for n, d in pairs(amp[s] or {}) do
        sum = sum + d
        big[n] = (big[n] or 0) + d
      end
    end
    local t0 = b * 0.5
    if sum > worst_density then worst_density, worst_bt = sum, t0 end
    if sum > TH.motion_density then
      add("motion_density", "warn", nil, t0, t0 + 0.5, sum, TH.motion_density,
        ("motion amplitude %.2f in bucket"):format(sum))
    end
    local nbig = 0
    for _, d in pairs(big) do if d > TH.competing_amp then nbig = nbig + 1 end end
    if nbig >= 3 then
      add("competing_beats", "warn", nil, t0, t0 + 0.5, nbig, 3,
        nbig .. " nodes with large simultaneous motion")
    elseif nbig == 2 then
      add("competing_beats", "info", nil, t0, t0 + 0.5, 2, 3,
        "two large motions share this beat")
    end
    -- wall of motion: track rest buckets (density < 0.5)
    if sum < 0.5 then
      no_rest_since = t0 + 0.5
    elseif t0 + 0.5 - no_rest_since > TH.wall_span then
      add("wall_of_motion", "info", nil, no_rest_since, t0 + 0.5,
        t0 + 0.5 - no_rest_since, TH.wall_span, "no rest beat in span")
      no_rest_since = t0 + 0.5 -- report once per span
    end
  end

  -- frozen spans: visible nodes but ~zero total motion
  local frozen_start = nil
  for s = 1, n_samples - 1 do
    local total, any_vis = 0, false
    for _, n in ipairs(visual) do
      local st = states[s][n]
      if st and st.vis then any_vis = true end
    end
    for _, d in pairs(amp[s] or {}) do total = total + d end
    local t = s / fps
    if any_vis and total < 0.002 then
      frozen_start = frozen_start or t
    else
      if frozen_start and t - frozen_start > TH.frozen_span then
        add("frozen_span", (t - frozen_start > TH.frozen_error) and "error" or "warn",
          nil, frozen_start, t, t - frozen_start, TH.frozen_span, "visible but static")
      end
      frozen_start = nil
    end
  end
  if frozen_start and dur - frozen_start > TH.frozen_span then
    add("frozen_span", (dur - frozen_start > TH.frozen_error) and "error" or "warn",
      nil, frozen_start, dur, dur - frozen_start, TH.frozen_span, "visible but static")
  end

  -- ============ segment-metadata rules ============
  local total_tweens, overshoot_tweens = 0, 0
  local first_motion = math.huge
  local ease_windows = {} -- list of {t0, ease_id}
  local sets = {}
  local overshoot_reported = {} -- dedupe stagger swarms: one finding per beat
  for _, g in ipairs(comp.timeline.order) do
    for _, seg in ipairs(g.segs) do
      if not seg.kind then -- plain tween/set
        if seg.t1 > seg.t0 then
          total_tweens = total_tweens + 1
          if seg.overshoot then
            overshoot_tweens = overshoot_tweens + 1
            if g.prop == "opacity" then
              local key = ("op|%d"):format(math.floor(seg.t0 * 2))
              if not overshoot_reported[key] then
                overshoot_reported[key] = true
                add("overshoot_on_opacity", "error", g.node, seg.t0, seg.t1, nil, nil,
                  "overshoot ease on opacity (" .. seg.ease_id .. ") — opacity exceeds 1 then clamps; use a smooth ease")
              end
            end
          end
          if seg.t0 < first_motion then first_motion = seg.t0 end
          -- collapse stagger members: one stagger call = one ease "use"
          local in_stagger = false
          for _, st in ipairs(comp.timeline.staggers or {}) do
            if seg.t0 >= st.t0 - 0.001 and seg.t0 <= st.t0 + st.span + 0.001 then
              in_stagger = st
              break
            end
          end
          if in_stagger then
            local key = tostring(in_stagger) .. seg.ease_id
            if not ease_windows[key] then
              ease_windows[key] = true
              ease_windows[#ease_windows + 1] = { t0 = in_stagger.t0, id = seg.ease_id }
            end
          else
            ease_windows[#ease_windows + 1] = { t0 = seg.t0, id = seg.ease_id }
          end
          -- subpixel drift
          if (g.prop == "x" or g.prop == "y") and type(seg.to) == "number"
            and math.abs(seg.to - seg.from) < TH.subpixel and seg.t1 - seg.t0 > 0.5 then
            add("subpixel_drift", "info", g.node, seg.t0, seg.t1,
              math.abs(seg.to - seg.from), TH.subpixel, "imperceptible move on " .. g.prop)
          end
        else
          sets[#sets + 1] = seg.t0
        end
      end
    end
  end
  if total_tweens > 0 and overshoot_tweens / total_tweens > TH.overshoot_budget then
    add("overshoot_budget", "info", nil, 0, dur,
      overshoot_tweens / total_tweens, TH.overshoot_budget,
      ("%d/%d tweens use overshoot eases"):format(overshoot_tweens, total_tweens))
  end
  if first_motion < 0.05 and first_motion ~= math.huge then
    add("cold_open", "warn", nil, 0, 0.3, first_motion, 0.1,
      "first motion at t=0 reads as a jump cut; offset 0.1-0.3s")
  end
  -- ease monoculture: same ease id > N times within any 2s window
  table.sort(ease_windows, function(a, b) return a.t0 < b.t0 end)
  local reported_ease = {}
  for i = 1, #ease_windows do
    local counts = {}
    for j = i, #ease_windows do
      if ease_windows[j].t0 - ease_windows[i].t0 > 2 then break end
      local id = ease_windows[j].id
      counts[id] = (counts[id] or 0) + 1
      if counts[id] > TH.ease_repeat and id ~= "linear" and not reported_ease[id .. math.floor(ease_windows[i].t0)] then
        reported_ease[id .. math.floor(ease_windows[i].t0)] = true
        add("ease_monoculture", "info", nil, ease_windows[i].t0, ease_windows[i].t0 + 2,
          counts[id], TH.ease_repeat, ("ease %q used %d times in 2s"):format(id, counts[id]))
      end
    end
  end
  -- flash cut rate
  table.sort(sets)
  for i = 1, #sets do
    local c = 1
    for j = i + 1, #sets do
      if sets[j] - sets[i] <= 1.0 then c = c + 1 else break end
    end
    if c > TH.flash_rate then
      add("flash_cut_rate", "warn", nil, sets[i], sets[i] + 1, c, TH.flash_rate,
        c .. " instant sets within 1s")
      break
    end
  end
  -- stagger spans
  for _, st in ipairs(comp.timeline.staggers or {}) do
    if st.span > TH.stagger_span then
      add("stagger_runaway", "warn", nil, st.t0, st.t0 + st.span, st.span,
        TH.stagger_span, ("stagger spreads %.2fs over %d items"):format(st.span, st.n))
    end
  end

  -- ============ geometry rules ============
  for _, n in ipairs(visual) do
    local min_size_worst, unsafe_worst = nil, nil
    -- onscreen/offscreen life analysis (HF discipline: hard-kill exits, park at
    -- opacity 0 — being visible while fully off-canvas is either waste or a bug)
    local first_on, last_on, visible_ever, last_vis = nil, nil, false, nil
    for s = 0, n_samples - 1 do
      local st = states[s][n]
      if st and st.vis then
        visible_ever = true
        last_vis = s / fps
        if st.bx0 and not (st.bx1 < 0 or st.bx0 > comp.width or st.by1 < 0 or st.by0 > comp.height) then
          first_on = first_on or s / fps
          last_on = s / fps
        end
      end
    end
    if visible_ever then
      if not first_on and states[0][n] and states[0][n].bx0 then
        add("never_onscreen", "info", n, 0, dur, nil, nil,
          "visible somewhere in the comp but never inside the canvas")
      else
        if first_on and first_on > 0.5 then
          -- parked visible off-canvas before entrance
          local st0 = states[0][n]
          if st0 and st0.vis and st0.bx0
            and (st0.bx1 < 0 or st0.bx0 > comp.width or st0.by1 < 0 or st0.by0 > comp.height) then
            add("parked_visible", "info", n, 0, first_on, first_on, 0.5,
              "parked off-canvas with opacity>0 before entering; set opacity=0 until entrance")
          end
        end
        if last_on and last_vis and last_vis - last_on > 1.5 and last_on < dur - 0.5 then
          add("off_frame_linger", "warn", n, last_on, last_vis, last_vis - last_on, 1.5,
            "exits canvas but stays visible; hard-kill after exit (opacity=0)")
        end
      end
    end
    for s = 0, n_samples - 1 do
      local st = states[s][n]
      if st and st.vis and st.bx0 then
        if n.kind == "text" then
          local sz = (st.size or 32) * st.sc
          if sz < TH.text_min and (not min_size_worst or sz < min_size_worst.v) then
            min_size_worst = { t = s / fps, v = sz }
          end
          local cx, cy = (st.bx0 + st.bx1) / 2, (st.by0 + st.by1) / 2
          if cx < TH.safe_x or cx > comp.width - TH.safe_x
            or cy < TH.safe_y or cy > comp.height - TH.safe_y then
            unsafe_worst = unsafe_worst or s / fps
          end
        end
        if (st.w and st.w <= 0) or (st.h and st.h <= 0) or st.sc <= 0 then
          add("zero_area", "error", n, s / fps, s / fps, st.sc, 0, "zero/negative area while visible")
          break
        end
      end
    end
    if min_size_worst then
      add("text_min_size", "warn", n, min_size_worst.t, min_size_worst.t,
        min_size_worst.v, TH.text_min, "text below legibility floor")
    end
    if unsafe_worst then
      add("safe_area", "info", n, unsafe_worst, unsafe_worst, nil, nil,
        "text center outside safe margins")
    end
  end

  -- ============ color / brand ============
  -- background stack: full-canvas rects (static or animated color)
  local bgs = {}
  for _, n in ipairs(visual) do
    if n.kind == "rect" and (n.initial.w or 0) >= comp.width * 0.9
      and (n.initial.h or 0) >= comp.height * 0.9 then
      bgs[#bgs + 1] = n
    end
  end
  local function bg_color_at(s)
    local area = comp.width * comp.height
    for i = #bgs, 1, -1 do
      local st = states[s][bgs[i]]
      if st and st.vis and st.bx0 then
        -- must actually COVER the canvas at this moment (a full-size slab
        -- parked off-canvas is not the background)
        local ix = math.max(0, math.min(st.bx1, comp.width) - math.max(st.bx0, 0))
        local iy = math.max(0, math.min(st.by1, comp.height) - math.max(st.by0, 0))
        if ix * iy >= area * 0.7 then
          local c = bgs[i].state.color or bgs[i].initial.color
          if c and (c[4] or 1) > 0.9 then return c end
        end
      end
    end
    return comp.background
  end
  local palette = {}
  for _, n in ipairs(visual) do
    if n.kind == "text" then
      -- contrast at each visible second
      local reported = false
      for s = 0, n_samples - 1, fps do
        local st = states[s][n]
        if st and st.vis and st.op > 0.5 and not reported then
          comp.timeline:evaluate(s / fps)
          local fg = n.state.color or n.initial.color
          local outline = n.state.outline
          if outline == nil then outline = n.initial.outline end
          if outline and outline > 0 then
            fg = n.initial.outline_color or fg
            if type(fg) == "string" then fg = require("cadence.color").parse(fg) end
          end
          local bg = bg_color_at(s)
          if fg and bg then
            local ratio = contrast_ratio(fg, bg)
            local large = ((st.size or 32) * st.sc) >= TH.large_size
            local need = large and TH.contrast_large or TH.contrast_normal
            if ratio < need then
              reported = true
              add("contrast_static", "error", n, s / fps, s / fps, ratio, need,
                ("%.2f:1 vs required %.1f:1"):format(ratio, need),
                "adjust lightness toward passing in OKLab direction")
            end
          end
        end
      end
    end
    local c = n.initial.color
    if c then
      local key = ("%d,%d,%d"):format(c[1] * 255, c[2] * 255, c[3] * 255)
      palette[key] = c
    end
  end
  local pcount = 0
  for _ in pairs(palette) do pcount = pcount + 1 end
  if pcount > TH.palette_max then
    add("palette_sprawl", "info", nil, 0, dur, pcount, TH.palette_max,
      pcount .. " distinct colors in comp")
  end
  if opts.brand and opts.brand.palette then
    local bp = {}
    for _, hex in ipairs(opts.brand.palette) do bp[#bp + 1] = color.parse(hex) end
    for key, c in pairs(palette) do
      local best = math.huge
      for _, b in ipairs(bp) do best = math.min(best, oklab_dist(c, b)) end
      if best > TH.brand_de then
        add("brand_token", "warn", nil, 0, dur, best, TH.brand_de,
          "color rgb(" .. key .. ") not in brand palette")
      end
    end
    for _, n in ipairs(visual) do
      if n.kind == "text" and not n.initial.font then
        add("brand_font", "warn", n, 0, dur, nil, nil, "text without brand font")
      end
    end
  end

  -- ============ audio rules ============
  local has_audio = #audio > 0
  if has_audio then
    for i, a in ipairs(audio) do
      local ia = a.initial
      if ia.at + (ia.duration or 0) > dur + 0.05 then
        add("audio_tail", "warn", a, dur, ia.at + ia.duration,
          ia.at + ia.duration - dur, 0, "clip extends past comp end (clamped)")
      end
      if a.kind == "tts" then
        for j, b in ipairs(audio) do
          if j > i and b.kind == "tts" then
            local ib = b.initial
            if ia.at < ib.at + ib.duration and ib.at < ia.at + ia.duration then
              add("vo_collision", "error", a, math.max(ia.at, ib.at),
                math.min(ia.at + ia.duration, ib.at + ib.duration), nil, nil,
                "two speech clips overlap")
            end
          end
        end
        -- ducking: beds (music, or long sfx/audio) too loud under speech
        for _, b in ipairs(audio) do
          local ib = b.initial
          local is_bed = b.kind == "music" or ((ib.duration or 0) >= 5 and b ~= a)
          if is_bed and ia.at < ib.at + ib.duration and ib.at < ia.at + ia.duration
            and (ib.volume or 1) > TH.duck_vol then
            add("duck_missing", "warn", b, ia.at, ia.at + ia.duration,
              ib.volume, TH.duck_vol, "bed volume high under speech")
          end
        end
      end
      if a.kind == "sfx" and (ia.duration or 0) < 2 then
        local near = false
        for _, g in ipairs(comp.timeline.order) do
          for _, seg in ipairs(g.segs) do
            if math.abs(seg.t0 - ia.at) < 0.25 or math.abs(seg.t1 - ia.at) < 0.25 then
              near = true break
            end
          end
          if near then break end
        end
        if not near then
          add("sfx_orphan", "info", a, ia.at, ia.at + (ia.duration or 0), nil, nil,
            "sfx hit without a nearby visual beat")
        end
      end
    end
    -- coverage gaps
    local events = {}
    for _, a in ipairs(audio) do
      events[#events + 1] = { t = a.initial.at, d = 1 }
      events[#events + 1] = { t = a.initial.at + (a.initial.duration or 0), d = -1 }
    end
    table.sort(events, function(x, y) return x.t < y.t end)
    local depth, last_end = 0, 0
    for _, ev in ipairs(events) do
      if depth == 0 and ev.d == 1 and ev.t - last_end > TH.gap_span and last_end > 0 then
        add("audio_gap", "info", nil, last_end, ev.t, ev.t - last_end, TH.gap_span,
          "silence gap in a comp with audio")
      end
      depth = depth + ev.d
      if depth == 0 then last_end = ev.t end
    end
  end

  table.sort(findings, function(a, b)
    local sev = { error = 1, warn = 2, info = 3 }
    if sev[a.severity] ~= sev[b.severity] then return sev[a.severity] < sev[b.severity] end
    return (a.t0 or 0) < (b.t0 or 0)
  end)
  return findings
end

return L
