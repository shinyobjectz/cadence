# ElevenLabs × ellua — audio bridge

The vendored ElevenLabs skills live at `skills/vendor/elevenlabs-skills/`
(upstream https://github.com/elevenlabs/skills, MIT, unmodified). This doc maps
which of them matter for ellua and how to combine them **today**, honestly:

**Status (updated 2026-08-01): the audio phase is BUILT.** The engine still
never plays audio (canon invariant 5) — clips resolve pre-render and mix via
ffmpeg `filter_complex` at encode. Live node API:

- `s:audio{ src, at, duration, media_start, volume, fade_in, fade_out }` — local file or URL
- `s:tts{ text, voice="sarah", at, volume, align, align_at, at_word, seed, speed,
  stability, style, previous_text, next_text, dictionaries, model }` — ElevenLabs
  TTS, content-hash cached; **`node.initial.duration` is filled before scripts
  run**, so scripts pace scenes to narration
  (`t:tween(bar, vo.initial.duration, ...)`). See forced alignment and
  request-level features below.
- `s:sfx{ prompt, gen_duration, at, volume }` — generated effects; also good for
  musical beds/pads on free-tier keys. **`gen_duration` minimum is 0.5s** — the
  API returns a bare 400 below that, so ellua raises a named error instead
- `s:music{ prompt, gen_duration, at, volume }` — Eleven Music; **requires a paid
  ElevenLabs plan** (free keys get a clear 402 error; fall back to `s:sfx` pads)

Free-tier voice gotcha: library voices 402 via API — the built-in name map uses
premade voices (sarah/roger/george/alice/liam/laura); raw voice_ids pass through.
Working example: `examples/narrated_test.lua`, `examples/kinetic_promo.lua`.
Word-sync IS implemented — see forced alignment below.

## Which vendored skills matter

| vendored skill | ellua relevance | today | future (resolve phase) |
|---|---|---|---|
| `text-to-speech` | **high** — narration/VO | generate VO mp3, probe duration, author comp to it, mux | TTS at resolve; clip duration known pre-render; drives comp timing |
| `speech-to-text` | **high** — word timestamps (Scribe) | transcribe the generated VO with `timestamps_granularity="word"`; hand-place caption tweens at word starts | `waitUntil('word:…')` sync + karaoke captions for free |
| `sound-effects` | medium — hits/whooshes/ambience | generate, place at offsets via `adelay` in the mux | SFX events bound to timeline markers |
| `music` | medium — bed track | `music_length_ms = duration*1000`, generate, mux under VO | resolve-phase bed + volume envelopes (ducking) |
| `voice-isolator` | low — clean up source-footage audio before reuse | as-is | — |
| `voice-changer` | low — re-voice an existing recording | as-is | — |
| `setup-api-key` | utility | run it if `ELEVENLABS_API_KEY` is missing | — |
| `agents`, `speech-engine` | not relevant to rendering (realtime conversational voice) | — | — |

All require `ELEVENLABS_API_KEY` and network. That is fine: audio generation is
resolve-phase-shaped work (network allowed), and it happens *outside* the
deterministic render either way.

## Request-level features — pick by need

The TTS request itself carries most of what people bolt on manually. Map a need
to its feature; everything below participates in the content-hash cache key.

| you need | use | how |
|---|---|---|
| consecutive lines that sound like ONE narrator (no cold-open prosody per clip) | **request stitching** | automatic: a line chained with `after=` or `at_word=` to an earlier `s:tts` inherits it as `previous_text`; set `previous_text`/`next_text` explicitly for manual chains |
| the same audio back when you regenerate (determinism beyond the cache) | **`seed = 42`** | same seed + text + voice + model → reproducible generation; pairs with ellua's byte-identical renders |
| narration that FITS a fixed scene window (instead of stretching the scene) | **`speed = 0.7–1.2`** | `voice_settings.speed`; generate, check `initial.duration`, nudge speed — two-way fit instead of one-way |
| a calmer / more expressive read | **`stability` / `style`** | passthrough to `voice_settings` |
| product names said right, every render | **`dictionaries = {{id=, version_id=}}`** | `pronunciation_dictionary_locators` on the request; create the dictionary once in the ElevenLabs dashboard/API |
| fast cheap iteration renders, full quality finals | **`ELLUA_DRAFT=1`** | un-pinned models swap to `eleven_flash_v2_5`; final render without the flag uses `eleven_multilingual_v2`; separate cache entries |
| expressive audio-tag delivery (`[excited]`, `[whispers]`) | **`model = "eleven_v3"`** | tags inline in `text`; per-platform emotional variants of the same line |

## Forced alignment — native (`align`, `align_at`, `at_word`)

Word timings now arrive **with the audio**: any `s:tts` that needs alignment
(`align`, `align_at`, or `at_word`) generates through
`/v1/text-to-speech/{voice}/with-timestamps`, and the character alignment in the
response is grouped into word times — **zero extra API calls**. The separate
`POST /v1/forced-alignment` endpoint is still used for audio ellua didn't
generate (`s:audio{ align_text = ... }`). Both cache next to the audio as
`<sha>.align.json`, so re-renders cost nothing.

```lua
-- read word times off a clip
local vo = s:tts { text = "Pick from over ninety languages.", voice = "sarah",
                   at = 0.5, align = true }
vo:word("ninety")        -- comp-absolute start  (1.279)
vo:word_end("languages") -- comp-absolute end
vo:word_times()          -- { {text=, t0=, t1=}, ... }, comp-absolute
```

**`align_at` is the one that matters.** The visual beat is fixed by the
footage; the narration slides itself so a chosen WORD lands on that frame:

```lua
s:tts { text = "Drop in any video.", voice = "sarah",
        align_at = { word = "Drop", at = T_UPLOAD } }
```

resolve measures where "Drop" falls inside the clip and sets `at = T_UPLOAD −
offset`. Reword the line, swap the voice, or re-record at a different aspect
(which changes cursor travel and therefore every marker time) and the sync still
holds, because the offset is **measured, not typed**. Verified: markers
upload/picker/submit = 1.70/3.37/7.17 → the three lines land their words on
1.70/3.37/7.17 exactly.

`at_word = { ref_node, "languages", gap }` schedules a clip to start when an
earlier aligned clip finishes saying a word.

Division of labour: **visuals deterministic** (recorder markers), **narration
aligned onto them**. Do not try to drive frame-exact audio grids off word times
— audio `at` is fixed at node creation, before alignment runs.

Matching is case- and punctuation-insensitive; pass an `nth` to disambiguate a
repeated word. Missing word = hard error naming the line, never a silent slip.

## Today's workflow: audio-first authoring

Timing must come from the audio, not the other way around — you cannot stretch
a comp to fit audio at render time (duration is static). So:

```bash
# 1. generate narration (see vendor/elevenlabs-skills/text-to-speech/SKILL.md)
#    eleven_v3 for quality; save vo.mp3
# 2. probe its true duration — THIS drives comp.duration
ffprobe -v error -show_entries format=duration -of csv=p=0 vo.mp3
# e.g. 7.432 → author comp with duration = 7.5 (round UP a touch)

# 3. word timestamps: prefer native align/align_at above (forced alignment).
#    Scribe (speech-to-text, timestamps_granularity="word") is for audio you did
#    NOT generate and have no transcript for.

# 4. render video (silent by design)
bin/ellua render comp.lua -o video.mp4

# 5. mux — video stream copied untouched, audio encoded
ffmpeg -y -i video.mp4 -i vo.mp3 \
  -c:v copy -c:a aac -b:a 192k -shortest final.mp4
```

Multiple clips at offsets (VO at 0s, SFX at 2.4s, music bed ducked):

```bash
ffmpeg -y -i video.mp4 -i vo.mp3 -i whoosh.mp3 -i music.mp3 -filter_complex "\
[1:a]adelay=0|0[vo];\
[2:a]adelay=2400|2400[sfx];\
[3:a]volume=0.25[bed];\
[vo][sfx][bed]amix=inputs=3:duration=first:normalize=0[mix]" \
  -map 0:v -map "[mix]" -c:v copy -c:a aac -b:a 192k final.mp4
```

Notes:
- `adelay` takes **milliseconds**, one value per channel.
- `-c:v copy` always — never re-encode the rendered video in the mux step.
- Verify the result: `ffprobe -v error -show_entries stream=codec_type,duration
  -of default=noprint_wrappers=1 final.mp4` (audio stream present, durations sane),
  then spot-listen or check waveform if the environment allows.
- Caption sync check: extract frames at a few word timestamps
  (`ffmpeg -ss <word.start> -i final.mp4 -frames:v 1 f.png`) and confirm the
  caption on screen matches the word being spoken.

## Caching / cost discipline

The future resolve phase content-hashes (script, voice, model) → zero API calls
on re-render (tests/PLAN.md R1). Emulate that manually today: keep generated
audio files next to the project (or under `~/.cache/ellua/`), name them by
content (e.g. `vo-<sha1-of-text-and-voice>.mp3`), and regenerate only when the
script text or voice changes — TTS calls cost money and add latency.

## Landed vs remaining

LANDED (2026-08-01): resolve-phase TTS/SFX/Music nodes, durations known before
scripts, three-pass encode (video → filter_complex mix → `-c copy` mux). The
manual mux workflow above is now **legacy** — use it only for endpoints ellua
doesn't wrap yet (dubbing, dialogue, alignment). REMAINING: `waitUntil('word:…')`
word-sync via `/with-timestamps`, volume-envelope tweens (ducking), music
composition-plan → scene-marker mapping.

## Full API surface audit (2026-08-01) — beyond the vendored skills

Endpoint goldmines for ellua not yet integrated (roadmap order):
1. `POST /v1/text-to-speech/{voice}/with-timestamps` — char-level timing with the
   audio in one call → karaoke captions + `waitUntil('word:…')` without any STT pass.
2. `POST /v1/forced-alignment` — audio + transcript → word times (for user-supplied VO).
3. `POST /v1/text-to-dialogue` — Eleven v3 multi-speaker + `[audio tags]` ([laughs],
   [whispers]) → two-voice explainers from one call.
4. `POST /v1/music/composition-plan` + `POST /v1/music` with plans — structured
   sections (intro/verse/drop) that could map 1:1 to comp scene markers.
5. `POST /v1/music/video-to-music` — score generated FROM the rendered video.
6. Scribe batch accepts `source_url` (incl. YouTube/TikTok) — captioning found footage.

Vendored-skills coverage gaps (upstream repo has no skill for these):
**dubbing** (full Dubbing Studio API), **forced-alignment**, **text-to-dialogue**,
**voice-design/remix** (`/v1/text-to-voice/*`), **pronunciation dictionaries**,
**Studio projects**. Write local reference notes before using any of them.

**Image & Video generation: product exists, API does not.** ElevenLabs "Image &
Video" (Nov 2025 beta) is an aggregator UI over third-party models (Veo, Sora,
Kling, Seedance, Runway, FLUX...). Zero public endpoints; "ElevenCreative Studio
API" is sales-gated, programmatic access "planned". Do not promise API-driven
video/image gen through ElevenLabs; ellua's media generation story stays local
(render) + other providers.

