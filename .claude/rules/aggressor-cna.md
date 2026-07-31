---
paths:
  - "adapters/**/*.cna"
---

# Red Robin — Cobalt Strike Aggressor (.cna) Rules

`adapters/cobaltstrike/redrobin.cna` runs in the CS Client (Sleep language), not the daemon. It is
the **capture hook** — observe-only. It emits `beacon.command_input` / `beacon.command_output` /
`beacon.initial` to `POST /v1/events` and never touches the command path.

## The invariant

**The adapter captures; the core advises.** The `.cna` contains **zero `alias` overrides** — the 28
that once intercepted the operator's commands went with the pre-flight gate (2026-07-26). Do not add
one back: an `alias` puts Red Robin in front of the operator's keystrokes, which is exactly the
coupling this adapter no longer has. Observation hooks only.

## Sleep gotchas (hard-won)

- **`hasmatch` is a STATEFUL iterator** — it caches its Matcher by string+pattern and toggles
  true/false across successive identical strings; used as a one-shot boolean it caused a fail-open
  (#144). Use stateless `ismatch '.*PATTERN.*'` (anchored) or `indexOf`. See the `NOTE` in
  `set BEACON_OUTPUT` and the error-classification chain in `redrobin_http_post`.
- The operator command line is `on beacon_input` arg **`$3`** (command name = `split(' ', $3)[0]`);
  `$2` = operator nickname. There is no alias-arg parsing; the file's only `substr`
  (in `redrobin_emit_beacon_initial`) strips the trailing `" *"` elevated-user marker from
  `binfo($bid, "user")`, not args.
- CS beacon hooks carry an **undocumented `$5` correlation id** — identical across the
  `input`/`tasked`/`output` of one command, unique per command. `BEACON_OUTPUT` `$4` = job id.
- Output observation uses `set BEACON_OUTPUT` (a filter hook that MUST `return $2`), not
  `on beacon_output`. It skips re-emitting Red Robin's own `[Red Robin]` lines — otherwise CS treats
  our own output as beacon output and the hook re-POSTs it (self-ingestion loop).
- `fork{}` builds a **fully isolated scope** — top-level globals and direct scalar assignments are
  invisible inside it. This is why the 30s heartbeat `fork{}` is commented out; re-enabling it needs
  `lambda(..., \$_http => \$REDROBIN_HTTP)` to capture variables into the closure (#110).

## Workflow rules

- **A `.cna` edit requires a Script Manager reload in CS** to take effect (a stale script looks like a
  code bug — it isn't).
- **`.cna` changes are validated ONLY by a live-beacon operator smoke, never by CI.** See the
  `smoke-test` skill.
