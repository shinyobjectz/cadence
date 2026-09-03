# skills/ — agent skills layer

Cadence is authored by agents first, humans second. This directory is the
agent-facing knowledge: the **cadence** skill (install via skills.sh) plus
vendored third-party skills it composes with.

## Install

```bash
npx skills add shinyobjectz/cadence
```

Registry: [skills.sh](https://skills.sh) · manifest: [`skills.sh.json`](../skills.sh.json)

## Layout

```
skills/
├── README.md
├── cadence/                 ← official skill (skills.sh)
│   ├── SKILL.md
│   ├── references/
│   └── evals/
├── ellua/                   ← legacy alias (same content path during transition)
└── vendor/
    └── elevenlabs-skills/   ← upstream, unmodified
```

## Rules

- **`vendor/elevenlabs-skills/` stays byte-identical to upstream.**
- When the Cadence API changes, update `skills/cadence/` in the same PR.
- Prefer `cadence` naming in new docs; `ellua` module alias remains in code.
