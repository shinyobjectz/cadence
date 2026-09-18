# The fact log — one grammar for what a comp is and what a clip shows

Status: built 2026-09-17, `vision/cadence_vision/{facts,lower,perceive,…}.py`. This is the layer
between Cadence and an editing model. It grew out of the vision MCP (`docs/VISION.md`), which
answers *how do we show a model a frame*; this answers the question after it — **what does the model
read, and how does what it says come back as Lua.**

## Why not just show the model the video

Every measurement says frontier models cannot edit video from pixels. On VEBench, temporal IoU for
localizing an edit sits between 0.00 and 0.11. On AgenticVBench, the best agent scores 31% where
humans score 81–95%. Our own small eval agrees in shape: on five stock clips, five models got
81/88 questions right, and every failure clustered in the same three places — telling direction
across time (who handed what to whom), reading orientation from a single frame, and judging depth
where the shot has a shallow focus plane.

The failures are not a vocabulary problem. Models describe these clips fluently and wrongly. What
is missing is a representation with *times and identities in it*, which is exactly what a renderer
has and a caption does not.

So: do not ask a model to see. Measure what can be measured, write it in a grammar the model can
read and write, and lower its edits back to source.

## The grammar

Three predicates, event calculus, and nothing else:

```prolog
holds(Fluent, T0, T1).        % something true over an interval
happens(Event, T).            % something that occurred at an instant
src(Fact, Producer, Conf).    % who measured that fact, and how sure the measurement was
```

**A fact with no `src` line is exact.** That single rule carries the whole design. A comp knows its
own truth — node ids, boxes, z-order, tween curves, cue times — so the lifter states it flatly. A
clip is only ever measured, so every perceived fact names its producer and its confidence. A reader
never has to ask which kind it is holding.

```prolog
% lifted from motion.lua — exact, no provenance
entity(circle2, "circle", node("circle2")).
holds(motion(circle2, dir(right), fast, ease(quadIn)), -0.025, 0.692).
holds(in_third(circle2, center), 0.633, 1.167).

% perceived from woman_9032610.mp4 — every line carries its source
entity(e3, "camera", seed(3.240)).
src(entity(e3, "camera", seed(3.240)), vlm_agreement, 0.98).
holds(out_of_focus(e3), 0.000, 6.480).
src(holds(out_of_focus(e3), 0.000, 6.480), laplacian, 0.80).
```

### The vocabulary is words, not numbers

Times are the only numbers in the log, because an edit is a thing you do at a time. Everything else
is a term an editor would say out loud: `in_third(e, left)`, `in_band(e, lower)`,
`motion(e, dir(right), fast)`, `shot_scale(e, mcu)`, `camera(dolly(in))`, `facing(e, away)`,
`approaching(e)`, `speaking(e)`, `action_boundary(s1)`, `same_as(e4, e1)`. Ease curves come back as their names — `ease(quadIn)` — because that is what the
source says and what an edit must write back.

This was a deliberate reversal. A first version emitted coordinates and magnitudes, and reading it
back, a model could describe the numbers and still not know what to change. Positions became
thirds, velocities became adverbs, and the log became editable.

## Two sides, one grammar

| | comp (`facts.lift`) | footage (`perceive.perceive`) |
|---|---|---|
| entities | one per node, id is the node id | one per track, `seed(t)` records where it was found |
| truth | exact from the scene graph | measured, every line with `src` |
| motion | fitted to the actual tween, ease name and sub-frame endpoints | regression over a camera-compensated centroid |
| text | the string the comp drew | OCR, with the boundary bisected |
| depth | z-order | DA3 relief, cross-checked against focus |

Because the two sides share a grammar, a comp that composites a clip produces one log describing
both, and a producer can be measured against exact truth before it is trusted on footage. That is
the discipline the whole suite is built on: **no producer is believed on a clip until it has been
checked on a comp.**

## Lowering: the log edits the source

`lower.lower(comp, edits)` maps node ids back to their constructor spans in the Lua and rewrites
fields in place. Verbs: `set_prop`, `set_ease`, `set_tween_duration`, `set_cue`.

Two properties make it safe. With an empty edit list it returns the source byte-for-byte. And
because Cadence renders deterministically, an edit can be verified by frame hashes rather than by
eye — retiming one caption cue from 0.55 s to 0.85 s changed exactly nine frames, 0.567–0.833 s, and
left the other ninety-nine identical.

Node ids are matched positionally against the constructors, not by name, because a constructor and
the node it builds need not share one: `s:captions{}` builds a *text* node whose id is `text6`.

## The agent surface: query, anchor, edit

Built 2026-09-18. Until then the fact layer was library code with tests: `facts.lift`,
`perceive.perceive` and `lower.lower` existed, nothing called them, and there was no way to
ask a log a question. `cadence-agent query` is unrelated — it is embedding search over a
`.npz` probe sidecar, not the grammar.

Four MCP tools now carry it, which also makes them shell commands
(`bin/cadence-vision call <tool> k=v`):

| tool | does |
|---|---|
| `fact_log(source, words, beats)` | lift a comp or perceive a clip; cached on size+mtime |
| `fact_query(pattern, sources, min_conf)` | pattern-match one or many logs |
| `fact_when(source, word, event)` | the time something was said, or a beat/onset |
| `fact_edit(comp, edits, verify, write)` | rewrite a comp from an assertion, and prove it |

### A query is a fact with holes in it

`vision/cadence_vision/query.py`. There is no second language: a capitalised atom binds, `_`
matches anything, a quoted string is always a literal.

```prolog
happens(release(A, B), T)          % every handover, and who did it to whom
holds(camera(M), T0, T1)           % every camera move
holds(text(E, "TYPE"), T0, T1)     % "TYPE" is a literal, not a variable named TYPE
```

Three rules earn their keep. `src` lines never come back as results — they are metadata about
another fact, and a query for `_` that returned them would double every answer. `min_conf`
filters perceived hits and **never** drops exact ones, because an exact fact has no confidence
at all and filtering on confidence must not become a way to hide what is certain. And
`holds(F, T0, T1)` is half-open, so abutting intervals never both match at their shared edge.

### Editing by what was said

The comp side used to carry only `says(nid, "the whole sentence")`, which is too coarse to
anchor to. `lift(want_words=True)` force-aligns any audio node that declares its own `text`
and emits `happens(word(...))`; `want_beats=True` does tempo, beats and onsets. Both are off by
default because both cost a pass, and **both carry `src`** — a word timing is measured by the
aligner even inside an otherwise exact log, and the rule that a fact without `src` is exact
outranks the convention that a lifted log has none. The header says so when they are on.

So an agent edits by speech in two steps, neither of which involves looking at the video:

```
fact_when  source=said.lua word=Programmatic     -> {"times": [1.445]}
fact_edit  comp=said.lua edits='[{"verb":"set_cue","node":"text2","index":0,"t0":1.445}]' verify=frames
           -> 41 frames changed, contiguous, 0.100-1.433s
```

The same query works on footage, where the words come from `whisper_then_align` instead of
`forced_align` — ASR discovers the words, alignment times them, because ASR timestamps are
~250 ms out and alignment ~15 ms.

### Propose, then commit

`fact_edit` does not write unless asked. `write` defaults to False, so an agent proposes,
reads what the edit would do, and commits separately. Three verify levels: `none` rewrites,
`facts` lifts before and after and diffs the logs, `frames` also renders both and hashes every
frame — the only level that can prove an edit touched nothing else.

