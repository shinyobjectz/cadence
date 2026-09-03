# Motion vocabulary & style cohesion

The difference between "animated" and "designed" is not more tweens — it's a
consistent motion system. This file: the primitives and their exact semantics.

## Vocabulary (beyond tween/set/wait/parallel)

| primitive | call | what it gives |
|-----------|------|---------------|
| spring ease | `ease.spring{stiffness=260, damping=11}` | real physics overshoot+settle; the "pro feel". Low damping = violent slam; high = soft landing |
| css bezier | `ease.cubicBezier(0.9, 0, 0.1, 1)` | exact CSS/GSAP curve ports; snappy editorial cuts |
| stagger | `t:stagger(nodes, dur, props, {each=0.05, ease, from="start\|end\|center"})` | cascade across many nodes; `props` may be `function(node, idx)` for per-node variation |
| wiggle | `t:wiggle(node, prop, {duration, amp, freq, seed})` | organic idle life; deterministic noise, auto attack/release, returns to base. Runs alongside (doesn't advance cursor) |
| path | `t:path(node, dur, {{x,y}, ...}, ease)` | constant-speed motion through waypoints (Catmull-Rom, arc-length) |
| kinetic text | `s:kinetic{text, x, y, size, color}` → `.chars` | per-char nodes, measured/centered by real font metrics; stagger over `.chars` |
| vector draw | `s:vector{w, h, draw=fn(v, t)}` | closed-form procedural graphics: bursts, trails, ribbons — anything expressible as pure f(t) |
| color rides | tween `color` | interpolation is OKLab (perceptual) — long hue rides stay vivid, never muddy |

## Exact semantics

- `ease.spring{stiffness, damping, mass}` returns an ease FUNCTION — pass it where
  an ease name goes. Closed-form damped oscillator; `zeta < 1` oscillates.
  Duration you give the tween maps across the spring's settle time (~6/(ζω)).
- `ease.cubicBezier(x1,y1,x2,y2)` returns an ease function (CSS semantics).
- `t:stagger` advances the cursor to the LAST tween's end. `from="center"` orders
  by distance from the middle index. `props` as `function(node, idx)` is called
  once per node at compile.
- `t:wiggle` does NOT advance the cursor and may not overlap any tween on the
  same node.prop (compile error). Value returns to base at the window's end;
  `ramp` (default 0.25s) is clamped to half the duration.
- `t:path` starts from the node's current sim position, appends waypoints,
  advances cursor by `dur`, and updates sim x/y to the final waypoint. Records
  onto BOTH x and y — any overlapping x/y tween is a compile error.
- `s:kinetic` char positions are assigned by the host at compile (real font
  metrics) — chars' `initial.x/y` are unset until then; `initial.scale/opacity/
  rotation` set in the scene are preserved. ASCII only (byte-wise split).
- `s:vector` draw runs EVERY frame; it must be a pure function of `t` (no
  upvalue mutation, no clocks, no randomness — hash from loop indices instead).
- Color tweens interpolate in OKLab; alpha linearly. Hex in, `{r,g,b,a}` out.
