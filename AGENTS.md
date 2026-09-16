# Agent Instructions

Read `CLAUDE.md` for build, test and architecture. Follow-up work is tracked in
`docs/SCENE.md` (renderer) and `CHECKS.md` (lint/check tiers) — no issue tracker.

## Session completion

1. Run the gates: `cargo build --release` + `bin/build-native`, then
   `bin/golden compare` (scene path, the default) and `bin/lint-tests`.
   `CADENCE_SCENE=0 bin/golden compare` checks the love fallback and needs the
   vendored LÖVE 12 fork to match (Homebrew 11.5 differs on fonts).
2. Commit, `git pull --rebase`, `git push`, `git status` must be up to date.
3. Leave a hand-off note in the commit body: what changed, what is left.

## Non-interactive shell commands

`cp`, `mv`, `rm` may be aliased to `-i` on some systems and will hang an agent.
Use `cp -f`, `mv -f`, `rm -f` / `rm -rf`, `ssh -o BatchMode=yes`,
`HOMEBREW_NO_AUTO_UPDATE=1 brew …`, `apt-get -y`.
