-- Stable runtime path for staged native helpers. Development and CI can point
-- ELLUA_NATIVE directly at a Cargo release directory to skip staging.
local N = {}

function N.root()
  return love.filesystem.getSource():gsub("/runtime/?$", "")
end

function N.extension()
  if jit and jit.os == "Windows" then return ".dll" end
  if jit and jit.os == "OSX" then return ".dylib" end
  return ".so"
end

function N.lib(name)
  local dir = os.getenv("CADENCE_NATIVE") or os.getenv("ELLUA_NATIVE") or (N.root() .. "/native/release")
  local prefix = (jit and jit.os == "Windows") and "" or "lib"
  return dir .. "/" .. prefix .. name .. N.extension()
end

return N