A malformed edit is refused with a reason rather than half-applied, and validation is a dry run
of the real lowering rather than a second copy of its rules, so the two cannot drift:

```
{"ok": false, "problems": ["IndexError: rect4 has 0 cues, asked for #0"]}
{"ok": false, "problems": ["edit 0: unknown verb 'teleport' (have set_cue, set_ease, set_prop, set_tween_duration)"]}
```

### A bug this surfaced

`props.lua` evaluates a comp *without* the resolve phase, so an audio node that never declared
`duration` has none — resolve is what fills it in from the media. The lifter fell back to the
comp's duration, so all eleven narration clips of the lesson comp lifted as playing until the
end of a 160 s comp, simultaneously. Fixed by probing the media (exact, so no `src`), clamped
to the comp; regression tests cover the media-length, `media_start` and clamp branches.

Coverage: `vision/tests/test_query.py` (15), `test_edits.py` (14), `test_transcript.py` (15).

## Producers, and what each was measured at

| goal | producer | measured |
|---|---|---|
| G0 | comp lifter | 43/43 eval cases lift and parse, 3956 facts, no `src` anywhere; identity lowering reproduces all 108 frame hashes |
| G1 | forced alignment + onset refinement | **17 of 17 words inside one frame** across two constructed comps (worst 16.0 ms, against 33.3 ms for a frame at 30 fps), and clip starts exact. Forced alignment alone got 15 of 17: CTC acoustic models emit late, so a word-initial sonorant is placed near the following vowel rather than at the consonant. That is not a model-quality problem — MMS_FA, wav2vec2 base and large, wav2vec2-lv60k and HuBERT-large all put "land" within 0–5 ms of each other at +50 ms, and the **largest model was the worst** (+114 ms on another word); every one of them is CTC. Snapping each boundary back to an independent energy onset removes the bias: stop and fricative onsets barely move (`past` −7 ms, `quietly` −9 ms), sonorants move 20–50 ms (`rain` +19→−3, `window` +30→−7, `yes` +45→−3). Held out properly — the constants were fixed on comp A and comp B, ten fresh words chosen for sonorant onsets, was then measured once at 10/10 |
| G1 | audio-visual correlation | in-sync vs out-of-sync margin 0.054 raw → 0.356 with 0.25 s smoothing |
| G1 | per-turn active speaker | on material where one shape moves on each word and another only in the gaps, every speech turn is attributed to the first and none to the second |
| G1 | diarization (ECAPA clustering) | 0.000 % speaker confusion over the 11.0 s of a four-turn two-speaker assembly; the same producer emits nothing for one speaker reading for 29 s, and nothing for two voices it cannot separate |
| G2 | DA3 intrinsics, signed focal trend | **dolly and zoom distinguished on a real test pair**, with two static controls: `zoom(in)` on a true zoom, `dolly(in)` on the man clip, `still` on both controls. Focal ratios 1.22 / 0.92 / 1.00, `focal_rise` +0.14 / −0.06 / +0.01. Without intrinsics a scale change is now `unknown`, never `dolly` — the second clause, which the code had been failing |
| G3 | camera-compensated motion | three parked cars stay scenery under a dolly; cat clip byte-identical |
| G4 | VLM cross-model grounding | an out-of-focus tripod camera no detector could seed: two models agreed at IoU 0.98, SAM 2 then tracked it 0.14–6.34 s (63 samples) |
| G5 | YOLO pose read as states | `facing` correct on a back view (`away`) and a piece to camera (`camera`) |
| G5 | MediaPipe hands via ONNX | palm detection 0.91–0.99 with landmarks on the hand (woman clip). On the handoff clip's gloved backlit hands it fails, and re-measured carefully it fails in a more specific way than "nothing is detected": on a right-half crop with the threshold dropped to 0.2 it fires on **every** frame at 0.52–0.68, but drawing the boxes shows they sit on the bare **forearm**, not the hand. Handpose then scores 0.004–0.43 against its own 0.5 gate on those boxes, i.e. it declines to landmark them. Two producers, both silent for the right reason (X2). The score alone would have read as success — the same trap as the 591×85 px "hand" earlier — so it was the picture that settled it, not the number |
| G5 | contact-break from native-resolution masks | release comp (truth exact) 2.44 against 2.400, **1.0 frame**; handoff clip 8.50 against a re-derived 8.42, **2.0 frames**. The alternatives on the same clip: box separation 0.22–0.32 s out, mask contact area at 10 fps has no event in it at all, frame-change peak 0.18 s out — it peaks where motion is fastest, which is after the hand has gone |
| G5 | palm orientation from the knuckle winding | `palm(e, camera\|away\|edge)` decided by the sign of the cross product of the two knuckle vectors, which flips with handedness; measured on constructed hands, since no clip here shows a palm presented to the lens |
| G8 | head pose from the same keypoints | `facing(e, camera)` rather than a separate `looking_at`: one fluent that also says `left`, `right` and `away`, instead of a synonym for one of its values |
| G6 | RapidOCR + bisected boundaries | three caption cues recovered verbatim; changes at 0.528 / 1.434 / 2.434 s against 0.55 / 1.45 / 2.45 — 22, 16 and 16 ms, all inside one frame. The round-trip closes with the lowering: retiming a cue moves only the frames between the old and new times |
| G7 | cross-shot identity from CLIP appearance | confidence is the pair's percentile against same-frame pairs, which cannot be one object; on the three-shot assembly the man in shot 1 (seed 2.400 s) and in shot 3 (seed 8.000 s) are rejoined across two cuts — `same_as(e5, e1)` at 1.00 — at different scale and stride. For the colour half, hue-rotating his second shot by 120° (navy jacket to maroon, and the street with it) still gives `same_as(e3, e1)` at 0.86; the appearance cosine falls only 0.915 → 0.873 across a full rotation against a 0.764 different-object baseline |
| G9 | shot scale from mask height | `mcu` on both the man and the woman clip |
| G10 | Laplacian sharpness vs DA3 | 0.17 against 3.16 between a soft and a sharp subject; the wrong `nearer` fact is now not emitted |
| G11 | self-similarity novelty | one boundary per clip, each where the other producers put the action: the cat's hop (2.500 s, against `motion` 2.0–3.0) and the bottle's arrival and departure (1.500 / 10.000 s, against `enter` 2.1 and `exit` 11.0) |

### What perception costs, and the tracker swap (2026-09-18)

Measured on a base M4 (16 GB) with `CADENCE_PROFILE=1 tools/bench_perceive.py`, cold, on the handoff
clip (14.3 s) with contact on. With SAM 2.1-small it took **1717 s, 120× realtime**, and SAM 2 was
96% of that: 631 s tracking 290 frames and 1023 s on 504 contact frames, about 2 s a frame at
`imgsz=1024`, barely ahead of the CPU. Everything else together was about 60 s: YOLO-World 74 ms a
frame, DA3 170 ms, OCR 206 ms, pose and hands 36 ms, CLIP negligible.

The tracker is now EdgeTAM (`segtrack.py`), SAM 2 with the memory attention replaced by a small
perceiver, and the same clip takes **214–279 s, 15–20× realtime**. The spread is run-to-run noise
on this machine, not configuration. It is also the more accurate tracker on hand-labelled truth,
DAVIS-2017 val, 8 sequences × 40 frames, prompted with the true box on frame 0:

