-- smoke: caption cue builders (phrase / line / word)
local C = require("cadence.captions")

local words = {
  { text = "Meet", t0 = 0, t1 = 0.3 },
  { text = "Cadence.", t0 = 0.3, t1 = 0.7 },
  { text = "The", t0 = 0.9, t1 = 1.0 },
  { text = "fastest", t0 = 1.0, t1 = 1.4 },
}

local phrase = C.from_words(words, { mode = "phrase", window = 3 })
assert(#phrase == 4)
assert(phrase[2].text == "Meet Cadence.")
assert(phrase[4].text == "Cadence. The fastest")

local line = C.from_lines({
  { id = "a", words = { words[1], words[2] } },
  { id = "b", words = { words[3], words[4] } },
}, { mode = "line" })
assert(#line == 2)
assert(line[1].text == "Meet Cadence.")

local tailed = C.apply_tail(phrase, 0.7, 0.3)
assert(tailed[#tailed].text == "")
assert(tailed[#tailed].t0 == 0.7)

print("captions_from_words ok")
