---
paths:
  - "adapters/**/*.cna"
---

# Red Robin — Cobalt Strike Aggressor (.cna) Rules

`adapters/cobaltstrike/redrobin.cna` runs in the CS Client (Sleep language), not the daemon. It is
the sole pre-flight gate + capture hook.

## Sleep gotchas (hard-won)

- **`hasmatch` is a STATEFUL iterator** — it caches its Matcher by string+pattern and toggles
  true/false across successive identical strings; used as a one-shot boolean it caused a fail-open
  (#144). Use stateless `ismatch '.*PATTERN.*'` (anchored) or `indexOf`. See `redrobin.cna:197`.
- In `redrobin.cna` the operator command line is hook arg **`$3`** (command name = `split(' ', $3)[0]`).
  The file uses **no `$0`/`$2` alias-arg parsing**; the only `substr` (~`redrobin.cna:325`) strips the
  trailing `" *"` elevated-user marker from `binfo`, not args.
- CS beacon hooks carry an **undocumented `$5` correlation id** — identical across the
  `input`/`tasked`/`output` of one command, unique per command. `beacon_output` `$4` = job id.
- Output observation uses `set BEACON_OUTPUT` (a filter hook returning `$2`), not `on beacon_output`.
- `alias` (parser plane) and `&b*` Sleep functions are **independent namespaces** — no recursion.

## Workflow rules

- **A `.cna` edit requires a Script Manager reload in CS** to take effect (a stale script looks like a
  code bug — it isn't).
- **`.cna` changes are validated ONLY by a live-beacon operator smoke, never by CI.** See the
  `smoke-test` skill.