| tracker | J | boundary F @ 8 px | @ 2 px | s/frame |
|---|---|---|---|---|
| EdgeTAM (MPS fp32) | **0.946** | **0.980** | **0.892** | 0.26 |
| SAM 2.1-small @ 1024 | 0.924 | 0.967 | 0.865 | 1.85 |

The release on the handoff reads 8.58 (reference 8.42, 4 frames). SAM 2's recorded 8.50 turned out
to depend on where the window fell: on identical windows SAM 2 read 8.52 and 8.62, and EdgeTAM read
8.58–8.60 on every window. A SAM 2 re-measure around the hit was tried and dropped: it cost 85 s and
moved the answer by half a frame. EdgeTAM's live memory holds at about 1 GB through a 70-frame
session, but the MPS pool grew to 7.7 GB across a clip, so it is emptied past 3 GB
(`CADENCE_MPS_POOL_GB`).

Depth does not sharpen EdgeTAM's outlines. Snapping the mask edge to DA3 depth edges inside a thin
band, gated on the depth difference, on the tracker's own uncertainty, and at 504 and 756 px, and a
depth-guided filter, all lowered DAVIS boundary F. The best variant scored 0.876 at 2 px against
0.892 for EdgeTAM alone, and an image-guided control did as well as any depth variant. Depth is
for z-order and occlusion, not outlines.

A row in this table is a claim about code that keeps changing underneath it, so the rows are re-run
rather than trusted. After the cut-crossing test replaced `survives_cut`, the hands moved to ONNX
and the grip confidences started being derived, the whole set was re-measured from a cold track
cache (`CACHE_VERSION` had been bumped, so nothing was reused): G2 `camera(dolly(in))` with
`focal_stability(0.037)`, G3 the car `still` 0.100–8.200 under that dolly, G4 the tripod camera at
`vlm_agreement` 0.98, G9 `mcu` on both the man and the woman, G10 `out_of_focus(e2)` with no
`nearer` fact emitted at all, G11 `action_boundary(s1)` at 2.500 inside the hop's own motion
interval. Nothing regressed. The habit is worth keeping: five modules had changed, and "the doc says
so" is not a measurement.

## Confidence is derived, never self-reported

X1, and the reason `facts.agreed_runs` exists. Every model in the stack returns a score, and every
one of those scores is about the model, not about the clip: a confident misread of a blurred word
scores as high as a clean read. What reaches the log instead is *how much the interval's samples
agreed with each other*. A caption re-read differently every other frame, a head flickering between
facing left and facing the camera — both are states that were not read, and the number says so.

Two details in that function were found by getting them wrong. A single deviating sample between
two that agree is a misread, not a change, so it is smoothed into the run it interrupts. And a
group too short to claim as an interval is folded into its neighbour, not dropped — dropping it
reports a word misread once as read perfectly.

The seeder takes the same idea further: two vision models are asked to ground the same object
independently, and the IoU between their answers *is* the confidence. That number found an
out-of-focus camera that every detector missed.

Tracking used to be the hole in this. A track carried its *seed's* detector score, which is an
opinion about one frame — a lucky 0.95 on the frame the object was clearest travelled with a mask
that had slid off it two seconds later. It now carries how often an independent detection of the
same class agrees with the propagated mask, at times the tracker was never told about, with the
seed's own frame excluded because it agrees by construction. Where there are too few independent
looks to measure — the usual case for a VLM-grounded seed, since no detector found that class at
all — the cross-model agreement stands rather than being overwritten by a fabricated number.

The hand facts were the other hole, and a quieter one, because they sat directly beside compliant
code: `gripping`, `grasp` and `release` carried a flat 0.7 and 0.6 while the pose states three
lines above them were already scored by agreement. They now go through the same `agreed_runs`, and
a grasp inherits the confidence of the run it opens — a grip is only as well timed as the samples
that agreed the hand was closed.

Where the number is still a constant, it is on a deterministic measurement rather than a model's
self-assessment: ffprobe reading a container, RMS finding silence, ffmpeg's scene threshold, the
Laplacian focus check, the camera classifier's per-label reliability. Those constants say how much
the *method* is worth, which is a different claim from a model scoring itself. One genuine
exception remains: forced alignment passes through the aligner's own per-word score. Its evidence
is the measurement in the table above — 15 ms mean error — not that number.

### The one-frame criterion sits on top of the aligner's own resolution

### G2: the zoom half, and two bugs it exposed

The dolly half had been validated on the man clip; the zoom half had never been tested at all —
`grep zoom vision/tests/*.py` returned nothing, and no clip in the set contains one.

The first attempt was a comp: a true dolly (`cam_z` 4.0→2.4) against a true zoom (`fov` 0.90→0.564,
matched so the 2D magnification is the same), both through `s:world`. It does not work, and the
reason is worth keeping. DA3 gave focal spread 0.067 against 0.069 — no separation — with depth
*rising* in both, when a dolly-in must make it fall, and its focal *fell* on both (688→590, 661→541).
Its intrinsics do not read flat-shaded synthetic renders. For this one producer X4's "comps first"
is closed.

What does work is a zoom made of real pixels: crop progressively into a clip whose camera is static
and scale back to full frame. That is optically a true zoom — the field of view narrows, the camera
does not move, scene geometry is untouched — while every pixel remains photographic.

    ffmpeg -t 6.4 -i woman_9032610.mp4 -vf \
      "zoompan=z='1+0.85*on/160':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1280x720:fps=25"

| case | DA3 focal ratio | `focal_rise` | verdict |
|---|---|---|---|
| zoom, crop 1.00 → 1.85 | 1.22 | +0.14 | `camera(zoom(in))` |
| still, same source unzoomed | 1.00 | +0.01 | `camera(still)` |
| dolly, man clip | 0.92 | −0.06 | `camera(dolly(in))` |
| still, cat clip | — | −0.01 | `camera(still)` |

Two bugs fell out. The rule tested `focal_spread`, a std/mean that **cannot tell a rise from a
fall**; the true zoom scored 0.053 against its own 0.08 bar and never fired, while `focal_slope` sat
computed and unused. It also required near-flat median depth, which is wrong in principle: zooming
in crops the frame to nearer content, so median depth falls (−0.028 here) even though nothing moved.
Depth cannot arbitrate a zoom. The decision is now the signed focal rise alone, and note DA3 reads
1.22 for a zoom that is 1.85 by construction — the magnitude is badly underestimated and only the
sign is trusted.

The second bug is G2's other clause. With `focal` unavailable, `zoom = bool(None and …)` is False and
control fell straight through to `dolly`, so the 2D estimator *was* emitting dolly on its own
whenever depth was off. A 2D scale change is now `unknown`. `camerafacts` had no test file at all,
which is how both survived; it has one now.

### X5 re-measured, and a hole in the harness

Re-running it found a bug in the seeder, by way of a bug in the scorer.

The first re-run scored inset localization at mean IoU 0.214 with `frames IoU > 0.5` of **0.0** —
and temporal IoU **0.963**. That combination is the tell. The scorer takes the best-matching track,
and when the panel track was missing it fell back to a `person` that happened to span the same
seconds while sitting somewhere else in the frame. A high `tiou` beside a spatial IoU of 0.2 is not
a localization; it is a coincidence of interval. So `score_inset` now marks `matched: False` when
the best track never once reaches half-overlap, and `report` withholds the temporal figure and says
the inset was not matched. X2's rule — silence when the thing was not found — applies to the thing
doing the scoring, not only to the producers.