Tier gates seen live on a free key: TTS premade-voices-only (library voices 402),
Music API 402, SFX/STT work. API-plan tiers exist separately (API Free/Pro/Scale).

## ElevenCreative platform APIs — the interop map

Beyond the request-level endpoints, the ElevenCreative platform exposes
project-shaped APIs. None are wired into ellua yet; this is the map of what
each one would buy, in priority order:

| platform API | what it is | what ellua would do with it |
|---|---|---|
| **Music composition plans** (`create composition plan` → `compose music with details`) | structured music: sections, styles, durations — not just a prompt | **music that hits markers the way VO hits words**: build the plan from comp markers so the drop lands on the reveal frame. The audio twin of `align_at`. |
| **Text to Dialogue (with timestamps)** | multi-speaker scenes, one call, per-word timing | two-voice product tours and support-call reenactments with the same word-sync guarantees as single-voice TTS |
| **Studio projects** (create/chapters/convert/snapshots; API access is sales-gated) | the human review loop: comments, locks, per-paragraph regens, versioned snapshots | interop, not replacement: push ellua VO scripts into a Studio project for a marketing team to review/approve, pull the approved chapter audio back into the comp. Compiled pipeline + human sign-off. |
| **Dubbing resource API** (segments, speakers, languages) | fine-grained programmatic dubs | the localization multiplier: per-segment control when re-rendering one comp into N languages |
| **Music Finetunes** | finetune the music model on your own sound | a *brand-sound* bed generator — every comp's music comes from the brand's own sonic identity |
| **Audio Native** | embeddable narrated-page player | narrate the docs/case-study pages themselves; zero render work |
| **Voice Design** | describe a voice into existence | one distinctive project narrator per brand instead of stock presets |

Rule of thumb: anything that produces an asset is resolve-phase-shaped and slots
in without touching the renderer. Anything interactive (Agents, realtime) targets
the LÖVE runtime instead — a different integration lane entirely.
