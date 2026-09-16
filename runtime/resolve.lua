-- Resolve phase (host code, runs AFTER comp load, BEFORE render).
-- Network/disk allowed here — render itself stays I/O-free per canon.
-- v0 video path: extract frames at comp fps, cover-cropped to node w×h, into a
-- content-addressed cache. Long-term this is replaced by the ellua-decode sidecar.
local R = {}

local function sh(cmd)
  local p = assert(_ELLUA_POPEN(cmd .. " 2>&1", "r"))
  local out = p:read("*a")
  local ok = p:close()
  return ok, out
end

local function sha1(s)
  return love.data.encode("string", "hex", love.data.hash("sha1", s))
end

local function cache_root()
  local custom = os.getenv("CADENCE_CACHE") or os.getenv("ELLUA_CACHE")
  if custom then return custom end
  return (os.getenv("HOME") or "/tmp") .. "/.cache/cadence"
end

local function exists(path)
  local f = _ELLUA_IOOPEN(path, "r")
  if f then f:close() return true end
  return false
end

local function localize(src)
  if src:match("^https?://") then
    local dst = cache_root() .. "/dl/" .. sha1(src) .. ".bin"
    if not exists(dst) then
      assert(sh(("mkdir -p '%s/dl'"):format(cache_root())))
      local ok, out = sh(("curl -fsSL -o '%s' '%s'"):format(dst, src))
      if not ok then error("ellua resolve: download failed: " .. src .. "\n" .. out) end
    end
    return dst
  end
  if not src:match("^/") then
    return (os.getenv("ELLUA_CWD") or ".") .. "/" .. src
  end
  return src
end

