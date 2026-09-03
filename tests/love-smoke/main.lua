-- Isolates the vendored engine from Ellua's runtime. It must run to completion
-- with `ELLUA_HEADLESS=1 ellua-love tests/love-smoke`.
function love.run()
  love.graphics.clear(0.1, 0.2, 0.3, 1)
  love.graphics.present()
  return 0
end
