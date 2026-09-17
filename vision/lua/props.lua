-- Host-free node-state probe: evaluate a comp at given times and print JSON.
--   luajit vision/lua/props.lua <root> <comp.lua> <t1> [t2 ...]
-- Output: { "fps":30, "duration":..., "width":.., "height":.., "nodes":[{id,kind,z,parent,src,anchor}], "at":{"1.0":{"text1":{"x":..,"opacity":..}}} }
local root, comp_path = arg[1], arg[2]
assert(root and comp_path, "usage: props.lua <root> <comp.lua> <t...>")
package.path = root .. "/lib/?.lua;" .. root .. "/lib/?/init.lua;" .. package.path
-- minimal JSON encoder (lib/cadence/json.lua only decodes)
local function enc(v)
  local tv = type(v)
  if tv == "nil" then return "null"
  elseif tv == "boolean" then return v and "true" or "false"
  elseif tv == "number" then
    if v ~= v or v == math.huge or v == -math.huge then return "null" end
    return string.format("%.6g", v)
  elseif tv == "string" then
    return '"' .. v:gsub('[%c"\\]', function(c)
      if c == '"' then return '\\"' elseif c == "\\" then return "\\\\" end
      return string.format("\\u%04x", c:byte()) end) .. '"'
  elseif tv == "table" then
    local mt = getmetatable(v)
    if not (mt and mt.__jsontype == "object") and (#v > 0 or next(v) == nil) then
      local parts = {}
      for i = 1, #v do parts[i] = enc(v[i]) end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    local keys = {}
    for k in pairs(v) do keys[#keys + 1] = tostring(k) end
    table.sort(keys)
    local parts = {}
    for _, k in ipairs(keys) do parts[#parts + 1] = enc(k) .. ":" .. enc(v[k]) end
    return "{" .. table.concat(parts, ",") .. "}"
  end
  return "null"
end
local json = { encode = enc }

-- determinism sandbox, same spirit as the renderer
math.randomseed(0)
local chunk = assert(loadfile(comp_path))
local comp = chunk()
assert(type(comp) == "table" and comp.compile, "comp did not return e.comp{}")
comp:compile()

local PROPS = { "x", "y", "w", "h", "r", "rx", "size", "opacity", "rotation", "scale",
  "color", "text", "reveal", "progress", "tracking", "weight", "outline",
  "fx_bloom", "fx_glow", "fx_blur", "fx_vignette", "fx_chroma", "fx_grain",
  "effect_blur", "effect_hue_rotate", "effect_saturate", "effect_brightness", "effect_contrast" }

local out = { fps = comp.fps or 30, duration = comp.duration, width = comp.width, height = comp.height,
  nodes = {}, at = setmetatable({}, { __jsontype = "object" }) }
for z, n in ipairs(comp.nodes) do
  local i = n.initial or {}
  out.nodes[#out.nodes + 1] = setmetatable({ id = n.id, kind = n.kind, z = z,
    parent = n.parent and n.parent.id or nil, src = i.src, anchor = i.anchor, wrap = i.wrap,
    from = i.from, dur = i.duration }, { __jsontype = "object" })
end
for i = 3, #arg do
  local t = tonumber(arg[i])
  comp:evaluate(t)
  local row = setmetatable({}, { __jsontype = "object" })
  for _, n in ipairs(comp.nodes) do
    local st = setmetatable({}, { __jsontype = "object" })
    for _, p in ipairs(PROPS) do
      local v = n:get(p)
      if v ~= nil then
        if type(v) == "table" then
          local parts = {}
          for k = 1, #v do parts[k] = v[k] end
          v = parts
        end
        st[p] = v
      end
    end
    row[n.id] = st
  end
  out.at[arg[i]] = row
end
io.write(json.encode(out), "\n")
