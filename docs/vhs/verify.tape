Output docs/images/verify.gif
Set Shell "zsh"
Set FontSize 16
Set Width 1280
Set Height 720
Set Theme "Catppuccin Mocha"
Set Padding 24

Hide
Type "cd evals/projects/launch-spot"
Enter
Sleep 300ms
Show

Type "# tier-0 compile in ~30ms"
Enter
Sleep 400ms
Type "../../bin/cadence verify comps/launch.lua --json --wasm-only"
Enter
Sleep 2s
