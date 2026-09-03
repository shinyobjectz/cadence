-- Web host bridge. Runs inside wasmoon (Lua 5.4 WASM). Compiles a mounted
-- composition and snapshots a frame for the Canvas2D painter.
-- Not used by the native ellua-love host.

  unpack = table.unpack
  math.atan2 = math.atan2 or math.atan

local function json_encode(v)
  local t = type(v)
  if v == nil then return "null" end
  if t == "boolean" then return v and "true" or "false" end
  if t == "number" then
    if v ~= v or v == math.huge or v == -math.huge then return "null" end
    return string.format("%.6g", v)
  end
  if t == "string" then
    return '"' .. v:gsub('\\', '\\\\'):gsub('"', '\\"'):gsub('\n', '\\n'):gsub('\r', '\\r') .. '"'
  end
  if t == "table" then
    local n = #v
    local is_arr = true
    local count = 0
    for k, _ in pairs(v) do
      count = count + 1
      if type(k) ~= "number" or k < 1 or k > n or k % 1 ~= 0 then is_arr = false end
    end
    if is_arr and (n > 0 or count == 0) then
      local parts = {}
      for i = 1, n do parts[i] = json_encode(v[i]) end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    local parts = {}
    for k, val in pairs(v) do
      if type(k) == "string" then
        parts[#parts + 1] = json_encode(k) .. ":" .. json_encode(val)
      end
    end
    return "{" .. table.concat(parts, ",") .. "}"
  end
  return "null"
end

local function trs(x, y, rot, scale)
  local c, s = math.cos(rot or 0), math.sin(rot or 0)
  scale = scale or 1
  return {
    a = scale * c, b = scale * s,
    c = -scale * s, d = scale * c,
    e = x or 0, f = y or 0,
  }
end

local function mul(m1, m2)
  return {
    a = m1.a * m2.a + m1.c * m2.b,
    b = m1.b * m2.a + m1.d * m2.b,
    c = m1.a * m2.c + m1.c * m2.d,
    d = m1.b * m2.c + m1.d * m2.d,
    e = m1.a * m2.e + m1.c * m2.f + m1.e,
    f = m1.b * m2.e + m1.d * m2.f + m1.f,
  }
end

local function ident()
  return { a = 1, b = 0, c = 0, d = 1, e = 0, f = 0 }
end

local function parent_chain(node)
  local chain, p = {}, node.initial and node.initial.parent
  while p do
    table.insert(chain, 1, p)
    p = p.initial and p.initial.parent
  end
  return chain
end

local function world_matrix(node)
  local m = ident()
  for _, p in ipairs(parent_chain(node)) do
    m = mul(m, trs(p:get("x") or 0, p:get("y") or 0, p:get("rotation") or 0, p:get("scale") or 1))
  end
  return mul(m, trs(node:get("x") or 0, node:get("y") or 0, node:get("rotation") or 0, node:get("scale") or 1))
end

local function inherited_opacity(node)
  local op = node:get("opacity") or 1
  local p = node.initial and node.initial.parent
  while p do
    op = op * (p:get("opacity") or 1)
    p = p.initial and p.initial.parent
  end
  return op
end

local function rgba(c)
  if type(c) ~= "table" then return { 1, 1, 1, 1 } end
  return { c[1] or 0, c[2] or 0, c[3] or 0, c[4] or 1 }
end

local SKIP = {
  group = true, kinetic = true, flex = true,
  tts = true, sfx = true, music = true,
  html = true, page = true, vector = true, fx = true, displace = true, draw = true,
  rive = true, world = true, mesh = true, light = true, camera = true,
}

local MEDIA = {
  image = true, svg = true, video = true, spritesheet = true, lottie = true,
}

local function fill_media(item, node)
  local i = node.initial
  item.id = node.id
  item.src = i.src
  item.w = node:get("w") or i.w
  item.h = node:get("h") or i.h
  item.rx = node:get("rx") or i.rx or 0
  item.from = i.from or 0
  item.duration = i.duration
  item.media_start = i.media_start or 0
  item.fps = i.fps
  item.loop = i.loop ~= false
  item.speed = i.speed or 1
  item.cols = i.cols
  item.rows = i.rows
end

local function layout_kinetic(nodes, measure)
  if type(measure) ~= "function" then return end
  for _, n in ipairs(nodes) do
    if n.kind == "kinetic" then
      local i = n.initial
      local widths, total = {}, 0
      for ch in i.text:gmatch(".") do
        local w = measure(ch, i.size or 64, i.font)
        widths[#widths + 1] = { ch = ch, w = w }
        total = total + w + (i.spacing or 0)
      end
      total = total - (i.spacing or 0)
      local cursor = i.x - total / 2
      local ci = 0
      for _, e in ipairs(widths) do
        if e.ch ~= " " then
          ci = ci + 1
          local node = n.chars[ci]
          if node then
            node.initial.x = cursor + e.w / 2
            node.initial.y = i.y
          end
        end
        cursor = cursor + e.w + (i.spacing or 0)
      end
    end
  end
end

function compile_comp(src, inputs_map)
  local chunk, err = load(src, "comp.lua", "t")
  if not chunk then error(err, 0) end
  local comp = chunk()
  assert(type(comp) == "table" and comp.compile, "ellua web: comp file must return e.comp{}")
  local measure = _G.__measure_text
  comp:compile({
    post_scene = function(nodes)
      layout_kinetic(nodes, measure)
    end,
  }, inputs_map)
  _G.__comp = comp
  return {
    width = comp.width,
    height = comp.height,
    duration = comp.duration,
    fps = comp.fps,
  }
end

function compile_comp_safe(src, inputs_map)
  local ok, result = pcall(compile_comp, src, inputs_map)
  if ok then
    return {
      ok = true,
      width = result.width,
      height = result.height,
      duration = result.duration,
      fps = result.fps,
    }
  end
  return { ok = false, error = tostring(result) }
end

function snapshot(t)
  local comp = assert(_G.__comp, "ellua web: no compiled composition")
  t = math.max(0, math.min(t, comp.duration))
  comp:evaluate(t)
  local nodes, audios = {}, {}
  for _, node in ipairs(comp.nodes) do
    if node.kind == "audio" then
      local i = node.initial
      audios[#audios + 1] = {
        src = i.src, at = i.at or 0, duration = i.duration,
        media_start = i.media_start or 0, volume = i.volume or 1,
        fade_in = i.fade_in or 0, fade_out = i.fade_out or 0,
      }
    elseif not SKIP[node.kind] then
      local op = inherited_opacity(node)
      if op > 0.001 or MEDIA[node.kind] then
        local m = world_matrix(node)
        local item = {
          kind = node.kind,
          a = m.a, b = m.b, c = m.c, d = m.d, e = m.e, f = m.f,
          opacity = op,
          color = rgba(node:get("color")),
          anchor = node:get("anchor") or node.initial.anchor,
          blend = node.initial.blend,
        }
        if node.kind == "rect" or node.kind == "surface" then
          item.w = node:get("w") or 0
          item.h = node:get("h") or 0
          item.rx = node:get("rx") or 0
          if node.initial.shadow then
            item.shadow = {
              blur = node.initial.shadow.blur or 40,
              dy = node.initial.shadow.dy or 10,
              alpha = node.initial.shadow.alpha or 0.3,
            }
          end
        elseif node.kind == "circle" then
          item.r = node:get("r") or 0
        elseif node.kind == "text" then
          item.text = node:get("text") or node.initial.text or ""
          item.size = node:get("size") or 32
          item.wrap = node.initial.wrap
          item.reveal = node:get("reveal")
          item.leading = node.initial.leading or 1.15
          item.font = node:get("font") or node.initial.font
        elseif node.kind == "particles" then
          local i = node.initial
          item.n = i.n
          item.seed = i.seed or 1
          item.life = i.life or 1.2
          item.emit = i.emit or 0
          item.emit_window = i.emit_window or 0.2
          item.heading = i.heading or -math.pi / 2
          item.spread = i.spread or math.pi
          item.speed = i.speed or 240
          item.gravity = i.gravity or 520
          item.r = i.r or 3.5
        elseif node.kind == "chart" then
          item.cmds = (package.loaded["cadence.chart"] and require("cadence.chart") or require("ellua.chart")).commands({
            type = node.initial.type, data = node.initial.data, data1 = node.initial.data1,
            w = node:get("w"), h = node:get("h"),
            stroke = node.initial.stroke, seed = node.initial.seed,
            roughness = node.initial.roughness,
            color = node:get("color") or node.initial.color,
            colors = node.initial.colors, hue = node.initial.hue,
            mix = node:get("mix") or 0, width = node.initial.width,
            inner = node.initial.inner, start = node.initial.start,
            track = node.initial.track,
          }, node:get("reveal") or 1)
        elseif node.kind == "ornament" then
          item.polylines = (package.loaded["cadence.ornament"] and require("cadence.ornament") or require("ellua.ornament")).polylines(node.initial.kind, {
            w = node:get("w"), h = node:get("h"), r = node.initial.r,
            n = node.initial.n, inner = node.initial.inner,
            rx = node.initial.rx, ry = node.initial.ry,
            x0 = node.initial.x0, y0 = node.initial.y0,
            x1 = node.initial.x1, y1 = node.initial.y1, bow = node.initial.bow,
            stroke = node.initial.stroke, seed = node.initial.seed,
            roughness = node.initial.roughness,
          }, node:get("reveal") or 1)
          item.width = node.initial.width or 2.5
        elseif node.kind == "spine" then
          if node.initial.skeleton then
            item.bones = (package.loaded["cadence.spine"] and require("cadence.spine") or require("ellua.spine")).pose(
              node.initial.skeleton, node.initial.animation, t)
          end
        elseif MEDIA[node.kind] then
          fill_media(item, node)
        end
        nodes[#nodes + 1] = item
      end
    end
  end
  return json_encode({
    t = t,
    width = comp.width,
    height = comp.height,
    bg = rgba(comp.background),
    nodes = nodes,
    audios = audios,
  })
end

return {
  compile_comp = compile_comp,
  compile_comp_safe = compile_comp_safe,
  snapshot = snapshot,
}