That refusal is what made the real fault visible instead of a plausible-looking 0.963. `ground()`
cached each model reply under `ground-{clip}-{i}-{model}`, where `i` is the target's **index in the
list**. The reply depends on the target and the timestamp, neither of which was in the key. So
grounding `["video panel inset"]` cached under index 0, and a later `ground(["cat", "video panel
inset"])` asked for `cat` at index 0 and was handed the inset's answer. Reproduced directly: asking
for both targets returned `cat` carrying the inset's box `[0.427, 0.197, 0.756, 0.567]`, while
asking for either alone was correct. The key now carries clip, time, target and model.

With the 30 stale entries cleared, the same call returns the inset at `[0.381, 0.589, 0.683, 0.889]`
— a different region entirely, so the old answer had been another object's box — and the eval reads:

| | before the fix | after |
|---|---|---|
| inset seed | `detector_agreement` "man with backpack" | `vlm_agreement` "video panel inset" at **0.98** |
| mean IoU | 0.292 | **0.947** |
| frames IoU > 0.5 | 0.0 | **0.981** |
| temporal IoU | 0.963, wrong object | **0.981** |
| cut localization | 2/2 inside 0 frames | 2/2 inside 0 frames |

Two things worth keeping. The seed confidence is now 0.98 where the run behind the originally
recorded 0.94 showed 0.60 — that run was being served a contaminated entry too, one that happened to
be close enough to work, which is why the number looked fine. And the first explanation offered for
the drop, that five clips and default prompts were not comparable with three and tailored ones, was
wrong: the matched three-clip re-run still scored 0.292. The cache was the cause both times.

Three tests cover this: a coincidental interval match is refused, a real match still prints every
metric, and two targets asked in different orders never share a cached reply.

### A renderer bug found while measuring G1 (fixed)

Measuring G1 "on comps" as the criterion words it means rendering audio at a non-zero `at`. Doing
that for the first time turned up a real bug in `runtime/main.lua`: `s:audio { at = 0 }` mixed
correctly, while `at = 0.5` and `at = 1.2` produced a file with **no usable audio** — and the
renderer still printed `AUDIO mixed 1 clip(s)`. `evals/cases/audio.lua` uses `at = 0`, which is why
nothing caught it.

Bisecting the filter chain, the stream is correct until `amix` and collapses there:

| chain | duration |
|---|---|
| trim + setpts + volume | (blank) |
| + `adelay=1200` | 4.900 s |
| + `aformat` | 4.900 s |
| + `amix` | **0.005 s** |
| + `atrim` | 0.023 s |
| + `apad` (full chain) | 0.025 s |

No `amix` option helps — `duration=first`, `dropout_transition=0`, reordering `aformat` before
`adelay`, and `aresample=async=1:first_pts=0` on each input all still collapse. `amix` passes on the
timestamps of its delayed inputs and the encoder cannot use them. Appending **`asetpts=N/SR/TB`
after `amix`**, which regenerates the pts from the sample count, fixes it: two clips at 1.2 s and
3.0 s mix to exactly 5.000 s with sound starting at 1.206 and the second clip's pattern from 3.006.

The voice/duck bus has its own `amix` and needs no such fix — checked by removing it there, which
changes nothing, because `apad` follows that bus and its output feeds the main `amix`. Only the
mix that reaches the encoder is patched.

Three changes landed. The `asetpts`; a guard, since `apad=whole_dur` pads every mix to the comp
duration and so a short mix file can only mean the chain dropped audio — the renderer now probes the
mix and raises instead of muxing a silent track, this bug having only been able to hide because the
success line printed either way; and a second, delayed clip in `evals/cases/audio.lua`, which adds
no pixels (goldens compare identical) but means the suite now covers `at > 0` at all.

Adding that delayed clip immediately turned up a second, smaller thing. The props sampler reports
an audio node at *every* sampled time, not only while it sounds, so the lifter was emitting
`holds(volume(audio3, 0.35), 0.000, 3.000)` for a clip whose `holds(playing(audio3), 1.000, 2.500)`
covered half of that — two exact facts contradicting each other. Every audio clip in the suite
had previously started at 0 and run the full duration, which is the only reason the envelope and the
clip window had always coincided. The envelope is now clamped to the clip's own window. Lowering
does not read `volume`, so the byte-exact round trip is unaffected.

Unrelated, but found by the same golden run: `evals/cases/drop.lua` does not render at all —
`physics_bake.lua:59: Incorrect number of parameters` — and has no golden captured. It predates this
work and is untouched by it.

G1 asks for word times inside one frame, and what that costs is worth stating, because it is what
sent this in the right direction in the end. Forced alignment places a boundary on the emission
grid, and MMS_FA's stride is 20.1 ms at its 16 kHz input, so a frame at 30 fps is **1.66 emission
steps**. The criterion is asking for better than two steps of the model's native resolution.

