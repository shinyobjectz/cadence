-- smoke: a video node's crop window survives the web bridge.
-- The painter reads node.crop to decide which part of a wide source fills a
-- tall frame. If the bridge drops it, every reframe silently centre-crops and
-- looks exactly like a comp that asked for nothing.
package.path = "./lib/?.lua;./lib/?/init.lua;" .. package.path
dofile("web/bridge.lua")

local function snap(extra)
  compile_comp(([[
    local e = require("cadence")
    return e.comp {
      width = 1080, height = 1920, duration = 8, fps = 30,
      scene = function(s)
        s:video { src = "x.webm", from = 0, duration = 8, media_start = 0,
                  x = 0, y = 0, w = 1080, h = 1920%s }
      end,
    }
  ]]):format(extra))
  return snapshot(1.0)
end

local windowed = snap(', crop = { x = 1750, y = 0, w = 810, h = 1440 }')
assert(windowed:find('"crop"'), "the bridge dropped the crop window")
assert(windowed:find('"x":1750'), "the crop window lost its origin")
assert(windowed:find('"w":810'), "the crop window lost its width")

-- No crop is not an empty crop: the painter's centre rule must stay reachable.
local plain = snap("")
assert(not plain:find('"crop"'), "a node with no crop should not carry one")

print("ok   tests/web_bridge_crop.lua")
