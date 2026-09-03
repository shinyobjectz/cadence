# Ellua examples

Run examples from the `ellua/` root so relative assets resolve consistently:

```bash
bin/ellua render examples/basics/hello.lua -o examples/out/hello.mp4
```

| directory | purpose |
|---|---|
| `basics/` | authored API spots (no product footage) |
| `clips/` | simple stock-footage compositions |
| `demos/` | capture-, font-, or external-service-dependent product demos |
| `assets/` | source media shared by the examples |
| `out/` | local render output; intentionally ignored |

`basics/` spots for the newer APIs:

```bash
bin/ellua render examples/basics/palette.lua -o examples/out/palette.mp4
bin/ellua render examples/basics/chart.lua   -o examples/out/chart.mp4
bin/ellua render examples/basics/bumper.lua  -o examples/out/bumper.mp4
bin/ellua render examples/basics/grade.lua   -o examples/out/grade.mp4
bin/ellua render examples/basics/captions.lua -o examples/out/captions.mp4
bin/ellua render examples/basics/pulse.lua   -o examples/out/pulse.mp4
bin/ellua render examples/basics/onair.lua   -o examples/out/onair.mp4
bin/ellua render examples/basics/bounce.lua  -o examples/out/bounce.mp4
bin/ellua render examples/basics/wave.lua    -o examples/out/wave.mp4
bin/ellua render examples/basics/world3d.lua -o examples/out/world3d.mp4
bin/ellua render examples/basics/page.lua    -o examples/out/page.mp4
```

`assets/` also contains legacy media retained for reproducibility. It is not a
guarantee that every asset is used by a current composition.

Public renderer testing lives in `evals/`, not here. Run `bin/eval --open` from
the `ellua/` root to fetch Wikimedia/public fixtures, render the suite, and
overwrite `evals/out/eval.html`.