Six words of seven delivered it and the seventh — "land" — sat 2.09 steps out, at +50 ms. Six
explanations were tested and all six failed: trim bias (the errors' signs are mixed), end of
utterance (+42 as the last word, +45 with a word after it), a slow onset ramp ("land" has the
*sharpest* onset of the seven, −45→−25 dB in 4 ms), sample-rate mismatch, grid quantisation, and
context (in isolation every other word sits at 21.0 ms and "land" at 43.0 ms).

What finally identified it was running four more acoustic models. wav2vec2 base, wav2vec2 large,
wav2vec2-lv60k and HuBERT-large put "land" at +50 ms too, three of them to the millisecond, and the
largest model was the *worst* overall. A bias that survives five independent models is not a bug in
any of them. It is the one thing they share: they are all CTC, and CTC emits late, with the peak for
a word-initial sonorant landing near the following vowel rather than at the consonant.

The ground truth was checked before the models were blamed, since five models agreeing is also what
a wrong truth looks like. Measuring the leading silence in each word's own wav put "land" at 2.2 ms,
in line with the rest — the truth was right.

So the fix is a second estimator that shares nothing with the first: short-time energy. Walk back
from the CTC boundary to where energy rises out of the preceding valley. Worst error over both
comps falls from 50.0 ms to 16.0 ms, 15/17 words inside a frame to **17/17**. The correction is
one-directional by construction, which is the useful check that it is modelling the real effect
rather than fitting noise: it only ever moves a word earlier, and it moves stop and fricative
onsets by 0–10 ms while moving sonorants by 20–50 ms.

Two things about the confidence, both X1. The old one was the mean CTC posterior — self-reported,
and worse than useless here, since it was above 0.9 on the words that were 50 ms late. The first
replacement graded the onset by how tightly varying the rise threshold pinned it down; that was
derived, but measurement showed it *anti*-correlated with error (mean 2.4 ms for the words it
scored low against 7.8 ms for the ones it scored high), so it was dropped rather than shipped.
What ships reports the two regimes the measurements actually support: an onset was found and is
unambiguous (17/17 inside a frame, Laplace-smoothed to 0.95), or none was, in which case the time
is the raw CTC boundary known to run up to 50 ms late and says so at 0.4. On the constructed comps
every word is refined; on real continuous speech 17 of 103 words fall back, which is the producer
declining to claim frame accuracy it does not have.

Four explanations were tested and discarded before calling it a model limit: the `say` fixture's
−45 dB trim biasing the truth (errors would share a sign, and they do not), an end-of-utterance
effect (the word scores +42 ms final and +45 ms with a word after it), a slow onset ramp (that word
has the *sharpest* onset of the seven, 4 ms to −25 dB, while the slowest at 25.9 ms errs by +1 ms),
and a sample-rate mismatch between the 22.05 kHz fixture and the 16 kHz model (`align` resamples
through ffmpeg, so there is none).

A finer grid was the obvious next move and it does not work. The emission stride is fixed in
samples, but it can be *offset* by padding the audio, so aligning on 2, 4 and 8 interleaved grids
and combining gives sub-step resolution from the same model. The mean improves a little — 15.3 ms
to 13.4 — and the word that fails does not move at all: +41.7, +41.3, +41.5, +41.4 ms across one,
two, four and eight grids. The model stably believes that boundary is there. **The error is not
quantisation.**

Aligning each word against its own file says the rest. Every word lands at exactly 21.0 ms, one
emission step, which is the lead-in the model takes when a file opens on speech with no silence
before it. Every word except one: "land" lands at 43.0 ms, two steps. It costs one step more than
its peers in isolation and in context alike, and the reason is in the word — a low-energy /l/ onset
the acoustic model does not commit to until the vowel arrives.

So closing it means a different acoustic model, not a finer grid and not a better-tuned version of
this one. That is a choice about dependencies, so it is written down here rather than made quietly.

## Silence is a valid output

X2. Where a producer cannot separate two explanations, it emits nothing, because a reader can act
on a missing fact and a confident wrong one propagates. Three places where this is load-bearing:

- **Defocus.** Monocular depth reads blur as distance. On the woman clip it put an out-of-focus
  camera in the near foreground at 0.29 relief against a sharp subject at 0.81 — backwards, and
  confidently. Sharpness vetoes the pair, and no ordering is claimed.
- **Camera motion.** Where the background is too sparse or too clustered to separate camera from
  subject, `camera(unknown)` is the answer. A wrong camera fact is inherited by everything that
  reads the log.
- **Caption boundaries.** A cross-fade reads as neither line, so the bisection returns the coarse
  sample time rather than a precise wrong one.

## Known gaps

- **Gait.** The man clip is a man walking while the camera dollies after him. He sits near the
  centre of the dolly's expansion, so he is still in the frame and still in the compensated world,
  and the walk leaves no trace in either. A `followed_by_camera` fluent was tried and fired on the
  three parked cars rather than on him — a static object at that spot is genuinely indistinguishable
  from a followed one by box centroid alone. The signal is in the body, not the box.
- **Hands.** The `mediapipe` package aborts the process on this macOS build
  (`DrishtiMetalHelper … Service is unavailable`, from a Metal-backed calculator inside the detector
  subgraph, unavoidable via the CPU delegate or `MEDIAPIPE_DISABLE_GPU`). That is a property of the
  runtime, not of the models: the same palm detector and 21-point landmarker exported to ONNX run
  under OpenCV's DNN backend with no Metal involved, and they work here. `vendor/` carries the two
  OpenCV Zoo wrappers, pinned, with the palm detector's 2000-line anchor table replaced by the six
  lines that generate it (identical to 7.5e-09 — 24x24 cells with 2 anchors each, then 12x12 with
  6, every anchor at its cell's centre).

  RTMPose was tried first, since the goal names it as the alternative, and it is the wrong shape of
  model for this. RTMW is top-down: a person detector proposes a box and the pose head fills in 133
  keypoints. The handoff clip has no person in it — two gloved forearms reach into a lit rectangle
  and nothing else is in frame — so YOLOX has nothing to find, and the keypoints land on the *bottle*
  and its shadow rather than on either hand. A body model needs a body.

  The gesture half of G5 is `palm(e, camera)` beside the existing `hand_state(e, open)`, kept as two
  fluents rather than one fused "open palm toward lens". Orientation and finger curl are independent
  things a shot can show, and an editor looking for a presented palm and one looking for a relaxed
  open hand are asking different questions. The reading is the winding of the two knuckle vectors
  leaving the wrist, which reverses between a palm and the back of the same hand and reverses again
  with handedness, so it is measured on constructed hands — no clip here presents a palm to the
  lens, and saying so is better than scoring it on footage that cannot answer.

  What remains unsolved is the handoff clip itself, and now for a stated reason rather than a
  missing library. The palm detector is confident on a bare hand (0.91 and 0.99 on the woman clip,
  landmarks correctly on the hand) and returns **nothing at all** on the handoff clip down to a
  score threshold of 0.3 — two rubber gloves, backlit, against a blown-out white rectangle, which
  is not what a detector trained on skin knows. So handoff timing still comes from mask contact.

The truth side of that comparison now exists. Stepping the clip a frame at a time at 25 fps: the
  white glove's fingertip is against the bottle through 8.24 s, marginal at 8.28, and a clear gap
  has opened by 8.32. **Hand-eye truth for the release is 8.30 s ± 0.02.**

  The other side does not. An earlier run of this clip produced `happens(handoff(e1, from(e2),
  to(e3)), 8.000)`, and re-running it does not: the seeder returns *one* gloved hand, and the
  handoff rule needs two, since it fires on one hand's touch interval ending as another's begins.
  Three separate things block the second hand, and it is worth naming all three, because fixing any
  one of them alone changes nothing:

  1. **The detector never sees the second hand at all.** 41 `gloved hand` detections over 37 sampled
     frames, top confidence 0.32. Eight frames carry two boxes at once, which looks promising until
     you read them: their centres sit at x ≈ 0.67–0.83 with IoU up to 0.62 and confidences of
     0.10–0.23. Those are duplicate boxes on the *white* glove. The green glove, over at x ≈ 0.3,
     is never detected once. So this is not a `_covered` threshold merging two hands — there is no
     second hand to merge. Rubber gloves in silhouette are not what it was trained on, the same
     thing that blinds the palm detector.
  2. **The seeding loop** only asked the vision models for classes it had *none* of, so a class that
     wanted two and had one was never topped up. That one is now fixed: grounding is asked for the
     shortfall. It made the run say `{'gloved hand': 1} still wanted` where before it said nothing.
  3. **`ground` returns at most one box per target string**, and deliberately — asking a model for a
     list makes the reply *order* model-dependent, and pairing by index silently swaps two objects'
     boxes. So no amount of asking yields a *second* instance of one class. A second would need a
     distinct target string, "the green glove" against "the white glove", and then its class is no
     longer `gloved hand` and the handoff rule stops recognising it as a hand at all.

  And underneath all three, the confidence mechanism itself declines: asked to ground `gloved hand`
  on the midpoint frame, qwen returns a box and gemini returns nothing usable, so the cross-model
  agreement that *is* the seed's confidence cannot form. That is X1 behaving correctly, not failing.

  With the landmark routes closed, three model-free estimators were tried, on the reasoning that the
  release is visible even if the hand is not: the white glove lets go and withdraws right while the
  green glove keeps the bottle. Hand-eye truth is 8.30 s and two frames at this clip's 25 fps is
  0.08 s, so the target is [8.22, 8.38].

  | estimator | what it gives | error |
  |---|---|---|
  | box separation (glove x0 − bottle x1) | largest jump at 8.52–8.62 | 0.22–0.32 s |
  | mask contact area, 10 fps | 422 → 380 px, smooth, no break at all | no event |
  | ROI frame-change at 25 fps, contact region | peak 8.48, rise onset ≈ 8.17 | 0.18 s / 0.13 s |

  None is inside two frames, and the reasons are instructive rather than incidental. The boxes are
  dominated by the forearm, which keeps moving through the release. The masks are 84×160 against a
  960×506 frame, so the fingers are a handful of cells. And frame-change peaks where motion is
  *fastest*, which is after the hand has let go, not at the moment it does — the release is the
  onset of a divergence, and onset estimation on a broad hump is worth about 0.13 s here.

  **The reference itself was wrong.** Three estimators clustering near 8.5 against a stated truth of
  8.30 is the same shape as G1's "land", and there the right response was to re-check the truth. At
  6× zoom on the fingertip/bottle junction, stepping 0.04 s at a time: the bottle's base rests on the
  glove through **8.40**, and at **8.44** a bright wedge of the background panel appears between
  them. That is an objective marker rather than a judgement, so the reference is 8.42 ± 0.02 and the
  recorded 8.30 was three frames early. The revision was made after the estimators had been run,
  which is the direction that should make anyone suspicious — so note that it does not rescue the
  goal. The best estimator lands at 8.56; had the truth been fitted to it, it would have been put
  there, not at 8.42.

  **The estimator question is now settled, on exact truth.** G5's producer had never had the X4
  treatment — measured on a comp before being trusted on footage — so `scratchpad/g5/release.lua`
  builds one: a carrier tracks a drifting object's underside exactly until T = 2.40 and then falls
  away, making the contact break exact by construction.

  | estimator | on the comp (truth 2.400) | error |
  |---|---|---|
  | contact-gap onset | 2.44 | **1.0 frame** |
  | ROI frame-change peak | 2.88 | 12.0 frames |

  So the definition that matters is the gap opening, not the change peaking — frame-change peaks
  where motion is *fastest*, which is well after the release, and its 0.18 s error on footage was
  not bad luck but the wrong question. Carried to the clip, the comp-validated estimator gives 8.56
  against the corrected 8.42: **3.5 frames, still outside the criterion**. The reason is resolution
  at the junction, not the rule — the real gap at 8.44 is one or two pixels across a soft,
  motion-blurred, low-contrast boundary, and colour segmentation does not register it until it is
  about four pixels wide at 8.56. On the comp the same rule sees 60 px rectangles and is exact.

  **And it is triggered by what the log already carries.** `sweep` takes a pair's `touching`
  interval and walks it in 1.4 s windows, taking each window's boxes from the tracks so the crop
  stays tight while following the pair. Handed the handoff clip's own
  `holds(touching(e1, e2), 2.100, 10.700)` — the full span, nothing pre-selected — it returns
  **8.50 at confidence 1.00, 2.0 frames** from the reference, in 887 s. The eight windows before the
  real one all returned None, so the onset guard produced no false positives along the way.

  Four cheaper triggers were tried first and none of them contains the event:

  | coarse signal | on the comp (truth 2.400) | on the handoff (ref 8.42) |
  |---|---|---|
  | frame-change peak, fixed ROI | 2.88 over a tight interval; **0.48** over the whole clip | 8.48 tight; **9.70** over the whole interval |
  | box separation | — | gradual, largest jump 8.52–8.62 |
  | centroid distance | — | smooth 130 → 316, **no break at all** |
  | mask contact area at 10 fps | — | 422 → 380 px, no event |

  The last two fail for the same reason and it is worth stating: the bottle and the glove were
  *already drifting apart* before the grip opened, so any measure of how far apart they are rises
  straight through the release without a step in it. Only the gap between their surfaces has a step,
  and only at native resolution. A fixed-ROI frame-change peak fails differently — over a long
  interval its region no longer contains the objects at all, which is how it lands on 9.70.

  `perceive` now takes `want_contact`, off by default because one pair over eight seconds is about
  fifteen minutes. The fact is `happens(release(a, b), t)` with `contact_gap` as its producer.

  **This is now a producer, `contact.py`, not a prototype.** The window is derived from the two
  entities' boxes at the contact moment, prompts are clamped into it, and the pure parts — window,
  clamp, gap, onset — are unit-tested. End to end through `break_time`:

  | material | truth | measured | error |
  |---|---|---|---|
  | release comp, exact by construction | 2.400 | 2.44, conf 0.67 | **1.0 frame** |
  | handoff clip | 8.42 | 8.50, conf 1.00 | **2.0 frames** |

  Three bugs surfaced in the wiring, all of which had been masked by the hand-picked window. Scaling
  the crop can land on an odd pixel height, which h264 refuses — the encode aborts, the segment is
  unreadable, and that arrives as silence, a plain bug wearing the costume of an honest "cannot
  tell". Widening the window to cover where the entities *travel* is worse than sizing it at the
  contact, because SAM runs at a fixed `imgsz` and a bigger crop spends fewer of those pixels on the
  junction: the handoff moved from 2 frames out to 4. And requiring the gap thresholds to agree
  *exactly* is wrong when a separation opens at a few pixels a frame — a 1 px and a 4 px threshold
  are legitimately a frame or two apart, so they are required to agree closely, the earliest wins,
  and the spread becomes the confidence.

  **Masks at native resolution close the gap.** The tracker downsamples to `MASK_W = 160` purely for
  storage, while `_propagate` has SAM's masks at full size first. Re-running SAM2 on a 2×-upscaled
  crop around the contact at 25 fps, keeping the masks un-downsampled (660×1120 instead of 84×160),
  and applying the same gap-onset rule: the minimum gap is **1.0 px on every frame from 7.90 to
  8.46**, then 41 → 58 → 79 → 100 px. The break is at **8.50**, and the threshold is irrelevant
  because the jump is 1 px to 41 px — anything between 2 and 40 gives the same answer.

  The first attempt gave nonsense (4, 7, 8, 9, 11 px, then frames with no glove under the bottle at
  all) because both box prompts fell outside the crop: the glove's box reached x=1395 in a 1110-wide
  window and both had negative y. SAM2 was being handed malformed prompts. Sizing the window to hold
  both boxes across the whole interval, and clamping, fixed it.

  **What that does and does not settle.** Against the re-derived reference of 8.42 the error is
  0.08 s — 2.0 frames, meeting the criterion at exactly its boundary. Against the originally recorded
  8.30 it is 5 frames and fails. The pass therefore depends entirely on the revision, and the revision
  was made after estimators had been run. Three things weigh against that being motivated reasoning:

  * it rests on a frame-by-frame observation anyone can repeat — contact at 8.40, a bright wedge of
    background between bottle and glove at 8.44 — not on any estimator's output;
  * when it was made it did *not* rescue the goal, the best estimator then standing at 8.56;
  * the estimator is independently validated at 1.0 frame against exact comp truth, so 8.50 implies a
    true break in 8.46–8.54, which agrees with the 8.44 read off the pixels.

  The honest reading is that the recorded 8.30 was about four frames early and the real break is near
  8.45. But a criterion measured against a reference revised in the same session is not cleanly met,
  and it wants confirming on a second clip whose truth is fixed before any estimator runs. The
  producer is prototyped and validated but **not wired into `perceive`**: the window is still chosen
  by hand, and generalising it means deriving it from the two tracks' union.

  **The gesture clause, actually in a log.** It had been claimed on the strength of `palm_facing()`
  returning the right value for constructed landmark geometry — a function's return, not a fact. No
  log anywhere carried a gesture fact, because `landmarks.emit` gates hand processing on an entity
  whose class contains "hand" and none of the clips had ever been prompted for one. Prompting the
  woman clip with `hand` gives:

      entity(e2, "hand", seed(0.720)).
      holds(hand_state(e2, open), 0.500, 3.700).   src ... mediapipe_hands, 0.70
      holds(hand_state(e2, open), 3.700, 6.480).   src ... mediapipe_hands, 0.86
      holds(palm(e2, away),       0.500, 1.300).   src ... mediapipe_hands, 0.67
      holds(palm(e2, camera),     1.500, 6.480).   src ... mediapipe_hands, 0.86

  `palm(e2, camera)` holding over `hand_state(e2, open)` is the criterion's "open palm toward lens".
  The hand is found on 12 of 16 sampled frames; the landmarks were drawn and checked against the
  picture before any of this was believed, because a confident detection has already been wrong once
  here. Worth recording that the check itself was wrong first: converting the frame RGB→BGR before
  calling `hands()` makes the detector find nothing, and the first annotated image showed no
  detections at all. `frame_at` returns RGB and that is what `emit` passes, so the pipeline was right
  and the verification was not.

  And a provenance bug it exposed: `_emit_runs` stamped every fluent `yolo_pose`, including `palm`
  and `hand_state`, which come from the hand landmarker — a different model that fails in different
  places, declining gloves outright where the pose model copes. Half the facts named the wrong
  producer. Provenance a reader cannot trust is worse than none, so the producer is now per-fluent. Note too that `rule_handoff` in `perceive.py` cannot fire here
  whatever the timing — it needs two hand entities and only one glove is ever detected, which is a
  grammar question about naming two instances of one class, not a perception one. Naming two instances of one class is a grammar question, and
  it is the next thing to decide rather than something to patch around.

  One thing to revisit when it can be measured. The rule reads

      happens(handoff(obj, from(ha), to(hb)), max(b0, a1 - 0.5))

  and that `- 0.5` back-dates the event half a second on no evidence at all. It is the first thing
  to suspect against an 8.30 truth — but suspecting is not measuring, and it stays a suspicion here
  rather than becoming a finding. Note where it lives, though: in a rule, not a model, which is what
  X3 predicts of fixes.
- **Costume change, and what it actually measures.** G7 asks that identity survive a costume or
  angle change. The angle half is measured on the three-shot assembly. For the costume half there
  is no footage here in which anyone changes clothes, so it was constructed: the man's second shot
  was hue-rotated by 60°, 120° and 180° before assembly, which moves his jacket navy → purple →
  maroon → olive. That is a *harder* perturbation than a costume change, because it moves the
  street, the leaves and the parked cars with him rather than only what he is wearing.

  The appearance signal barely notices. Cosine between his shot-1 and shot-3 crops:

  | hue rotation | 0° | 60° | 120° | 180° |
  |---|---|---|---|---|
  | cos(shot 1, shot 3) | 0.915 | 0.881 | 0.873 | 0.882 |

  against 0.764 for a known-different pair in the same clip — percentile 1.00 at every rotation, and
  a worst case 0.109 above the baseline. CLIP appearance is reading shape and structure far more
  than colour, which is the property this goal was asking after.

  The *fact*, though, did not fire in that first assembly, and for a reason worth keeping: with the
  man, the cat and one spurious man track there was exactly **one** known-different pair, and
  `MIN_NULL = 6` refuses to calibrate a percentile against a single number. Identity was abstaining
  for want of a null, not for want of evidence — X2 doing its job, and a reminder that a sparse
  scene silences this producer no matter how obvious the match looks.

  Given a scene with enough in it to calibrate against — the man, a tree and three parked cars, so
  7 known-different pairs — it fires:

      holds(same_as(e3, e1), 6.100, 8.900).
      src(holds(same_as(e3, e1), 6.100, 8.900), clip_appearance, 0.86).

  where `e1` is the man in shot 1 and `e3` is the same man in shot 3 under the 120° rotation. A
  parked car rejoins across the same cut at 0.71. So both halves of G7 are measured: the angle
  change on the unaltered three-shot assembly at 1.00, and the colour change here at 0.86.
- **A teardown flake that can fail the build.** About one full-suite run in five ends, *after* all
  294 tests have passed and reported, with `libc++abi: terminating due to uncaught exception of type
  std::__1::system_error: recursive_mutex lock failed`, and that aborts the interpreter with exit
  134. No single test module reproduces it in three runs each — it needs the whole set of native
  libraries resident together (torch, cv2, onnxruntime, av, ultralytics), and OpenCV already warns
  on import that `av` and `cv2` ship duplicate `libavdevice` builds. So a red CI run here is worth
  reading before believing: "294 passed" followed by exit 134 is this, not a failure. It is left
  diagnosed rather than fixed, because guessing at C++ static-destructor ordering across five
  vendored native libraries is how you get a flake that moves instead of one that goes away.
- **Hallucinated bodies.** A frame holding only a gloved hand and its shadow produced a confident
  seated person with 13 of 17 keypoints above 0.5, where the real man walking away had 4. Keypoint
  counts measure plausibility, not presence. What keeps it out of the log is that pose facts attach
  only to entities the tracker already found.
- **Cuts and propagation.** SAM 2 does not know what a cut is. Given a whole clip it drags a mask
  straight through one and reports a three-shot assembly as a single track of nonsense. Propagation
  is therefore bounded by the shot, and each boundary is then re-examined: the track is carried one
  shot further only if the content inside its box ignored the cut.

  The first version of that re-examination asked whether the *mask* survived the crossing unchanged
  in place and size, on the premise that an overlay sits still while the plate changes under it.
  That premise is wrong for the thing the clip eval actually contains — a picture-in-picture tweened
  across the frame while it plays — and the measurement said so plainly. Replaying the inset's two
  crossings and four in-shot ones through every test available, none separated the accepts from the
  rejects:

  | test | accepts (inset, both directions) | rejects (content in a shot, n=4) |
  |---|---|---|
  | mask IoU | 0.41, 0.59 | 0.24 – 0.63 |
  | box IoU | 0.91, 0.92 | 0.74 – 0.92 |
  | mask area ratio | x0.48, x0.65 | x0.29 – x1.05 |
  | crop histogram | one pass, one fail | two pass, two fail |

  Every column overlaps, so any threshold over them would have been fitted noise, and the histogram
  test was deleted rather than tuned. What separates the cases is what a picture-in-picture
  physically is: a rectangle of foreign footage. At a cut the plate changes completely and the panel
  does not, so the pixels inside the box change far less than the pixels outside it — **0.59x for
  both directions of the inset against 0.98x–1.18x for content belonging to the shot**. It is a
  ratio against the same frame's own change, not an absolute, because how much a cut changes depends
  on the two shots. It declines to answer in two situations rather than guess: a box covering most
  of the frame leaves only a border to compare against, and a boundary where the frame barely
  changes is not a cut this can measure.

  The one crossing that scored like an overlay without being labelled one is worth keeping in view:
  a box at `[0.307, 0.694, 0.460, 0.889]`, lying wholly inside the panel's `[0.208, 0.588, 0.509,
  0.887]` — a man detected *in* the picture-in-picture, because the panel is playing the man clip.
  That region does ignore the cut, and carrying it across is right. The test was correct there too;
  only the name `man with backpack` is ambiguous about which layer it means.

  Content that stops at its cut is what makes cross-shot identity a question worth asking — the
  pieces have to exist separately before anything can rejoin them with a calibrated score.
- **Diarization.** pyannote's models are gated: `pyannote/segmentation-3.0` and
  `pyannote/speaker-diarization-3.1` both return 403 until someone accepts their conditions in a
  browser, which an agent cannot do. `diarize.py` uses `speechbrain/spkrec-ecapa-voxceleb`, which is
  ungated, and clusters 2 s windows of speech per clip. What is lost with it is pyannote's
  overlap-aware segmentation: two people talking at once come out as one turn, labelled with
  whoever dominates.

  It runs *after* `speaking(e)`, not instead of it, and is told what that producer named. A cluster
  whose speech mostly falls inside intervals already attributed to an entity is emitted under that
  entity's name; a cluster matching nobody on screen keeps an anonymous `spk<n>`, which is the case
  this exists for — a voice-over, a reply from off frame, the same person still talking after the
  cut that left them. `speech_turn(e2)` asserts both that one voice holds the interval and that the
  voice is e2, so a named turn's confidence is the product of the two.

  (`speech(a1)` is the separate, weaker claim that the audio has speech in that interval. One
  predicate meaning both, told apart only by whether its argument was an audio stream, is a trap.)
- **Synthesised speech is not evaluation material for this.** The obvious way to build exact truth
  for a diarizer is to write the timeline yourself with `say -v Alex` and `say -v Samantha`. It does
  not work, and it fails quietly. ECAPA places those two voices **0.170** apart — closer than two
  utterances of one real speaker (0.176 mean, 0.227 for the specific pair measured), so a clip built
  that way is one voice as far as any speaker embedding is concerned, and a correct diarizer scores
  zero on it. The model is not at fault: the same model puts speech against a tone or noise at
  ~1.0, one real speaker at 0.176, and two different real speakers at 0.89-1.13. The measurement
  above uses real speakers from LibriSpeech and VoiceBank for that reason.
- **The VLM seeder is on a clock.** One clip spent 1189 s here when a provider accepted the request
  and hung up mid-response: the socket sat in `CLOSE_WAIT` and the eval harness default of three
  180-second attempts applied per model, per target. Grounding now runs with its own short budget,
  because losing a seed costs one entity's facts while hanging costs every producer behind it.
- **Not built yet:** a predictive (V-JEPA-class) event-boundary model in place of the
  self-similarity one, and a dedicated gaze model — `facing` reads where the head points, which is
  what "she turns away" means in an edit, but not where the eyes go.

## Measuring it on real pixels

`bin/cadence-vision eval --clips DIR` (X5) assembles real clips into a comp, which fixes the cut
times and a travelling inset's rectangle exactly while every pixel still comes from a camera, then
scores what perception recovers from the render.

Cuts are scored as an **off-by-N-frames histogram**, not as tIoU: at 3 s into a 9 s assembly, a cut
found one frame late overlaps 0.996 of the shot and is still wrong in an edit. The inset is scored
by per-frame IoU and by temporal IoU, and the seeder is not told where it is — a run that never
finds it scores zero recall, which is a seeder result and not a scoring artefact.

First run, 3 clips assembled to 9 s: **both cuts found, 0 frames off, no misses and no false
positives**; the inset seeded by cross-model agreement at mean IoU 0.957 and median 0.965, with
every sampled frame above 0.75.

And one finding the harness existed to produce. Temporal IoU came back at **0.519** while spatial
IoU was 0.957 — the inset is on screen 1.8–7.2 s across two cuts, and the track covered 2.9 s, one
shot's length. Spatially perfect, temporally half there, and only a measurement with exact truth
would have said which. Bounding propagation by the shot is right for content in the shot and wrong
for anything composited over the cut, so each boundary became a question rather than a wall
(`tracking.ignores_the_cut`, and see *Cuts and propagation* above for the two tests that had to be
discarded first).

| | run 1 | run 3 |
|---|---|---|
| cuts within 0 frames | 2/2 | 2/2 |
| inset samples | 29 | 54 |
| inset span recovered | 3.1–5.9 s | 1.9–7.2 s (truth 1.8–7.2) |
| mean IoU | 0.957 | 0.940 |
| frames IoU > 0.75 | 1.000 | 0.981 |
| **temporal IoU** | **0.519** | **0.981** |

Mean IoU fell slightly, and that is the honest shape of the result rather than a regression: the
frames recovered at the two extremes are the ones where the panel is partly there, so they are
scored now instead of being missing. Trading 0.017 of spatial agreement for 0.462 of temporal
coverage is the right trade for an edit, where a track that stops two shots early is unusable
however well it fits the frames it does cover.

Worth recording that the extension does not simply run to the end of the neighbouring shot: SAM 2
loses the panel where the panel is not, so the track stops at 1.90 s and 7.20 s against a truth of
1.8–7.2 s. Content that belongs to its shot still stops at its cut — all four of those crossings
were rejected — which is what lets `identity` rejoin it as a separate sighting with a calibrated
score.

## Layout

```
vision/cadence_vision/
  facts.py        grammar, comp lifter, agreed_runs (derived confidence)
  lower.py        fact-log edits back into Lua source
  perceive.py     orchestrates producers into a log for a clip
  tracking.py     open-vocabulary seeds + VLM-agreement fallback, SAM 2 propagation
  camerafacts.py  still/pan/tilt/dolly/zoom/handheld, DA3 intrinsics separate dolly from zoom
  audiofacts.py   words, beats, loudness, forced alignment, sound-source correlation
  diarize.py      speaker turns from clustered ECAPA embeddings, named after entities
  ocr.py          on-screen text as a fluent, boundaries bisected
  landmarks.py    posture and facing from pose keypoints; hands where the library works
  identity.py     the same subject after a cut, calibrated against same-frame pairs
  boundaries.py   where the action turns over inside a shot
  clipeval.py     the real-pixels/exact-truth harness
  vendor/         pinned Apache-2.0 MediaPipe ONNX wrappers (OpenCV Zoo)
```

## Research log

Benchmarks: VEBench (temporal IoU 0.00–0.11 for edit localization), AgenticVBench (best agent 31%
vs humans 81–95%), CameraBench (camera-motion taxonomy). Models used: Depth Anything 3 small
(Apache, depth + per-frame intrinsics), SAM 2.1 small (propagation), YOLO-World v2 (open-vocabulary
seeds), YOLO11n-pose (COCO-17), RapidOCR (PaddleOCR models on onnxruntime, no torch), torchaudio
MMS forced alignment, faster-whisper (what was said, never when), librosa (beats, loudness),
SpeechBrain ECAPA-TDNN (speaker embeddings for diarization), MediaPipe palm detection and hand
landmarks as ONNX (OpenCV Zoo), which is how hands work on a Mac at all.

Cross-shot identity is Foote-style in spirit and CLIP-based in fact; the boundary detector is
Foote's novelty score over a self-similarity matrix with CLIP embeddings in place of audio features.

Candidates considered and not adopted: SAM 3 and Grounding DINO 1.5 for seeding (swap in when
available), pyannote for diarization (gated; ECAPA clustering stands in, and would be replaced by
it for overlap-aware segmentation if the gate were ever opened), InsightFace + OSNet + DINOv3 as
stronger opinions on identity
than appearance alone, L2CS-Net for gaze, Kinetics-GEBD / V-JEPA-class predictive features for
event boundaries. Each would slot in as another producer behind the same fact, which is the point
of writing the interface as a grammar rather than as an API.
