-- Compile-phase Box2D bake. Simulation runs once at a fixed dt; sampled
-- x/y/rotation become ordinary timeline segments. Render stays pure f(t).
local B = {}

local function node_size(node)
  local i = node.initial
  local w = i.w
  local h = i.h
  if (not w or not h) and node.kind == "text" then
    local painter = require("painter")
    local font = painter.font(i.size or 32, i.font)
    w = font:getWidth(i.text or "")
    h = font:getHeight()
  end
  if (not w or not h) and i.r then
    w, h = i.r * 2, i.r * 2
  end
  return w or 40, h or 40
end

local function center_of(node)
  local x, y = node.initial.x or 0, node.initial.y or 0
  local w, h = node_size(node)
  if node.initial.anchor == "center" or node.kind == "circle" then
    return x, y, w, h
  end
  return x + w / 2, y + h / 2, w, h
end

local function write_sim(rec, node, prop, value)
  rec.sim[node] = rec.sim[node] or {}
  rec.sim[node][prop] = value
  node.initial["_" .. prop .. "_rest"] = value
end

function B.bake(comp, rec)
  local drops = comp.timeline.drops
  if not drops or #drops == 0 then return end
  if not love.physics then
    error("ellua: t:drop needs love.physics (enable t.modules.physics in conf.lua)", 0)
  end
  local prev_meter = love.physics.getMeter()
  local meter = 64
  love.physics.setMeter(meter)

  for _, job in ipairs(drops) do
    local opts = job.opts or {}
    local t0, t1 = job.t0, job.t1
    local dur = t1 - t0
    local fps = opts.sample_fps or 60
    local n_samples = math.max(2, math.floor(dur * fps) + 1)
    -- Authoring gravity is pixels/s². LÖVE scaleDown() divides by meter.
    local g = opts.gravity or 980
    local world = love.physics.newWorld(0, g, false)

    local ground_y = opts.ground_y or (comp.height - 48)
    local gw = (opts.ground_w or comp.width) + 80
    local ground = love.physics.newBody(world, comp.width / 2, ground_y, "static")
    local gshape = love.physics.newRectangleShape(ground, gw, 24)
    gshape:setFriction(opts.friction or 0.55)

    if opts.walls ~= false then
      local left = love.physics.newBody(world, 12, comp.height / 2, "static")
      love.physics.newRectangleShape(left, 24, comp.height)
      local right = love.physics.newBody(world, comp.width - 12, comp.height / 2, "static")
      love.physics.newRectangleShape(right, 24, comp.height)
    end

    local bodies = {}
    for idx, node in ipairs(job.nodes) do
      local cx, cy, w, h = center_of(node)
      local body = love.physics.newBody(world, cx, cy, "dynamic")
      local shape = love.physics.newRectangleShape(body, math.max(8, w * 0.92), math.max(8, h * 0.78))
      shape:setDensity(opts.density or 1)
      shape:setRestitution(opts.restitution or 0.18)
      shape:setFriction(opts.friction or 0.45)
      body:resetMassData()
      if opts.spin then
        body:setAngularVelocity((idx % 2 == 0 and 1 or -1) * (opts.spin or 2))
      end
      bodies[#bodies + 1] = { node = node, body = body, w = w, h = h }
    end

    local xs, ys, rs = {}, {}, {}
    for i = 1, #bodies do
      xs[i], ys[i], rs[i] = {}, {}, {}
    end

    local dt = dur / (n_samples - 1)
    local phys_dt = 1 / 240
    local acc = 0
    for s = 1, n_samples do
      if s > 1 then
        acc = acc + dt
        while acc >= phys_dt do
          world:update(phys_dt)
          acc = acc - phys_dt
        end
      end
      for i, item in ipairs(bodies) do
        local bx, by = item.body:getX(), item.body:getY()
        local ang = item.body:getAngle()
        local node = item.node
        if node.initial.anchor == "center" or node.kind == "circle" then
          xs[i][s], ys[i][s] = bx, by
        else
          xs[i][s] = bx - item.w / 2
          ys[i][s] = by - item.h / 2
        end
        rs[i][s] = ang
      end
    end

    for i, item in ipairs(bodies) do
      local node = item.node
      comp.timeline:record_bake(node, "x", t0, t1, xs[i])
      comp.timeline:record_bake(node, "y", t0, t1, ys[i])
      comp.timeline:record_bake(node, "rotation", t0, t1, rs[i])
      write_sim(rec, node, "x", xs[i][#xs[i]])
      write_sim(rec, node, "y", ys[i][#ys[i]])
      write_sim(rec, node, "rotation", rs[i][#rs[i]])
    end

    world:destroy()
  end

  love.physics.setMeter(prev_meter)
end

return B