-- Layout pass: runs as compile's post_scene hook (before scripts record), so
-- tweens resolve from solved positions.
function R.layout(nodes)
  local layout = require("layout")
  for _, n in ipairs(nodes) do
    if n.kind == "kinetic" then
      -- measure per-char widths with the real font, center the run on i.x
      local i = n.initial
      local scene = require("scene")
      local font = (not scene.enabled) and require("painter").font(i.size, i.font) or nil
      local fid = scene.enabled and scene.font_id(i.font) or nil
      local widths, total = {}, 0
      for ch in i.text:gmatch(".") do
        local w = font and font:getWidth(ch) or scene.measure(fid, i.size, ch)
        widths[#widths + 1] = { ch = ch, w = w }
        total = total + w + i.spacing
      end
      total = total - i.spacing
      local cursor = i.x - total / 2
      local ci = 0
      for _, e in ipairs(widths) do
        if e.ch ~= " " then
          ci = ci + 1
          local node = n.chars[ci]
          node.initial.x = cursor + e.w / 2
          node.initial.y = i.y
        end
        cursor = cursor + e.w + i.spacing
      end
    elseif n.kind == "flex" then
      if not layout.available then error("ellua resolve: layout dylib missing (build layout/)") end
      local i = n.initial
      local items = {}
      for k, child in ipairs(i.items) do
        items[k] = { w = child.initial.w or (child.initial.r and child.initial.r * 2),
                     h = child.initial.h or (child.initial.r and child.initial.r * 2),
                     grow = child.initial.grow, margin = child.initial.margin }
      end
      local solved = layout.solve(i, items)
      for k, child in ipairs(i.items) do
        local sl = solved[k]
        local ci = child.initial
        ci.w = ci.w and sl.w or ci.w
        ci.h = ci.h and sl.h or ci.h
        local centered = ci.anchor == "center" or child.kind == "circle"
        local ax = centered and sl.w / 2 or 0
        local ay = centered and sl.h / 2 or 0
        ci.x = i.x + sl.x + ax
        ci.y = i.y + sl.y + ay
      end
    end
  end
end

local function ffprobe_duration(file)
  local ok, out = sh(("ffprobe -v error -show_entries format=duration -of csv=p=0 '%s'"):format(file))
  local d = ok and tonumber(out:match("[%d%.]+"))
  if not d then error("ellua resolve: cannot probe duration of " .. file) end
  return d
end

local function bake_energy(file, start, dur)
  local rate = 50
  local key = sha1(table.concat({ tostring(file), tostring(start), tostring(dur), tostring(rate) }, "|"))
  local dst = cache_root() .. "/energy/" .. key .. ".f32"
  if not exists(dst) then
    assert(sh(("mkdir -p '%s/energy'"):format(cache_root())))
    local ok, out = sh(("ffmpeg -y -hide_banner -loglevel error -ss %s -t %s -i '%s' -ac 1 -ar %d -f f32le '%s'")
      :format(start or 0, dur, file, rate, dst))
    if not ok then error("ellua resolve: energy bake failed\n" .. out) end
  end
  local f = assert(_ELLUA_IOOPEN(dst, "rb"), "ellua resolve: energy file missing")
  local raw = f:read("*a")
  f:close()
  local ffi = require("ffi")
  local n = math.floor(#raw / 4)
  local buf = ffi.new("uint8_t[?]", #raw)
  ffi.copy(buf, raw)
  local fl = ffi.cast("float*", buf)
  local samples, peak = {}, 0
  for i = 0, n - 1 do
    local v = math.abs(fl[i])
    samples[i + 1] = v
    if v > peak then peak = v end
  end
  if peak < 1e-6 then peak = 1 end
  for i = 1, #samples do samples[i] = samples[i] / peak end
  return samples
end

local function elevenlabs_key()
  local key = os.getenv("ELEVENLABS_API_KEY")
  if not key or key == "" then
    error("ellua resolve: ELEVENLABS_API_KEY not set — export it to use tts{}/sfx{} nodes", 0)
  end
  return key
end

local VOICES = { -- premade voices (free-tier API-safe); any raw voice_id also accepted
  sarah = "EXAVITQu4vr4xnSDxMaL",
  roger = "CwhRBWXzGAHq8TQ4Fs17",
  george = "JBFqnCBsd6RMkjVDRZzb",
  alice = "Xb7hH8MSUJpSbSDYk0k2",
  liam = "TX3LPaxmHKxFdv7VOQHJ",
  laura = "FGY2WhTYpPnrIDTdsKH5",
  rachel = "EXAVITQu4vr4xnSDxMaL", -- alias → Sarah (Rachel is library-gated on free API)
}

-- LuaJIT popen:close() doesn't return exit status — verify the artifact instead:
-- a failed ElevenLabs call writes a JSON error body where audio should be.
local function verify_audio(dst, what, out)
  local f = _ELLUA_IOOPEN(dst, "rb")
  local head = f and f:read(2)
  if f then f:close() end
  if not head or head == "{" or head:sub(1, 1) == "{" then
    local body = ""
    local g = _ELLUA_IOOPEN(dst, "rb")
    if g then body = g:read("*a") or ""; g:close() end
    os.remove(dst)
    error(("ellua resolve: %s failed: %s%s"):format(what, body ~= "" and body or "(no output)",
      out and ("\n" .. out) or ""), 0)
  end
end

-- Draft tiering: ELLUA_DRAFT=1 swaps un-pinned models to Flash (fast/cheap
-- iteration); finals re-render without the flag. Cache keys include the model,
-- so draft and final audio never collide.
local function tts_model(i)
  if i.model then return i.model end
  if os.getenv("ELLUA_DRAFT") == "1" then return "eleven_flash_v2_5" end
  return "eleven_multilingual_v2"
end

-- Build the TTS request body. Beyond text/model:
--   seed          — deterministic generation (same seed+text+voice → same audio)
--   speed         — voice_settings.speed (0.7–1.2): fit narration to a scene
--   stability/style — passthrough voice_settings
--   previous_text/next_text — request stitching: consecutive lines generated as
--     one continuous read instead of N cold opens (auto-filled from after/at_word)
--   dictionaries  — pronunciation_dictionary_locators = {{id=,version_id=},...}
local function tts_body(i, model)
  local parts = { string.format('"text":%q,"model_id":%q', i.text, model) }
  if i.seed then parts[#parts + 1] = ('"seed":%d'):format(i.seed) end
  if i.previous_text then parts[#parts + 1] = string.format('"previous_text":%q', i.previous_text) end
  if i.next_text then parts[#parts + 1] = string.format('"next_text":%q', i.next_text) end
  if i.speed or i.stability or i.style then
    local vs = {}
    if i.speed then vs[#vs + 1] = ('"speed":%s'):format(i.speed) end
    if i.stability then vs[#vs + 1] = ('"stability":%s'):format(i.stability) end
    if i.style then vs[#vs + 1] = ('"style":%s'):format(i.style) end
    parts[#parts + 1] = '"voice_settings":{' .. table.concat(vs, ",") .. '}'
  end
  if i.dictionaries then
    local ds = {}
    for _, d in ipairs(i.dictionaries) do
      ds[#ds + 1] = string.format('{"pronunciation_dictionary_id":%q,"version_id":%q}',
        d.id or d[1], d.version_id or d[2])
    end
    parts[#parts + 1] = '"pronunciation_dictionary_locators":[' .. table.concat(ds, ",") .. ']'
  end
  return "{" .. table.concat(parts, ",") .. "}"
end

local function tts_cache_key(i, voice, model)
  return sha1(table.concat({ i.text, voice, model, i.seed or "", i.speed or "",
    i.stability or "", i.style or "", i.previous_text or "", i.next_text or "",
    i.dictionaries and #i.dictionaries or "" }, "|"))
end

-- Group the with-timestamps character alignment into word timings, in the same
-- shape forced-alignment produces, so downstream code has one format.
local function chars_to_words(al)
  local words, cur, t0, t1 = {}, {}, nil, nil
  local chars = al.characters or {}
  local starts = al.character_start_times_seconds or {}
  local ends = al.character_end_times_seconds or {}
  for k = 1, #chars do
    local ch = chars[k]
    if ch:match("%s") then
      if #cur > 0 then
        words[#words + 1] = { text = table.concat(cur), start = t0, ["end"] = t1 }
        cur, t0 = {}, nil
      end
    else
      cur[#cur + 1] = ch
      t0 = t0 or starts[k]
      t1 = ends[k]
    end
  end
  if #cur > 0 then words[#words + 1] = { text = table.concat(cur), start = t0, ["end"] = t1 } end
  return words
end

-- want_align: generate through /with-timestamps — the response carries the
-- character alignment, so the word times come back WITH the audio and the
-- separate forced-alignment round-trip is skipped entirely.
local function tts_cache_key(i, voice, model)
  return sha1(table.concat({ i.text, voice or "", model or "", i.provider or "", i.seed or "", i.speed or "",
    i.stability or "", i.style or "", i.previous_text or "", i.next_text or "",
    i.dictionaries and #i.dictionaries or "" }, "|"))
end

local function cadence_audio_bin()
  local root = (os.getenv("CADENCE_ROOT") or os.getenv("ELLUA_ROOT") or (N and N.root and N.root()))
  if not root then
    root = love.filesystem and love.filesystem.getSource and love.filesystem.getSource():gsub("/runtime/?$", "")
  end
  root = root or "."
  local p = root .. "/bin/cadence-audio"
  if exists(p) then return p end
  return "bin/cadence-audio"
end

local function tts_generate(i, want_align)
  local provider = (i.provider or os.getenv("CADENCE_TTS_PROVIDER") or os.getenv("CADENCE_AUDIO_PROVIDER") or ""):lower()
  local voice = VOICES[(i.voice or "sarah"):lower()] or i.voice
  local model = tts_model(i)
  local base = cache_root() .. "/tts/" .. tts_cache_key(i, voice, model)
  local dst = base .. ".mp3"
  local align_dst = base .. ".align.json"

  if not exists(dst) then
    assert(sh(("mkdir -p '%s/tts'"):format(cache_root())))
    
    -- If an open/local/alternative provider is specified (or ElevenLabs key is absent)
    local has_eleven_key = pcall(elevenlabs_key)
    if provider ~= "" or not has_eleven_key then
      local abin = cadence_audio_bin()
      local cmd = string.format("%s tts --text %q --out %q", abin, i.text, dst)
      if provider ~= "" then cmd = cmd .. string.format(" --provider %q", provider) end
      if voice and voice ~= "" then cmd = cmd .. string.format(" --voice %q", voice) end
      if model and model ~= "" then cmd = cmd .. string.format(" --model %q", model) end
      if i.speed then cmd = cmd .. string.format(" --speed %s", i.speed) end
      if want_align then cmd = cmd .. string.format(" --align-out %q", align_dst) end
      local ok, out = sh(cmd)
      if not ok then error("cadence resolve: TTS failed:\n" .. out) end
      verify_audio(dst, "TTS", out)
      return dst
    end

    local key = elevenlabs_key()
    local body = tts_body(i, model)
    if want_align then
      local raw = base .. ".wt.json"
      local ok, out = sh(string.format(
        "curl -fsS -X POST 'https://api.elevenlabs.io/v1/text-to-speech/%s/with-timestamps?output_format=mp3_44100_128'" ..
        " -H 'xi-api-key: %s' -H 'content-type: application/json' -d '%s' -o '%s'",
        voice, key, body:gsub("'", "'\\''"), raw))
      if not ok then error("cadence resolve: TTS (with-timestamps) failed:\n" .. out) end
      local f = assert(_ELLUA_IOOPEN(raw, "rb")); local resp = f:read("*a"); f:close()
      local doc_ok, doc = pcall(function() return require("cadence.json").decode(resp) end)
      if not doc_ok or not doc.audio_base64 then
        os.remove(raw)
        error("cadence resolve: TTS with-timestamps returned no audio: " .. resp:sub(1, 200), 0)
      end
      local audio = love.data.decode("string", "base64", doc.audio_base64)
      local a = assert(_ELLUA_IOOPEN(dst, "wb")); a:write(audio); a:close()
      -- persist word times where align_generate() looks for them → no second call
      local words = chars_to_words(doc.normalized_alignment or doc.alignment or {})
      local j = { '{"words":[' }
      for k, w in ipairs(words) do
        j[#j + 1] = string.format('%s{"text":%q,"start":%s,"end":%s}',
          k > 1 and "," or "", w.text, w.start or 0, w["end"] or 0)
      end
      j[#j + 1] = "]}"
      local g = assert(_ELLUA_IOOPEN(align_dst, "wb"))
      g:write(table.concat(j)); g:close()
      os.remove(raw)
    else
      local ok, out = sh(string.format(
        "curl -fsS -X POST 'https://api.elevenlabs.io/v1/text-to-speech/%s?output_format=mp3_44100_128'" ..
        " -H 'xi-api-key: %s' -H 'content-type: application/json' -d '%s' -o '%s'",
        voice, key, body:gsub("'", "'\\''"), dst))
      if not ok then error("cadence resolve: TTS failed:\n" .. out) end
    end
    verify_audio(dst, "TTS", nil)
  end
  return dst
end

local function music_generate(i)
  local dst = cache_root() .. "/music/" ..
    sha1(table.concat({ i.prompt, i.gen_duration or 30, i.model or "music_v1" }, "|")) .. ".mp3"
  if not exists(dst) then
    local key = elevenlabs_key()
    assert(sh(("mkdir -p '%s/music'"):format(cache_root())))
    local body = string.format('{"prompt":%q,"music_length_ms":%d}',
      i.prompt, math.floor((i.gen_duration or 30) * 1000))
    local ok, out = sh(string.format(
      "curl -fsS -X POST 'https://api.elevenlabs.io/v1/music?output_format=mp3_44100_128'" ..
      " -H 'xi-api-key: %s' -H 'content-type: application/json' -d '%s' -o '%s'",
      key, body:gsub("'", "'\\''"), dst))
    if not ok then error("ellua resolve: music failed:\n" .. out) end
    verify_audio(dst, "music (note: Music API requires a paid ElevenLabs plan)", out)
  end
  return dst
end

-- the sound-generation endpoint rejects anything under 0.5s with a bare 400
local function sfx_generate(i)
  if i.gen_duration and i.gen_duration < 0.5 then
    error(("ellua: sfx{gen_duration=%.2f} too short — the API minimum is 0.5s"):format(
      i.gen_duration), 0)
  end
  local dst = cache_root() .. "/sfx/" ..
    sha1(table.concat({ i.prompt, i.gen_duration or "" }, "|")) .. ".mp3"
  if not exists(dst) then
    local key = elevenlabs_key()
    assert(sh(("mkdir -p '%s/sfx'"):format(cache_root())))
    local dur = i.gen_duration and (',"duration_seconds":' .. i.gen_duration) or ""
    local body = string.format('{"text":%q%s}', i.prompt, dur)
    local ok, out = sh(string.format(
      "curl -fsS -X POST 'https://api.elevenlabs.io/v1/sound-generation'" ..
      " -H 'xi-api-key: %s' -H 'content-type: application/json' -d '%s' -o '%s'",
      key, body:gsub("'", "'\\''"), dst))
    if not ok then error("ellua resolve: SFX failed:\n" .. out) end
    verify_audio(dst, "SFX", out)
  end
  return dst
end

-- ElevenLabs Forced Alignment: given the clip and its transcript, returns
-- per-word start/end times. Lets scripts pace motion off what the narration is
-- ACTUALLY saying instead of hand-guessed offsets — reflow the copy and every
-- cue that referenced a word moves with it. Cached beside the audio.
local function normword(w) return (w:lower():gsub("[^%w']", "")) end

local function align_generate(afile, text)
  local dst = afile:gsub("%.mp3$", "") .. ".align.json"
  if not exists(dst) then
    local has_eleven_key, key = pcall(elevenlabs_key)
    if has_eleven_key and key and key ~= "" and (not os.getenv("CADENCE_LOCAL_ALIGN")) then
      local ok, out = sh(string.format(
        "curl -fsS -X POST 'https://api.elevenlabs.io/v1/forced-alignment'" ..
        " -H 'xi-api-key: %s' -F 'file=@%s' -F 'text=%s' -o '%s'",
        key, afile, text:gsub("'", "'\\''"), dst))
      if not ok then
        -- Fallback to local CTC aligner
        local abin = cadence_audio_bin()
        sh(string.format("%s align --file %q --text %q --out %q", abin, afile, text, dst))
      end
    else
      -- Run local CTC forced alignment directly
      local abin = cadence_audio_bin()
      local ok, out = sh(string.format("%s align --file %q --text %q --out %q", abin, afile, text, dst))
      if not ok then error("cadence resolve: local alignment failed:\n" .. out) end
    end
  end
  local f = assert(_ELLUA_IOOPEN(dst, "rb"), "cadence resolve: alignment unreadable")
  local raw = f:read("*a"); f:close()
  local doc = require("cadence.json").decode(raw)
  local words = {}
  for _, w in ipairs(doc.words or {}) do
    local clean = (w.text or ""):gsub("^%s+", ""):gsub("%s+$", "")
    if clean ~= "" then
      words[#words + 1] = { text = clean, t0 = w.start or w.t0, t1 = w["end"] or w.t1 }
    end
  end
  return words, doc.phonemes
end

function R.media(nodes, fps)
  local decode = require("decode")
  for _, n in ipairs(nodes) do
    if n.kind == "video" and decode.available then
      -- fast path: in-process FFI decoder, no extraction, frame-exact by PTS.
      -- yuv420 sources come back as raw planes (GPU converts); others as RGBA.
      local i = n.initial
      n.dec = decode.open(localize(i.src), i.w, i.h)
      if i.cursor_src then n.cursor_file = localize(i.cursor_src) end
    elseif n.kind == "video" then
      local i = n.initial
      local file = localize(i.src)
      local key = sha1(table.concat({ i.src, i.media_start, i.duration, fps, i.w, i.h }, "|"))
      local dir = cache_root() .. "/frames/" .. key
      if not exists(dir .. "/DONE") then
        assert(sh(("mkdir -p '%s'"):format(dir)))
        local cmd = string.format(
          "ffmpeg -hide_banner -loglevel error -y -i '%s' -ss %f -t %f" ..
          " -vf 'fps=%d,scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d'" ..
          " -q:v 2 '%s/%%05d.jpg'",
          file, i.media_start, i.duration, fps, i.w, i.h, i.w, i.h, dir)
        local ok, out = sh(cmd)
        if not ok then error("ellua resolve: frame extraction failed:\n" .. out) end
        assert(sh(("touch '%s/DONE'"):format(dir)))
      end
      local _, count = sh(("ls '%s' | grep -c jpg"):format(dir))
      n.frames_dir = dir
      n.frame_count = tonumber(count:match("%d+")) or 0
      if n.frame_count == 0 then error("ellua resolve: no frames extracted for " .. i.src) end
    elseif n.kind == "image" then
      n.file = localize(n.initial.src)
      if n.initial.cursor_src then n.cursor_file = localize(n.initial.cursor_src) end
      if not exists(n.file) then error("ellua resolve: image not found: " .. n.initial.src) end
    elseif n.kind == "lottie" then
      n.file = localize(n.initial.src)
      if not exists(n.file) then error("ellua resolve: lottie not found: " .. n.initial.src) end
    elseif n.kind == "mesh" and n.initial.src then
      n.file = localize(n.initial.src)
      if not exists(n.file) then error("ellua resolve: mesh not found: " .. n.initial.src) end
      local scene3d = require("scene3d")
      if scene3d.available then
        n.mesh_id = scene3d.load(n.file)
        if not n.mesh_id or n.mesh_id == 0 then
          error("ellua resolve: glTF load failed: " .. n.file)
        end
      end
    elseif n.kind == "audio" or n.kind == "tts" or n.kind == "sfx" or n.kind == "music" then
      local i = n.initial
      if n.kind == "audio" then
        n.afile = localize(i.src)
        if not exists(n.afile) then error("ellua resolve: audio not found: " .. i.src) end
      elseif n.kind == "tts" then
        -- auto request-stitching: a line chained to an earlier TTS clip inherits
        -- it as previous_text, so consecutive lines read as one narration
        local ref = (i.after and (i.after[1] or i.after.node)) or (i.at_word and i.at_word[1])
        if ref and ref.kind == "tts" and i.previous_text == nil and ref.initial.text then
          i.previous_text = ref.initial.text
        end
        n.afile = tts_generate(i, (i.align or i.align_at or i.at_word) and true or false)
      elseif n.kind == "music" then
        n.afile = music_generate(i)
      else
        n.afile = sfx_generate(i)
      end
      n.media_duration = ffprobe_duration(n.afile)
      -- default clip window = whole file; scripts can read i.duration after resolve
      i.duration = i.duration or (n.media_duration - i.media_start)
      -- sequential chaining: after = {ref_node, gap} places this clip when the
      -- referenced (earlier-created) clip ends. Resolution is in creation order,
      -- so the ref's at/duration are already final.
      if i.after then
        local ref = i.after[1] or i.after.node
        local gap = i.after[2] or i.after.gap or 0
        assert(ref and ref.initial and ref.initial.duration,
          "ellua: audio after={ref} must reference an earlier audio node")
        i.at = ref.initial.at + ref.initial.duration + gap
      end
      -- Forced alignment. Runs before the align_* scheduling props below, which
      -- need word offsets to place the clip at all.
      if (i.align or i.align_at or i.at_word) and (n.kind == "tts" or n.kind == "audio") then
        local words, phonemes = align_generate(n.afile, i.align_text or i.text or "")
        n.words = words
        n.phonemes = phonemes
      end
      -- align_at = { word = "Drop", at = 1.57 } OR { event = "beat", at = ... }
      if i.align_at then
        local target = i.align_at.at or i.align_at[2]
        if type(target) == "table" and target.initial then
          target = target.initial.at or 0
        end
        if i.align_at.word or i.align_at[1] then
          local want = normword(i.align_at.word or i.align_at[1])
          local hit
          for _, w in ipairs(n.words or {}) do
            if normword(w.text) == want then hit = w break end
          end
          if not hit then
            error(("cadence resolve: align_at word %q not spoken in %q"):format(
              i.align_at.word or i.align_at[1], i.text or ""), 0)
          end
          i.at = target - hit.t0
        elseif i.align_at.event then
          local want = (i.align_at.event):lower()
          local hit
          for _, ev in ipairs(n.events or {}) do
            if (ev.name and ev.name:lower() == want) or (ev.type and ev.type:lower() == want) then
              hit = ev break
            end
          end
          if hit then
            i.at = target - hit.t0
          end
        end
        if i.at and i.at < 0 then
          io.stderr:write(("cadence: align_at pulled %q to %.2fs (before zero) — clamped\n"):format(i.text or "?", i.at))
          i.at = 0
        end
      end
      -- at_word = { ref_node, "languages", gap } — start when another aligned
      -- clip finishes saying a given word.
      if i.at_word then
        local ref = i.at_word[1]
        assert(ref and ref.words, "ellua: at_word needs an earlier aligned node")
        local want, hit = normword(i.at_word[2]), nil
        for _, w in ipairs(ref.words) do
          if normword(w.text) == want then hit = w break end
        end
        assert(hit, "ellua: at_word word not found: " .. tostring(i.at_word[2]))
        i.at = ref.initial.at + hit.t1 + (i.at_word[3] or 0)
      end
      if i.follow then
        i.energy = bake_energy(n.afile, i.media_start or 0, i.duration)
      end
    elseif n.kind == "html" then
      if n.initial.src and not n.initial.html then
        local f = assert(_ELLUA_IOOPEN(localize(n.initial.src), "r"),
          "ellua resolve: html src not found: " .. n.initial.src)
        n.initial.html = f:read("*a")
        f:close()
      end
    elseif n.kind == "page" then
      -- Fetch HTML + subresources here, then cache a viewport PNG. Painter is
      -- a static image draw — no network and no JS in evaluate(t).
      local html = require("html")
      if not html.available then
        error("ellua resolve: html dylib missing (build html/)")
      end
      local i = n.initial
      local src = i.src
      local base = i.base
      if src and src:match("^https?://") then
        base = base or src
        if not i.html then
          local f = assert(_ELLUA_IOOPEN(localize(src), "r"),
            "ellua resolve: page src not found: " .. src)
          i.html = f:read("*a")
          f:close()
        end
      elseif src then
        local path = localize(src)
        if not i.html then
          local f = assert(_ELLUA_IOOPEN(path, "r"),
            "ellua resolve: page src not found: " .. src)
          i.html = f:read("*a")
          f:close()
        end
        base = base or path
      end
      assert(i.html, "ellua resolve: page{html=} or page{src=} required")
      local w, h = i.w, i.h
      local png = cache_root() .. "/page/" .. sha1(table.concat({
        i.html, tostring(base or ""), tostring(w), tostring(h),
        i.bake == false and "0" or "1",
      }, "|")) .. ".png"
      if not exists(png) then
        assert(sh(("mkdir -p '%s/page'"):format(cache_root())))
        html.render_page_png(i.html, base or "", w, h, 1.0, png, i.bake ~= false)
      end
      n.file = png
    elseif n.kind == "svg" then
      local i = n.initial
      local src = localize(i.src)
      local png = cache_root() .. "/svg/" .. sha1(table.concat({ i.src, i.w, i.h }, "|")) .. ".png"
      if not exists(png) then
        assert(sh(("mkdir -p '%s/svg'"):format(cache_root())))
        local ok, out = sh(("resvg '%s' '%s' --width %d --height %d"):format(src, png, i.w, i.h))
        if not ok then error("ellua resolve: resvg failed:\n" .. out) end
      end
      n.file = png
    elseif n.kind == "spritesheet" then
      local i = n.initial
      local src = localize(i.src)
      local json_path = i.json and localize(i.json) or (src:match("%.json$") and src)
      if json_path then
        local f = assert(_ELLUA_IOOPEN(json_path, "r"),
          "ellua resolve: spritesheet json not found: " .. json_path)
        local raw = f:read("*a")
        f:close()
        local data = require("ellua.json").decode(raw)
        local frames = {}
        if data.frames[1] then
          for _, fr in ipairs(data.frames) do
            frames[#frames + 1] = {
              x = fr.frame.x, y = fr.frame.y, w = fr.frame.w, h = fr.frame.h,
              duration = (fr.duration or 100) / 1000,
            }
          end
        else
          local list = {}
          for name, fr in pairs(data.frames) do
            list[#list + 1] = { name = name, fr = fr }
          end
          table.sort(list, function(a, b) return a.name < b.name end)
          for _, item in ipairs(list) do
            local fr = item.fr
            frames[#frames + 1] = {
              x = fr.frame.x, y = fr.frame.y, w = fr.frame.w, h = fr.frame.h,
              duration = (fr.duration or 100) / 1000,
            }
          end
        end
        assert(#frames > 0, "ellua resolve: spritesheet json has no frames")
        n.sheet = { frames = frames }
        if src:match("%.json$") then
          local img = i.image or (data.meta and data.meta.image)
          assert(img, "ellua resolve: spritesheet json missing meta.image")
          local dir = json_path:match("^(.*)/") or "."
          n.file = img:match("^/") and img or (dir .. "/" .. img)
        else
          n.file = src
        end
      else
        n.file = src
        n.sheet = { grid = true, cols = i.cols, rows = i.rows }
      end
      if not exists(n.file) then
        error("ellua resolve: spritesheet image not found: " .. n.file)
      end
    elseif n.kind == "displace" then
      if n.initial.src then
        n.file = localize(n.initial.src)
        if not exists(n.file) then
          error("ellua resolve: displace image not found: " .. n.initial.src)
        end
      end
    elseif n.kind == "text" and n.initial.src and not n.initial.cues then
      local path = localize(n.initial.src)
      local f = assert(_ELLUA_IOOPEN(path, "r"), "ellua resolve: captions src not found: " .. n.initial.src)
      local raw = f:read("*a")
      f:close()
      local cap = require("ellua.captions")
      if path:match("%.csv$") then
        n.initial.cues = cap.normalize(cap.parse_csv(raw))
      else
        n.initial.cues = cap.normalize(cap.parse_srt(raw))
      end
    elseif n.kind == "spine" and n.initial.src and not n.initial.skeleton then
      local path = localize(n.initial.src)
      local f = assert(_ELLUA_IOOPEN(path, "r"), "ellua resolve: spine src not found: " .. n.initial.src)
      local raw = f:read("*a")
      f:close()
      n.initial.skeleton = require("ellua.json").decode(raw)
    end
  end
end

return R
