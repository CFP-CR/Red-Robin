> **Status note (2026-07-08):** This is the v1.2 *design* spec (authored 2026-05-10). Some dates and
> scope statements are historical — v0.1 shipped to `main` 2026-06-01. For current canonical API
> surfaces (event types, routes, enums) see `.claude/rules/wire-protocol.md`; for the shipped state
> see the repo git history. Do not "fix" this doc's internal contradictions here — that is a separate
> reconciliation task.

---

---
title: "Red Robin — Architecture Design Document"
type: "spec"
status: "draft-v1.2"
created: "2026-05-08"
updated: "2026-05-10"
related: "[[red-robin-copilot]]"
tags:
  - "#domain/red-team"
  - "#domain/ai-engineering"
  - "#domain/ai-security"
---
# Red Robin — Architecture Design Document

**Date:** 2026-05-10
**Status:** Draft v1.2 — two consecutive audits (internal + external) applied. Major architectural decision: two-tier verdict protocol (§1.2.1) — Layer A sync within 150ms, Layer B async retroactive. Resolves Sleep HTTP latency constraint without breaking the "no friction" thesis.
**Brainstorm session:** 2026-04-29 → 2026-05-08
**Verification passes:** 2026-05-10 (3 audits: bytecode + research + coherence)
**Target deadlines:**
- Red Team Village @ DEF CON 34: **2026-05-31**
- La Villa @ DEF CON 34: **2026-06-01**

> **STATUS NOTE — 2026-07-26 (supersedes parts of this document).** The synchronous OPSEC
> **pre-flight gate was removed**: Layer A (Sigma/YAML rules + the `rules/` pack), Layer B (LLM
> judge), the verdict pipeline, `VerdictAction`, the `opsec.verdict` event, `POST /v1/verdict/quick`,
> and all 28 Aggressor `alias` overrides are deleted. **Red Robin is advisory-only** — the adapter
> *captures* (`beacon.command_input` / `beacon.command_output` / `beacon.initial` → `POST /v1/events`)
> and the core *advises* in the TUI chat; nothing intercepts the operator's command path. This
> document is retained as the **historical v1.2 design record** and is deliberately NOT rewritten;
> read §Componente 3 and §Componente 4 together with their status notes below.

---

## 1. Mission and Thesis

**Project:** Red Robin (anteriormente "The Operator's Co-Pilot")
**Mission:** AI companion for red team operators, agnostic to C2 framework, with open event protocol.

### Thesis

> El futuro del red teaming no es autónomo. Es augmented. Una herramienta que hace al operador humano mejor, sin reemplazarlo.

**Diferenciación vs PentAGI / XBOW:**
- Ellos reemplazan al operador (autónomos)
- Red Robin amplifica al operador (companion + memoria persistente + OPSEC awareness + engagement-aware suggestions)

### Why this matters

Red Robin no es informativo, es **operativo.** El operador ve en todo momento:
- Working memory del engagement (hosts, accounts, defenses, tools)
- **Engagement progress** contra objectives (kill chain, milestones, ROE)
- OPSEC verdicts pre-flight con severity escalada
- Chat con contexto persistente

**Esa diferencia entre "tool informativo" y "tool operativo" es el insight central del talk.**

---

## 2. Architectural Principles

1. **Core agnostic + adapters per-C2** — el daemon no sabe nada de Cobalt Strike. Adapters traducen entre C2 nativo y protocolo Red Robin.
2. **Operador nunca reemplazado** — Red Robin observa, advierte, sugiere. Nunca ejecuta.
3. **Open standard with reference implementations** — protocolo público, reference adapters mantenidos por equipo, comunidad extiende.
4. **Production-grade without over-engineering** — cada campo y cada mecanismo justifica su existencia con caso de uso real. YAGNI ruthless.
5. **Operator owns the data** — todo vive en la máquina del operador. Cero exfil, cero teamserver footprint.

---

## 3. System Topology

```
┌─────────────────── OPERATOR'S MACHINE ───────────────────────────┐
│                                                                  │
│   ┌──────────────────────────────────┐                           │
│   │          CS Client               │                           │
│   │  ┌────────────────────────────┐  │                           │
│   │  │  Operator console          │  │                           │
│   │  └──────────────┬─────────────┘  │                           │
│   │                 ▼                │                           │
│   │  ┌────────────────────────────┐  │                           │
│   │  │  redrobin.cna (Aggressor)  │  │                           │
│   │  │  - alias overrides (gate)  │  │                           │
│   │  │  - set BEACON_OUTPUT (obs) │  │                           │
│   │  │  - popup beacon (right-click) │                           │
│   │  └──────┬─────────────────────┘  │                           │
│   │         │ if PASS: &bmimikatz()  │                           │
│   │         │ ─────────────────────► (native CS RPC to TS)       │
│   │         │                        │                           │
│   │         │ HTTP localhost  ▲      │                           │
│   │         │  (gate query +  │      │                           │
│   │         │   output push)  │      │                           │
│   │         ▼                 │      │                           │
│   └─────────│─────────────────│──────┘                           │
│             │                 │                                  │
│   ┌─────────▼─────────────────│──────────────────────────┐       │
│   │              RED ROBIN DAEMON                        │       │
│   │  ┌────────────────────────────────────────────────┐  │       │
│   │  │  CS ADAPTER  (sole C2 boundary)                │  │       │
│   │  │  - Aggressor side: HTTP ingress/egress to .cna │  │       │
│   │  │  - REST API side: client to CS Team Server     │──┼──┐    │
│   │  │  - Wire protocol normalizer (envelope ↔ C2)    │  │  │    │
│   │  └────────────┬────────────────────────┬──────────┘  │  │    │
│   │  envelopes ⇕  │  (the contract)        │  envelopes ⇕   │    │
│   │               ▼                        ▼             │  │    │
│   │  ┌────────────────────────────────────────────────┐  │  │    │
│   │  │  RED ROBIN CORE  (C2-agnostic)                 │  │  │    │
│   │  │  - OPSEC engine (Layer A YAML + Layer B LLM)   │  │  │    │
│   │  │  - Memory (5 layers)                           │  │  │    │
│   │  │  - KB index + chat                             │  │  │    │
│   │  │  - TUI service                                 │  │  │    │
│   │  └────────────────────────────────────────────────┘  │  │    │
│   └──────────────────────────────────────────────────────┘  │    │
│                                                             │    │
│   ┌─────────┐                                               │    │
│   │   TUI   │ ◄── WebSocket / IPC con Core                  │    │
│   └─────────┘                                               │    │
│                                                             │    │
└─────────────────────────────────────────────────────────────│────┘
                                                              │
                  ┌───────────────────────────────────────────┘
                  │ REST API (CS 4.12+)
                  │ - read state (GET /beacons, /tasks)
                  │ - dispatch TUI-originated commands
                  │ - emergency clearCommandQueue (kill switch)
                  ▼
        ┌──────────────────────┐
        │   CS Team Server     │
        │   (DO / AWS)         │
        └──────────┬───────────┘
                   │
                   ▼
        ┌──────────────────────┐
        │  Target Host         │
        │  └─ Beacon           │
        └──────────────────────┘
```

**Critical architectural property:** Red Robin **Core** speaks the wire protocol only. **Adapters** are the sole boundary that knows about C2 protocols (Aggressor `.cna`, REST API, gRPC for Sliver, etc.). Swapping the C2 changes only the adapter — Core is untouched.

**Three flow paths, all routed through the adapter:**

| Path | Origin | Mechanism | Through Adapter? |
|---|---|---|---|
| **1. Console gate** | Operator types in CS console | `.cna` alias intercepts → adapter → core (verdict) → adapter → `.cna` (PASS = call `&bmimikatz()`, BLOCK = `return`) | Yes (both directions) |
| **2. TUI dispatch** | Operator types in Red Robin TUI | Core (verdict) → adapter → CS REST API `POST /consoleCommand` | Yes (egress) |
| **3. Output observation** | Beacon output arrives at CS Client | `set BEACON_OUTPUT` hook in `.cna` → adapter → core (memory update) | Yes (ingress) |

**Implications:**
1. **Core has zero C2 credentials.** Bearer tokens, JWT for REST API, beacon IDs, Aggressor primitives — all encapsulated inside the adapter. Core cannot execute commands even if compromised.
2. **Path 1 gate works offline-to-teamserver.** The alias gate lives entirely on the operator's machine. If the teamserver is unreachable, the gate still functions; commands queue locally as CS normally handles.
3. **Path 1 command flow stays native.** When the alias calls `&bmimikatz()` (the Sleep function, not affected by the alias plane), the command goes from CS Client to Team Server via the CS-native RPC channel — Red Robin is OUT of the data path. The gate is a pre-dispatch filter, not a proxy.
4. **Adapter manifest declares capabilities** (see §7). Core uses adapter features only when declared (`preflight_block_console`, `tui_dispatch`, `output_observe`).

---

## 4. Component Architecture (8 components, all locked)

### Componente 1 — Adapters (sole C2 boundary)

**Decisión: Aggressor `alias` overrides + REST API client + `set BEACON_OUTPUT` observation, todo dentro del adapter. Core jamás habla protocolo C2.**

#### 1.1 Adapter contract (the boundary)

Cada adapter es un package autocontenido. El core importa adapters por interfaz uniforme; ningún C2-specific detail (REST endpoints, Aggressor primitives, gRPC stubs) escapa del package.

```
adapters/cobaltstrike/
├── redrobin.cna           # Aggressor — corre en CS Client
├── adapter.py             # Python — corre en Red Robin daemon
├── manifest.toml          # capabilities + version + metadata
├── aliases/               # alias body templates por comando
│   ├── tier1_gate.sleep
│   └── tier2_warn.sleep
└── tests/                 # contract tests
```

**Wire protocol envelopes** (ver §4 Componente 7) son el único canal entre adapter y core. Adapter normaliza eventos C2-específicos a envelopes; core ve solo envelopes.

#### 1.2 Path 1 — Console gate via Aggressor `alias`

**Mecanismo verificado** (decompilado de `cobaltstrike-client.jar` 4.12 + the block-and-replace pattern is production-proven in SpecterOps OPSEC Profiles since 2018; the wrap-and-forward pattern (`alias mimikatz { if (gate) { &bmimikatz($1, $2); } }`) is supported by the same mechanism — confirmed via `aliasmanager.fireCommand` bytecode at `aliasmanager.class:222` — and demonstrated on a per-command basis in production scripts like `mgeeky/cobalt-arsenal/cwd-in-beacon-status-bar.cna` (`alias cd → bcd($1, $2)`). Red Robin's `redrobin.cna` will be the first production deployment at the 27-alias scale; Phase 1 spike validates):

El operador tipea `mimikatz logonpasswords` en la consola CS. El parser de Aggressor chequea aliases ANTES del dispatcher built-in. Si existe `alias mimikatz {...}`, ejecuta ese código en lugar del comando original. El cuerpo del alias decide qué hacer:

```sleep
# Cerebro: una sub central, queries al daemon vía HTTP localhost
sub redrobin_gate {
    local('$bid $cmd $raw $verdict');
    ($bid, $cmd, $raw) = @_;
    $verdict = redrobin_query($bid, $cmd, $raw);   # HTTP POST a daemon
    if ($verdict['action'] eq "BLOCK") {
        berror($bid, "[Red Robin] " . $verdict['reason']);
        return $false;                              # comando muere acá
    }
    if ($verdict['action'] eq "WARN") {
        blog($bid, "[Red Robin] WARN: " . $verdict['reason']);
    }
    return $true;
}

# Alias stubs (uno por comando gated, 4-5 LOC c/u)
# CANONICAL SAFE PATTERN: extract raw arg portion from $0 to control arg parsing.
# Aggressor `$2` is the FIRST tokenized argument (NOT entire arg string —
# pycobalt aggressor.md confirmed); `$3`, `$4`, ... are subsequent tokens.
# Quoted strings group into one token. To avoid silent arg-dropping on
# multi-arg commands (e.g. `mimikatz logonpasswords privilege::debug`),
# all 27 stubs use substr($0, strlen("<cmd> ")+1) + Sleep function call.
alias mimikatz {
    if (redrobin_gate($1, "mimikatz", $0)) {
        local('$args');
        $args = substr($0, strlen("mimikatz ")+1);
        &bmimikatz($1, $args);                      # función Sleep — NO recurse
    }
}
alias dcsync {
    # &bdcsync signature: (bid, dc_hostname, [user]). Use explicit token split.
    if (redrobin_gate($1, "dcsync", $0)) {
        local('$rest @parts');
        $rest = substr($0, strlen("dcsync ")+1);
        @parts = split(' ', $rest);
        if (size(@parts) >= 2) {
            &bdcsync($1, @parts[0], @parts[1]);
        } else {
            &bdcsync($1, @parts[0]);
        }
    }
}
# ... ~26 stubs para Tier 1 (built-ins + wrappers) + Tier 2
```

**Alias name ↔ Sleep symbol mapping** (verified contra `BeaconTaskBridge` bytecode):

| Console command (alias name) | Sleep function | Notes |
|---|---|---|
| `mimikatz` | `&bmimikatz` | direct |
| `dcsync` | `&bdcsync` | sig (bid, dc, [user]) |
| `hashdump` | `&bhashdump` | direct |
| `logonpasswords` | `&blogonpasswords` | top-level Beacon cmd |
| `execute-assembly` | `&bexecute_assembly` | **underscore in Sleep** |
| `inline-execute` | `&binline_execute` | **underscore in Sleep**; CS 4.12+ |
| `kerberos_ticket_use` | `&bkerberos_ticket_use` | same |
| `psexec` | `&bpsexec` | + variants `_command`, `_psh` |
| `wmi`, `winrm`, `jump`, `spawn`, `bypassuac`, `getsystem`, `elevate`, `inject`, `dllinject`, `psinject` | `&b<name>` | direct |
| `shell`, `run`, `execute`, `powershell`, `powerpick` | `&b<name>` | direct (wrappers Tier 1B) |

**Removed from Tier 1A:** `make_token` — verified NOT a top-level Beacon command (no `&bmake_token` in bytecode). Make Token is GUI-only via `openMakeTokenDialog()`. Coverage in v0.1 goes via `popup beacon` rebuild (which intercepts the GUI dialog action), not console alias.

#### 1.2.1 Two-tier verdict protocol (Sleep HTTP latency mitigation)

**Architectural constraint:** Sleep has no native async HTTP. `mgeeky/httprequest.cna` (the canonical Sleep HTTP base) wraps `java.net.URLConnection` which is synchronous-blocking. If the alias body waits for a full daemon round trip, the Aggressor event thread blocks for the entire duration — operator sees the CS console freeze.

**Layer B p95 SLA is <3s**. A 3s console freeze on every novel command would destroy the "no friction" thesis of Red Robin (operator-visible UX problem). Path 1 must NOT block more than ~150ms.

**Solution: two-tier verdict protocol.** The alias body queries `localhost:7777/v1/verdict/quick` with a hard timeout. The daemon:

1. Runs Layer A (YAML rules) synchronously — deterministic, <100ms p95
2. Returns immediately with one of three responses:
   - `PASS` → alias calls `&b<cmd>()` and command proceeds
   - `BLOCK` → alias prints `berror` and returns (no dispatch)
   - `ESCALATE` → Layer A had no match OR matched but rule needs Layer B; daemon dequeues for async Layer B processing AND alias forwards command (fail-open) AND daemon will post retroactive verdict to TUI when Layer B finishes

```sleep
sub redrobin_gate {
    local('$bid $cmd $raw $verdict');
    ($bid, $cmd, $raw) = @_;
    # Hard timeout 150ms — daemon must return within budget or alias fails open
    $verdict = redrobin_query_quick($bid, $cmd, $raw, 150);
    if ($verdict['action'] eq "BLOCK") {
        berror($bid, "[Red Robin] " . $verdict['reason']);
        return $false;
    }
    if ($verdict['action'] eq "WARN") {
        blog($bid, "[Red Robin] WARN: " . $verdict['reason']);
    }
    if ($verdict['action'] eq "ESCALATE") {
        # Layer B is now running async; will post retroactive verdict to TUI.
        # Alias forwards command (fail-open) — Path 3 (output observation)
        # is the safety net if Layer B returns DANGER too late.
        blog($bid, "[Red Robin] novel pattern — Layer B reviewing...");
    }
    return $true;
}
```

**Trade-offs documented:**

| Aspect | This design (Option A) | Alternative B (Sleep `fork` + semaphore) | Alternative C (accept 3s freeze) |
|---|---|---|---|
| Operator UX | ✅ <150ms on every command | ❌ still blocks operator's command line | ❌ visible 3s freeze on novel |
| Aggressor event thread | ✅ never freezes | ⚠ released but command still waits | ❌ frozen ≤3s |
| Layer B coverage | ⚠ retroactive (Path 3 catches if late DANGER) | ✅ pre-flight | ✅ pre-flight |
| Implementation complexity | Medium (async daemon side) | High (semaphore correctness in Sleep) | Low |

**This design IS coherent with the §1.4 coverage matrix safety net**: Layer A handles ~90% of events fast; Layer B handles novel patterns asynchronously with retroactive correction; Path 3 catches anything that slipped through. The 10% of events that escalate are exactly the events the operator should be MOST careful about — for those, a 1-2s retroactive WARN in the TUI (after the command already went to teamserver) is acceptable because:

- Path 3 captures output regardless — daemon sees what happened
- TUI displays "RETROACTIVE DANGER: command Y triggered post-dispatch" — operator can react (abort follow-ups, change tactic)
- Layer A bootstrap pack must be comprehensive enough that all KNOWN dangerous commands match Layer A → never escalate → pre-flight gate intact for known-bad
- Layer B's retroactive role is for novel/contextual patterns where the LLM finds risk Layer A missed

**Critical implementation requirement (Phase 1 spike):** validate that `redrobin_query_quick` returns within 150ms p95 against Layer A engine. If 150ms is too tight, raise to 250ms but no higher — past 250ms the operator perceives lag.

#### 1.2.2 Por qué NO hay recursión (clave técnica — verified contra docs oficiales):

`alias` y `&b*` viven en **dos planos distintos**:

| Plano | Qué intercepta `alias` |
|---|---|
| **Console parser** | `alias mimikatz {...}` redefine el comando `mimikatz` que el parser de consola dispatcha |
| **Sleep function** | `&bmimikatz` es la FUNCIÓN Sleep que encola el task. `alias` NO la toca. |

Llamar `&bmimikatz($1, $2)` desde dentro del alias body invoca la función Sleep directamente, sin volver al parser de consola → no hay recursión.

**Nota sobre el bang-suffix `!`:** En Aggressor, `&bmimikatz!($1, $2)` es la variante **quiet** — suprime el "Tasked beacon to run X" del console output. Es ortogonal al gate; usable cuando Red Robin imprime su propia versión bonita del mensaje. **v0.1 lo mantiene SIN `!`** para no esconder la console nativa del operador.

**`$0` = raw input incluye flags**, permitiendo pattern-matching completo en el daemon. Si el alias retorna sin llamar `&bmimikatz`, el comando NUNCA llega al teamserver — verificado contra el dispatcher en `BeaconTaskBridge.class`.

**Coverage v0.1 — wrappers + built-ins:**

El operador a menudo no tipea `mimikatz` directo; usa **wrappers** que ejecutan herramientas externas (NetExec, Rubeus, Invoke-Kerberoast, etc.) en el host del beacon. Por eso el Tier 1 cubre AMBOS: comandos built-in obvios + wrappers con arg-pattern detection en el daemon.

| Tier | Type | Comandos | Cuenta | Daemon gating logic |
|---|---|---|---|---|
| **Tier 1A (built-ins peligrosos)** | Direct | `mimikatz`, `dcsync`, `hashdump`, `logonpasswords`, `kerberos_ticket_use`, `psexec`, `wmi`, `winrm`, `jump`, `spawn`, `bypassuac`, `getsystem`, `elevate`, `inject`, `dllinject`, `psinject` | ~16 | Pattern fija al comando — DANGER por defecto, contextual con working memory |
| **Tier 1A-GUI (Make Token via popup)** | GUI dialog | `make_token` is not a console command in CS 4.12 — `openMakeTokenDialog()` exposes it via right-click menu. Covered via `popup beacon` rebuild, NOT console alias. | 1 | Popup body queries daemon before calling `&bloginuser`/`&brunas` underlying primitive |
| **Tier 1B (wrappers con arg-pattern)** | Wrapper | `shell`, `run`, `execute`, `execute-assembly`, `inline-execute`, `powershell`, `powerpick` | ~7 | Daemon inspecciona `$0` cruda para detectar: `nxc`, `crackmapexec`, `Rubeus`, `Invoke-Kerberoast`, `Invoke-Mimikatz`, `SharpHound`, `lazagne`, `seatbelt`, `winpeas`, etc. |
| **Tier 2 (warn)** | Direct | `screenshot`, `keylogger`, `screenwatch`, `browserpivot`, `socks` | ~5 | Aviso visual + log |
| **Pass-through** | Direct | `pwd`, `cd`, `ls`, `ps`, `getuid`, `jobs`, `sleep`, `mode`, `note`, `clear`, `help` | sin alias | Pre-flight skip; Path 3 observa el output igual (memoria) |

**Ejemplo Tier 1B wrapper:**

```sleep
alias shell {
    local('$verdict');
    # daemon recibe $0 = "shell nxc ldap 192.168.1.0/24 -u user -p pass"
    # daemon pattern-matches: nxc + ldap target + creds → WARN (LDAP enum noisy + plaintext creds in cmdline)
    $verdict = redrobin_query($1, "shell", $0);
    if ($verdict['action'] eq "BLOCK") {
        berror($1, $verdict['reason']);
        return;
    }
    if ($verdict['action'] eq "WARN") {
        blog($1, $verdict['reason']);
    }
    &bshell($1, substr($0, strlen("shell ")));
}
```

El daemon mantiene un catálogo de **arg-patterns por wrapper** en `infrastructure/opsec/wrapper_patterns.yaml`:

```yaml
shell:
  - pattern: 'nxc\s+(ldap|smb|wmi|rdp|ssh)\s+'
    severity: WARN
    reason: "NetExec scan — high-signal protocol enum"
  - pattern: '(mimikatz|gsecdump|wce|pwdump)\.exe'
    severity: DANGER
    reason: "Known credential dumper executable name on cmdline"
execute-assembly:
  - pattern: 'Rubeus\.exe\s+(kerberoast|asreproast|dump|tgtdeleg)'
    severity: DANGER
    reason: "Rubeus credential extraction"
powershell:
  - pattern: 'Invoke-(Mimikatz|Kerberoast|ASREPRoast|PowerDump)'
    severity: DANGER
    reason: "Empire-style PowerShell credential cmdlet"
```

**Esto es expresivo y mantenible** — operador agrega patrones sin tocar código Python ni `.cna`.

**Tamaño esperado del `.cna`:** estimado ~225 LOC (30 LOC sub central + ~27 alias stubs × 5 LOC + ~50 LOC popup beacon rebuild + ~20 LOC HTTP client). Validar en Phase 1 spike.

**Popup beacon rebuild** — los menús right-click en CS Client bypassean la consola. El adapter reconstruye `popup beacon { ... }` espejando los aliases para cubrir GUI clicks. Patrón shipping en SpecterOps OPSEC Profiles.

#### 1.3 Path 2 — TUI dispatch via REST API

Cuando el operador originates un comando desde el TUI de Red Robin (no desde CS console), el flow es:

```
TUI (operator input) → Core (verdict) → Adapter → CS REST API
                                        │
                                        └─ POST /api/v1/beacons/{bid}/consoleCommand
                                           con bearer JWT
```

El adapter mantiene el cliente REST API y maneja JWT lifecycle. Core nunca ve la API.

**CS 4.12 REST endpoints relevantes** (verificados contra OpenAPI spec):
- `POST /api/auth/login` → JWT bearer token
- `POST /api/v1/beacons/{bid}/consoleCommand` — raw line dispatch
- `POST /api/v1/beacons/{bid}/execute/*` — structured commands (preferred over consoleCommand)
- `GET /api/v1/beacons` — list current beacons (state recovery on adapter startup)
- `GET /api/v1/tasks/{taskId}` — poll task status (no push/webhook in 4.12)
- `POST /api/v1/beacons/{bid}/clearCommandQueue` — emergency kill switch (flushes ALL queued tasks, blunt)

#### 1.4 Path 3 — Output observation

Beacon output llega al CS Client desde el teamserver. El adapter usa primitivos del `.cna` complementarios:

**Filter hooks** (passthrough-safe, modifican rendering del console):
- **`set BEACON_OUTPUT`** — cada bloque de output del beacon. Variantes documentadas: `BEACON_OUTPUT_ALT`, `BEACON_OUTPUT_PS`.
- **`set BEACON_TASKED`** — el mensaje "Tasked beacon to run X" antes de mostrarse en consola.
- **`set BEACON_ERROR`** — el mensaje de error antes de mostrarse en consola.

Los tres existen en `default.cna` canónico de CS 4.12 (líneas 786, 803, 808 verificadas en decompiled jar). Son undocumented pero stable — production-deployed en SpecterOps OPSEC Profiles y mgeeky scripts.

**Events** (observation-only, no afectan rendering):
- **`on beacon_tasked`** — dispara cuando un task se encola; útil para reconstruct timeline.
- **`on beacon_error`** — dispara cuando hay error post-dispatch.
- **`on beacon_input`** — dispara cuando un operador (cualquiera, broadcast) tipea un comando; ver §6 T11 para uso en unmanaged-operator detection.

```sleep
# Output capture — filter hook (primary)
set BEACON_OUTPUT {
    redrobin_push_event("beacon_output", $1, $2);
    return $2;   # passthrough — no rompe consola del operador
}

# Tasking observation — event (preferred for timeline)
on beacon_tasked {
    redrobin_push_event("beacon_tasked", $1, $2);
}
```

**Por qué events sobre filters para tasking/error**: filter hooks (`set BEACON_TASKED`/`BEACON_ERROR`) son canales de **rendering** — su return value modifica el display string. Events (`on beacon_tasked`/`on beacon_error`) son canales de **observación pura** — no requieren passthrough discipline. Para Path 3 (memory capture sin alterar UX) los events son cleaner. Los filter hooks se reservan para casos de rendering tweaks (e.g., tagged console output con metadata Red Robin) — feature v0.2 candidate.

**Path 3 es el safety net** — cubre TODO lo que el gate pre-flight no atrapa.

#### 1.4.1 Coverage matrix — qué cubre cada path

Combinando los 3 paths, el adapter alcanza la siguiente cobertura por escenario operativo:

| Operator action | Pre-flight gate (Path 1) | TUI dispatch gate (Path 2) | Post-exec observation (Path 3) | Memoria actualizada |
|---|---|---|---|---|
| `mimikatz logonpasswords` en CS console | ✅ alias mimikatz (Tier 1A) | n/a | ✅ output captured | ✅ |
| `shell nxc ldap ...` en CS console | ✅ alias shell (Tier 1B) + arg-pattern detect en daemon | n/a | ✅ output captured | ✅ |
| `execute-assembly Rubeus.exe kerberoast` | ✅ alias execute-assembly (Tier 1B) + arg-pattern | n/a | ✅ | ✅ |
| `screenshot` en CS console | ✅ alias screenshot (Tier 2 warn) | n/a | ✅ | ✅ |
| `pwd` en CS console | ❌ pass-through (sin alias) | n/a | ✅ output captured retroactively | ✅ |
| Comando nuevo no listado en Tier 1/2 | ❌ pass-through | n/a | ✅ output captured | ✅ (Layer A puede emitir WARN retroactivo en próxima iteración) |
| Operador tipea `mimikatz` en TUI de Red Robin | n/a | ✅ core verdict ANTES de adapter | ✅ (cuando output regresa) | ✅ |
| Otro operador en mismo TS sin `redrobin.cna` | ❌ T11 | ❌ T11 | ✅ (si compartís beacon, su `BEACON_OUTPUT` llega via tu hook) | ✅ partial — sus comandos visibles en output stream |
| Operador tipea `nxc` en su Kali LOCAL (terminal, no CS) | ❌ fuera de scope del adapter | ❌ fuera de scope | ❌ fuera de scope | ❌ |

**El gate pre-flight es lo mejor (block antes de dispatch), pero NO es la única línea de defensa.** Path 3 garantiza que:

1. **Working memory siempre se actualiza** — hosts, accounts, defenses, tools observados, sin importar si hubo gate
2. **Episodic memory registra todo** — incluso comandos pass-through quedan en `events.jsonl` para post-engagement review
3. **Layer A puede emitir WARN retroactivo** — operador corrió `whoami /priv`; daemon detecta privilege escalation precursor en próximo verdict relacionado
4. **Layer B (LLM judge) tiene contexto completo** — el modelo razona contra TODO lo que el operador ha hecho, no solo lo gated
5. **Detection-engineering aware** — Sigma rule lookup post-hoc puede flaggear "esto que corriste hace 30 segundos matches T1003.001 SIGMA-A4F2 — el SOC probablemente ya lo vio"

**Diseño explícito**: pre-flight gate cubre los **comandos peligrosos conocidos** (Tier 1A + 1B con patrones explícitos). Post-exec observation cubre **todo lo demás** sin enumerar exhaustivamente. Esto evita el anti-pattern de "tenemos que aliasear los 200 comandos de Beacon" — solo aliaseamos los ~27 que realmente importan, y el safety net atrapa el resto.

#### 1.5 Por qué no MCP

- Core no ejecuta comandos contra el C2 desde su propio razonamiento (Path 2 nace de input explícito del operador en el TUI, no de un agente autónomo)
- MCP server sería plumbing redundante entre core y adapter
- El adapter ya es el "MCP equivalent" — exposes capabilities, encapsulates C2 protocol
- Si en v0.2+ se quiere exponer Red Robin a otros agentes (Claude Desktop, etc.), el MCP server vive AFUERA del adapter como cliente del wire protocol — invierte la relación, no la duplica

#### 1.6 Sliver-Mock (v0.1) y Sliver-Real (v0.1.1)

Para validar que el contrato del adapter es genuinely C2-agnostic, v0.1 ship con **dos adapters**: `cobaltstrike/` (real) y `sliver-mock/` (synthetic Linux + Acme scenarios). v0.1.1 target 2026-06-22 ship `sliver-real/` usando **protoc-generated Python client** desde el repo oficial de Sliver protos. SliverPy library's `master` branch HEAD is 2023-04-28; newer work exists on `v1.6.x-update` (HEAD 2024-11-25) but no release > v0.0.19 (Dec 2022) cuts that work. To avoid depending on an unreleased branch + accept SliverPy's own README warning ("Not all features in Sliver v1.5+ are supported yet"), we generate protoc stubs directly from `BishopFox/sliver/protobuf/` and pin a Sliver version in the adapter manifest.

### Componente 2 — LLM

**Decisión: Híbrido cloud-first MVP, local opt-in roadmap.**

- v0.1 MVP: Anthropic Claude (`claude-sonnet-4-6` para inferencia, `claude-haiku-4-5` para summarizer/distillation)
- Voyage AI (`voyage-4-large`) para embeddings — current SOTA en MTEB benchmarks (`voyage-3` está generation-behind y fuera del free tier post-MongoDB acquisition Feb 2025)
- Local LLM (Ollama / Llama 3.3) — roadmap v0.2, justified explícitamente como "MVP iteration speed; local for OPSEC-strict engagements"

**Trade-off honesto:** la promesa OPSEC del paper se modera. Operador asume que Anthropic ve eventos del engagement. Para engagements regulados (gov, finance), local-mode required — disponible v0.2.

### Componente 3 — Memory System

> **STATUS NOTE — 2026-07-26.** **L3 Episodic** is now sourced from `beacon.command_input` — one row
> per operator command — rather than from `opsec.verdict`, which no longer exists. `Episode` is
> narrowed to `(id, engagement_id, correlation_id, command, created_at)`. **L4 Tradecraft Graph
> remains unbuilt.** L1 / L2 / L5 are substantially as described. Layer B references below are
> historical.

**Decisión: B3 — 5 layers completos en v0.1.**

| Layer | Tecnología | Contenido |
|---|---|---|
| L1 Long-term | SQLite (`facts` table extendida) + Voyage embeddings + numpy vector store | Cross-engagement durable facts: rules sugeridas, lessons que escalaron a permanentes, KB indexed (vault + MITRE + público) |
| L2 Working Memory | JSON estructurado por engagement | Hosts, accounts, defenses, tools, **engagement profile** (objectives, scope, ROE), **engagement progress** (current phase, milestones, kill chain) |
| L3 Episodic | SQLite | Operator-tagged + auto-detected lessons del engagement actual |
| L4 Tradecraft Graph | SQLite con relaciones | Técnica → bypass → detection → mitigation → tool. Bootstrap pack desde MITRE ATT&CK + public OPSEC docs. |
| L5 Context Management | Sliding window + summary | Cuando engagement excede budget, resume turnos viejos |

**Sobre L4 (Tradecraft Graph) — calidad sobre velocidad:**
- El grafo solo aporta valor si la data está bien curada (técnica → bypass → detection chains validadas)
- Bootstrap inicial: MITRE ATT&CK relations (técnicas → mitigations + sub-techniques) + curated bypass chains de docs públicos
- Layer B (LLM judge) puede traversar el grafo para sugerencias del tipo "el operador tiene Sysmon en este host, ¿qué bypass para esta técnica?"
- Sin L4, el chat queda al nivel de "sugiero alternativas similares" via RAG. Con L4, queda al nivel de "estas son las mitigations específicas para esta técnica + estos son los bypasses conocidos."

**Engagement progress structure (parte de L2):**

```yaml
engagement:
  id: acme-q2-2026
  client: Acme Corp
  scope:
    in_scope: ["*.acme.local", "10.0.0.0/16"]
    out_of_scope: ["10.0.99.0/24", "*.acme-legal.com"]
  objectives:
    - id: obj_1
      goal: "Compromise primary DC"
      status: in_progress
      priority: high          # low | medium | high
      progress_pct: 60
      milestones: [...]
      pending: [...]
  current_phase: credential_access
  kill_chain_status: { recon: complete, ..., exfil: not_started }
  rules_of_engagement: [no destructive, no DoS, ...]
  # Mirror of operational state captured during the engagement
  hosts: [...]                  # observed hosts with OS, defenses, access level
  accounts: [...]               # captured credentials with provenance
  defenses_observed: [...]      # EDR/AV/Sysmon configurations detected
  tools_used: [...]             # operator's tools deployed (mimikatz, nanodump, Rubeus...)
```

**Auto-detección:** Layer B (LLM judge) infiere phase transitions y propone milestones. Operador confirma o overridea via `:rr milestone`, `:rr phase`, `:rr objective`.

### Componente 4 — OPSEC Engine

> **STATUS NOTE — 2026-07-26. THIS ENTIRE COMPONENT WAS REMOVED.** Layer A, Layer B, the verdict
> pipeline, the redactor, the scope enforcer, and the lesson promoter are all deleted — there is no
> `input_proposed` → verdict path any more, and no consumer of a verdict in either the `.cna` or the
> TUI. `Severity` (INFO/WARN/DANGER/ESCALATE) survives as the assistant's `Recommendation.opsec_risk`.
> Everything below is a historical design record.

**Decisión: Híbrido Layer A (YAML rules) + Layer B (LLM judge with RAG) + Severity D (escalating) + Adversarial "explain" mode.**

**Critical architectural property:** El engine consume **solo wire envelopes** (`input_proposed` events) y emite **solo verdict envelopes**. No conoce Aggressor, REST API, ni ningún detalle C2-específico. Los dos consumers del verdict — alias bodies en `.cna` (Path 1) y el TUI dispatch loop (Path 2) — viven en el adapter y en presentation, respectivamente. Esto permite tests del engine con mock adapters.

**Verdict pipeline order (deterministic stages, fail-fast):**

```
input_proposed event
        │
        ▼
┌─────────────────────────────┐
│ Stage 0 — Pass-through      │  → if cmd in passthrough_whitelist
│   skip-list (pwd, cd, ls…) │     emit verdict PASS, end
└─────────────────────────────┘
        │
        ▼
┌─────────────────────────────┐
│ Stage 1 — Scope enforcement │  → extract target host(s) from cmd args
│   (T9 ROE compliance)       │     (jump <host>, psexec @<host>, nxc <cidr>,
│                             │      shell with -h/-H/-target/--target/...)
│                             │     match against engagement.scope.out_of_scope
│                             │     → DANGER "ROE violation: <host> is out-of-scope"
│                             │     override requires reason logged
└─────────────────────────────┘
        │
        ▼
┌─────────────────────────────┐
│ Stage 2 — Layer A YAML rules│  → deterministic <100ms
│   bootstrap + community     │     emit PASS / WARN / DANGER / ESCALATE
└─────────────────────────────┘
        │
        ▼ (only if ESCALATE)
┌─────────────────────────────┐
│ Stage 3 — Layer B LLM judge │  → async, retroactive (see §1.2.1 two-tier protocol)
│   context: working memory   │     emit retroactive verdict to TUI
│   + episodic + KB RAG       │
└─────────────────────────────┘
```

**Stage 1 implementation:** target extraction is a small parser in `application/scope_enforcer.py` covering common Beacon command patterns (`jump <target>`, `psexec <target>`, `wmi <target>`, `winrm <target>`, `nxc <protocol> <cidr|host>`, `crackmapexec <protocol> <cidr|host>`, anything with `--target`/`-H`/`--host` flags). Hostname/IP/CIDR match against `engagement.scope.out_of_scope` patterns (supports glob and CIDR). Lives BEFORE Layer A so it always runs even if no rule matches.

**Existing `Triage` retired** — el patrón "interesting markers + debounce" se subsume en Layer A (más expressive). Las testes de triage se reescriben para Layer A.

**Layer A — Rules engine:**
- YAML files en `~/.redrobin/rules/` (operator-owned, hot-reload)
- **Bootstrap pack v0.1: ~12-15 reglas concretas** que shippean con el package (no folder vacío):
  - `mimikatz_logonpasswords.yaml` — match `mimikatz "sekurlsa::logonpasswords"` → DANGER (T1003.001 + lsass touch + auditd/Sysmon EID 10 trigger)
  - `dcsync_replication.yaml` — match `dcsync` con domain controller target → DANGER (T1003.006)
  - `vssadmin_list_shadows.yaml` — DANGER (high-signature ransomware precursor)
  - `kerberoast_setspn.yaml` — `setspn -q */*` o equivalente → WARN
  - `domain_admin_enumeration.yaml` — `net group "Domain Admins"` → WARN si first-time
  - `psexec_lateral.yaml` — psexec.exe o `jump psexec64` → DANGER en hosts con EDR conocido
  - `bypassuac_*.yaml` — WARN en presencia de cualquier EDR observado
  - `inject_lsass.yaml` — inject targeting lsass.exe PID → DANGER
  - `make_token_plaintext.yaml` — make_token con plaintext password → WARN
  - `unmanaged_powershell.yaml` — `powershell` sin `powerpick` o BOF → WARN (AMSI/script block)
  - `winrm_enable.yaml` — `winrm quickconfig` o `Enable-PSRemoting` → DANGER (persistent footprint)
  - `screenshot_persistent.yaml` — `screenwatch` >5min → WARN
  - `socks_external.yaml` — `socks <port>` con port común (80/443/8080) → INFO (encourage non-standard)
  - `runas_admin_creds.yaml` — `runas /user:*admin*` → WARN
  - `nbtstat_arp_sweep.yaml` — broad network discovery primitives → INFO
- Comunidad extiende vía pulls del repo (`rules/community/`)
- Layer B propone reglas auto-suggested basadas en episodic memory (operator approval gate)

**Layer B — LLM judge:**
- Razona contra Working Memory + Episodic Memory + KB retrieval
- (Tradecraft Graph deferred a v0.2 — Layer B compensa via RAG sobre KB)
- Usa engagement progress + objectives para contextualizar
- Casos: comandos novedosos, secuencias contextuales, explicación didáctica
- **Performance:** Anthropic prompt caching nativo via `cache_control: {type: "ephemeral"}` aplicado al system prompt + KB chunks recuperados (TTL 5min default, 1h opt-in para sesiones largas). El cache se invalida al cambiar engagement.

**Severity D (escalating):**
- INFO → annotation silenciosa, no interrumpe
- WARN → soft confirm (operador presiona Enter o `:rr <cmd>`)
- DANGER → hard block en Path 1 (alias retorna sin llamar bang-suffix); hard block en Path 2 (TUI no dispatcha al adapter). Override via `:rr override "<reason>"` con razón explícita registrada en episodic memory.

**Modes:**
- Default: pre-flight + post-exec
- Adversarial "explain" mode: `:rr explain` muestra perspectiva SOC sin bloquear

**Sensitive data handling (logs / persistence):**

Decisión locked: **redaction es minimal, operator-configured.**

- **Default behavior:** Red Robin no redacta nada automáticamente. Logs y persistencia capturan lo que llega del adapter sin filtros.
- **Operator opt-in:** `config.toml` tiene sección `[redaction]` donde el operator define patrones explícitos a enmascarar:

```toml
[redaction]
# Patterns are regex. Matched substrings replaced with [REDACTED:<label>].
patterns = [
    { pattern = '(?i)password\s*[=:]\s*\S+', label = "PASSWORD" },
    { pattern = '[A-F0-9]{32}:[A-F0-9]{32}', label = "NTLM_HASH" },
    # ... operator adds whatever they want
]
fields = ["beacon.command_output.output", "operator.question.text"]  # which event fields to scan
```

- **Filosofía:** Red Robin asume que el operator es el dueño/responsable de su data. **Maximum visibility, maximum responsibility.** Falsos positivos de redaction agresiva serían peor (operator no ve outputs reales) que el riesgo de logs con secrets ocasionales.
- **File permissions enforced:** todos los logs/DBs en `chmod 600`. Recomendación de full-disk encryption en README.
- **Burn-after-engagement:** deferred a v0.2. Para v0.1, operator borra manualmente con `redrobin engagement delete <slug>`.

**API key enforcement (mitigation T2 from Threat Model):**

Anthropic API key reading is **programmatically constrained** to environment variable only:

```python
# infrastructure/llm/anthropic_client.py
def _get_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY env var not set. "
            "Red Robin refuses to read API keys from config files for security."
        )
    return key

# config.toml loader explicitly REFUSES api_key field
def load_llm_config(toml_data: dict) -> LLMConfig:
    if "api_key" in toml_data.get("llm", {}):
        raise ValueError(
            "Found api_key in config.toml — this is not allowed. "
            "Set ANTHROPIC_API_KEY env var instead. "
            "See SECURITY.md for rationale."
        )
    # ... rest of config loading
```

Operator who tries to put API key in config.toml gets clear error message at startup, not silent acceptance.

### Componente 5 — Operator Interface

**Decisión: F refinado — extender `prompt_toolkit` TUI existente + inline `.cna` annotations en CS console. Textual deferred a v0.2 si se quiere mejorar polish.**

**Razón del downgrade vs E (Textual):** el draft del peer ya tiene TUI funcional con full-screen layout, suggestion pane, status bar, memory inspector via Float overlay. Migrar a Textual = full UI rewrite (~5-7d). Extender prompt_toolkit con engagement progress + chat panel = ~3-5d. El audit demostró que el draft no es CLI lineal — es TUI competente. Mockup sirve como visual target del layout, no como compromiso de tecnología.

**TUI (prompt_toolkit extendida) — qué se agrega al draft:**

1. **Working Memory pane permanente** (izquierda) — reemplaza/extiende suggestion pane con:
   - Engagement progress block (phase, objectives, kill chain mini-map)
   - Hosts/Accounts/Defenses/Tools/Lessons (existente, refactor)
2. **Event Stream pane** (centro) — extiende log pane existente con severity tagging
3. **Chat pane** (derecha) — nuevo panel con scrollback de conversación + citations
4. **Memory Inspector overlay** (Ctrl+I) — extiende existente con engagement detail + objectives + milestones timeline

**i18n:** TUI labels detectan locale del operador (env `LANG`); slash commands (`:rr ...`) en inglés siempre (universal).

**Inline `.cna` (en consola CS):** coordina con TUI. Severidad escalada visible:
- INFO: 1 línea annotation
- WARN: soft prompt sincrónico (Enter / `:rr alts` / `:rr explain` / `:rr skip`)
- DANGER: hard block, requires `:rr override "<reason>"`

**Canonical slash command set (operator-facing, v0.1):**

| Command | Context | Action |
|---|---|---|
| `:rr alts` | After WARN/DANGER | Show ranked alternatives in TUI chat |
| `:rr explain` | After WARN/DANGER | Show full verdict + episodic context |
| `:rr skip` | After WARN soft prompt | Cancel the proposed command (no dispatch) |
| `:rr override "<reason>"` | After DANGER hard block | Force-dispatch the blocked command; reason logged in episodic |
| `:rr exec <cmd>` | TUI input | Dispatch a command from TUI via Path 2 (adapter REST API) |
| `:rr promote "<lesson>"` | Any time | Promote an episodic lesson to longterm KB |
| `:rr milestone "<text>"` | Any time | Manually mark engagement progress |
| `:rr phase <name>` | Any time | Override auto-detected phase |
| `:rr objective <id> <pct>` | Any time | Update objective progress manually |
| `:rr inspector` (or Ctrl+I) | TUI | Open memory inspector overlay (full engagement state) |
| `:rr help` | Any time | List all commands with brief descriptions |

Slash commands are case-sensitive English (universal) regardless of the TUI label locale.

**Capability degradation per-adapter:** TUI lee adapter manifest y ajusta UX. Para Sliver (no preflight_block), severity en TUI sin inline blocking. Demo cinematográfico solo en CS por arquitectura del C2.

**Mockup:** `_Claude/redrobin-mockup/index.html` (sirve con `python -m http.server`). El mockup muestra **layout target** — la implementación real en prompt_toolkit usa box-drawing characters Unicode y density más alta que el mockup web; spatial structure se preserva.

### Componente 6 — Demo Environment

**Decisión: Red Team Playground (GOAD-Light en AWS) con fallback mock adapter + replay.**

**Plan A (default, 95% of cases):**
- GOAD-Light deployado vía Terraform existente (Red Team Playground production-grade desde 2026-03-27)
- Real AD lab con Sysmon + simulated EDR
- Operador ssh → CS Client → adapter → Red Robin daemon
- Engagement scenario "Acme Corp" (mismo del mockup) pre-cargado

**Plan B (fallback si DEF CON network falla):**
- Mock adapter local con replay de engagement real grabado
- Output simulado pero realista
- Honestidad transparente: anuncio en stage si activás fallback

**Pre-talk checklist:**
- GOAD-Light deployable en <10 min via Terraform
- `engagement.yaml` Acme Corp ready
- Mock adapter con replay grabado (5-10 min realistic session)
- Backup laptop pre-configured
- Personal hotspot como tercer fallback

**Demo storyboard (draft) — Acme Corp engagement, ~20 min total:**

| # | Scene | Duration | Commands / Actions | Talk cues |
|---|---|---|---|---|
| 0 | **Setup** (off-stage / pre-talk) | — | `redrobin engagement use acme-q2-2026` · `redrobin daemon start` · `redrobin tui` · CS Client open with `redrobin.cna` loaded | Verify GOAD-Light running. Smoke-test pre-flight intercept manually. |
| 1 | **Apertura — Sliver-Mock** | 2-3 min | Run `redrobin-sliver-mock --scenario linux_demo` (separate Linux scenario). TUI shows Sliver adapter connected, working memory updates from pregrabados events. | "Same daemon, same TUI, different C2. Protocol agnostic — demonstrated live." |
| 2 | **Switch to CS — Idle** | 30s | Switch CS Client to focus. TUI Working Memory empty (engagement reset). Engagement progress shows phase=not_started, 3 objectives at 0%. | "Now operating Acme Corp via Cobalt Strike. Watch the working memory populate." |
| 3 | **First beacon — Initial access** | 1-2 min | First beacon connects from DC01. `getuid` → output: `NT AUTHORITY\SYSTEM (admin)` | "Beacon checks in. Red Robin auto-detected initial_access milestone." |
| 4 | **INFO event — light recon** | 1-2 min | `whoami /priv` → output. INFO annotation in CS console + TUI event stream. Working memory updates user + privileges. | "Low-noise recon. Red Robin annotates without interrupting flow." |
| 5 | **WARN event — AD recon** | 2-3 min | `net group "Domain Admins" /domain` → WARN inline. Operator sees alternatives in TUI chat panel. Operator chooses `:rr alts` → executes BOF alternative. | "WARN — soft confirmation. Red Robin offered three alternatives, ranked by stealth. Operator chose BOF path." |
| 6 | **EDR observation** | 30s | `tasklist /v` → output reveals `CSAgent.exe` (CrowdStrike). Working Memory updates `defenses_observed`. | "Working Memory now knows DC01 has CrowdStrike. Future verdicts contextualize accordingly." |
| 7 | **DANGER event — vssadmin BLOCKED** | 3-4 min | Operator types `vssadmin list shadows` in CS console. `alias vssadmin` in `redrobin.cna` fires → queries daemon → receives `BLOCK` verdict → calls `berror($1, ...)` and **returns without calling the bang-suffix** → command never reaches teamserver. TUI shows full verdict + episodic context ("3/3 prior detections in similar context, avg 5.3min"). | **Cinematic moment.** Show the consola CS line `[!] [Red Robin] DANGER: VSS enumeration matches ransomware precursor TTP. T1490 + EID 8222 high-signal. Override: ':rr override <reason>'`. "Red Robin caught a command that would have burned the engagement in under 5 minutes. The command **never reached the teamserver** — the alias gate intercepted at the operator's CS Client." |
| 8 | **Operator chats** | 2-3 min | `:rr explain` → why is this DANGER? Then `:rr alts` → operator types in TUI chat: "tengo SYSTEM en DC01 con CrowdStrike. quiero dump creds sin tocar lsass. opciones?" → engagement-aware response with 4 ranked options + "obj_3 persistence still 0%, consider before pivot" | "Notice the response goes beyond TTPs — it reasons against engagement objectives. obj_1 60%, obj_3 0% → strategic recommendation." |
| 9 | **Cred extraction — clean execution** | 1-2 min | Operator runs `execute-bof nanodump.x64.o`. Output success. Working memory updates `tools_used: nanodump (no detection)`. Auto-detected milestone: credential_access advanced. | "Red Robin auto-detects milestones. kill_chain progress reflects in TUI." |
| 10 | **Memory inspector deep-dive** | 1-2 min | `Ctrl+I` → expanded view shows full engagement state: hosts, accounts (3 captured), defenses, tools used, lessons (auto + tagged), episodic context, kill chain | "All this state lives locally. Operator can audit, export, or share with team." |
| 11 | **Wrap — promote lesson + close** | 1 min | `:rr promote "nanodump invisible to CrowdStrike on Win 2022"` → migrated from episodic to longterm. Then `redrobin engagement close acme-q2-2026` → 12 lessons summary. | "What was learned today is available for future engagements. This is the augmented operator over time." |

**Total: ~18-22 min on stage.**

**Recording strategy:**
- Pre-record scene 1 (Sliver-Mock) for video reliability
- Live scenes 2-11 from CS Client connected to GOAD-Light
- Have asciinema recording of full engagement as "scene 12 backup" if any live scene breaks

**Speaker cues to script (per scene):**
- 1-line punchline per scene that lands the thesis (operator augmented vs replaced)
- Time-checkpoint markers ("if behind, skip scene 9")
- Backup pivot lines if demo glitches ("...as you can see Red Robin would have flagged this if our network cooperated")

### Componente 7 — Wire Protocol v0.1

**Decisión: Envelope tipado + 11 event types + extensions namespace + JSON Schema 2020-12 + transport auth + task_id correlation.**

**Envelope (10 fields, all justified):**

```json
{
  "spec_version": "0.1.0",
  "id": "evt_01HZX9KXXR8QF3T2",
  "ts": "2026-04-29T18:23:45.123Z",
  "source": {
    "adapter": "cs-aggressor",
    "version": "1.0.0",
    "engagement_id": "eng_acme_q2",
    "adapter_session_id": "ses_01HZX9KMXR1B0K3V"
  },
  "event": {
    "type": "beacon.command_input",
    ...type-specific fields...
  },
  "extensions": {}
}
```

`source.adapter_session_id` is populated after `POST /v1/adapters/connect` returns the session ID and persists for the adapter's lifetime. Required for multi-session scenarios (CS Client closed/reopened mid-engagement → new session_id, daemon disambiguates).

**11 event types in v0.1:**

| # | Type | Emitted by | Purpose |
|---|---|---|---|
| 1 | `adapter.connected` | Adapter | Manifest declaration on handshake |
| 2 | `beacon.initial` | Adapter | First checkin of new beacon |
| 3 | `beacon.command_input` | Adapter | Operator typed command (sync intercept point) |
| 4 | `beacon.command_output` | Adapter | Output returned (correlated via `task_id`) |
| 5 | `host.observation` | Adapter or core | Generic discovered fact about host (OS, user, EDR, integrity, paths) |
| 6 | `credential.observed` | Adapter or core | Cred captured from output (NTLM, Kerberos, plaintext) |
| 7 | `opsec.verdict` | Core (engine) | Severity decision INFO/WARN/DANGER + rule + reasoning |
| 8 | `operator.question` | TUI | Operator chat input |
| 9 | `operator.command` | TUI | Slash command (`:rr <cmd>`) |
| 10 | `assistant.response` | Core (LLM) | Red Robin text response |
| 11 | `engagement.milestone_achieved` | Core or operator | Milestone auto-detected or tagged |

Future deferred subtypes (network.*, beacon.exited, host.av_edr_observed, etc.) live in `docs/protocol/roadmap.md` of the protocol repo.

**`task_id` correlation:** in `beacon.command_input` and `beacon.command_output` payloads to link async pairs (critical for Sliver async model).

**Transport auth:** bearer token shared between adapter and daemon via HTTP `Authorization` header. Token in `~/.redrobin/transport.token` (chmod 600), generated on first daemon startup.

**Strict ingress validation:** malformed events rejected HTTP 400 + logged. NOT processed.

**Versioning policy (semver):**
- PATCH: bugfix
- MINOR: backwards-compatible additions (new types, new optional fields)
- MAJOR: breaking changes (rare, well-communicated)

**Ships as v0.1:**
- `Red-Robin-Protocol/` repo separado
- `docs/v0.1.md`, `docs/versioning-policy.md`, `docs/transport.md`
- `schemas/v0.1/*.schema.json` (envelope + 10 type schemas)
- `tests/fixtures/{valid,invalid}/*.json`
- CI validator

### Componente 8 — Adapter Pattern v0.1

**Decisión: Manifest formal + lifecycle (connect/heartbeat/disconnect) + repo separation + 2 reference adapters + template repo.**

**Manifest:**

```json
{
  "manifest_version": "0.1.0",
  "spec_versions_supported": ">=0.1.0,<0.2.0",
  "name": "cs-aggressor",
  "version": "1.0.0",
  "description": "Cobalt Strike adapter via Aggressor companion",
  "vendor": "CFP-CR",
  "license": "MIT",
  "homepage": "https://github.com/CFP-CR/red-robin-adapter-cs",
  "capabilities": {
    "preflight_block_console": true,
    "preflight_block_tui": true,
    "tui_dispatch": true,
    "output_observe": true,
    "console_annotation": true,
    "emergency_kill_switch": true,
    "gate_scope": "per_operator",
    "command_modification": false
  },
  "events_emitted": ["adapter.connected", "beacon.initial", "beacon.command_input", "beacon.command_output", "host.observation", "credential.observed"],
  "events_consumed": ["opsec.verdict"],
  "requirements": {
    "cobalt_strike": ">=4.12",
    "platform": ["linux", "windows", "darwin"]
  }
}
```

**Capability flag semantics:**

| Flag | Meaning | CS adapter | Sliver-mock | Sliver-real (v0.1.1) |
|---|---|---|---|---|
| `preflight_block_console` | Can block operator commands typed in the C2's native console BEFORE dispatch | `true` (Aggressor `alias` overrides) | `false` (mock) | `false` (Sliver console is operator-side already; same gate-via-TUI applies) |
| `preflight_block_tui` | Can block commands originating from Red Robin TUI BEFORE dispatch | `true` (gate happens in core, TUI never sends on block) | `true` | `true` |
| `tui_dispatch` | Adapter can forward TUI-originated commands to the C2 | `true` (REST API `POST /consoleCommand`) | `false` (synthetic only) | `true` (gRPC `Task()`) |
| `output_observe` | Adapter pushes beacon/session output events to core | `true` (`set BEACON_OUTPUT` hook + REST poll) | `true` (replay) | `true` (gRPC event stream) |
| `console_annotation` | Adapter can inject INFO/WARN annotations into the C2 console | `true` (`blog`, `berror` from `.cna`) | `false` | `false` (no console to annotate from outside) |
| `emergency_kill_switch` | Adapter exposes "flush all queued tasks for this beacon" | `true` (REST `clearCommandQueue`) | `false` | `true` (gRPC `Kill` of running tasks) |
| `gate_scope` | `per_operator` or `team` — does the gate cover only the operator who loaded the adapter, or all team operators? | `per_operator` (Aggressor architectural limit; see T11) | `n/a` | `per_operator` (same limit for Sliver client-side) |
| `command_modification` | Adapter rewrites commands before dispatch (e.g., add OPSEC flags) | `false` (v0.1 only gates, doesn't rewrite) | `false` | `false` |

**Lifecycle:**
1. Adapter starts with `--daemon http://127.0.0.1:7777 --token $REDROBIN_TOKEN`
2. POST `/v1/adapters/connect` with manifest + bearer auth
3. Daemon validates manifest schema + version compat + capabilities consistency
4. Daemon responds with `adapter_session_id`
5. Adapter emits events with `source.adapter_session_id`
6. Heartbeat every 10s to `/v1/adapters/heartbeat`
7. Daemon marks disconnected after 30s no heartbeat
8. Graceful shutdown via POST `/v1/adapters/disconnect`

**Repo organization (v0.1) — MONOREPO:**

Single repo (`cfp-cr/Red-Robin`). Branch layout:

- `main` — protected, production releases (PR target after v0.1 completes)
- `dev-jose` — peer's original draft (preserved; reference for cherry-picks during migration)
- **`dev-juan`** — operator's working branch for v0.1 implementation. PR target for cherry-picks from `dev-jose`. Integrates with peer via separate PRs once aligned.

**Local clone:** `C:\Users\jcspi\Documents\dev-projects\Red-Robin`.

**Architecture: Layered — 4 capas, simple por diseño, standards strictly enforced.**

Estructura del daemon en `redrobin/` (package directo en root del repo, sin `src/` wrapper) sigue las 4 capas clásicas (DDD / Clean Architecture). La **architecture es deliberadamente simple** (no over-engineered), pero los **standards de organización están strictly enforced** vía CI tooling — la regla no es preferencia, es regla.

| Capa canónica | Carpeta(s) | Responsabilidad |
|---|---|---|
| **Domain** | `domain/` | Business rules, entities, dataclasses, rules engine, memory schemas |
| **Application** | `application/` | Use cases, workflows, pipelines (verdict pipeline, chat handler, dispatchers) |
| **Infrastructure** | `infrastructure/` | I/O concretes: LLM, vector store, SQLite, HTTP client, filesystem |
| **Presentation** | `api/` + `tui/` | Entry points: HTTP for adapters, TUI for operator |

**Dependency direction (strictly enforced via `import-linter` in CI):**

```
api/ ──┐
       ├──► application/ ──► domain/  ◄── infrastructure/
tui/ ──┘                              (concretes called by application)
```

**Reglas concretas (validated en CI antes de merge):**

| Regla | Por qué |
|---|---|
| `domain/` NO puede importar `infrastructure/`, `application/`, `api/`, `tui/` | Domain queda testeable + framework-agnostic |
| `application/` NO puede importar `api/` ni `tui/` | Use cases no saben de UI/HTTP |
| `presentation/` (`api/`, `tui/`) NO puede importar `infrastructure/` directamente | Presentation va via `application/` use cases |
| `infrastructure/` PUEDE importar `domain/` (para implementar ABCs cuando existan) | Inversión de dependencia natural |

**Enforcement mechanism — simple no complicado:**
- `importlinter.cfg` en repo root: ~30 líneas declarativas
- 1 step en CI workflow: `lint-imports` corre en cada PR
- Violation rechaza el PR automáticamente
- **No es architecture complicated — es guard rail simple sobre architecture simple**

**Interfaces / abstractions (minimal, no preventivas):**

Architecture es simple — **una sola ABC explícita en v0.1**:

| Interface | Razón |
|---|---|
| `domain/llm.py::LLMProvider` (ABC) | Multi-impl real: Anthropic v0.1, Ollama v0.2 roadmap. Justifica DIP genuino. |
| Todo lo demás | Concrete classes. Duck typing. Tests usan `unittest.mock` o test doubles ad-hoc. |

**Por qué tan pocas interfaces:**
- Una implementación por concepto (excepto LLM) = interface es ceremony pura
- Python duck typing + `unittest.mock` cubren testability sin ABCs preventivas
- YAGNI: agregamos interfaces cuando surge multi-impl real, no antes

**Cuándo agregar una interface en el futuro:**
1. Surge un segundo impl real (no hipotético)
2. Tests del domain se vuelven dolorosos por el coupling
3. Crece el equipo y la disciplina humana ya no escala

**La distinción crítica:**
- **Standards (enforced):** dependency direction, layer boundaries, naming conventions
- **Architecture (simple):** 4 capas, 1 interface, concrete classes, sin DI containers, sin patterns innecesarios

Strict standards + simple architecture = production-grade sin ceremony.

**Monorepo file layout (after v0.1 implementation):**

```
Red-Robin/                          # repo root, branch v0.1
├── README.md                       # extended (multi-component overview)
├── pyproject.toml                  # daemon's Python deps
├── config.example.toml             # daemon config
├── importlinter.cfg                # NEW — enforces dependency direction in CI
├── .github/workflows/lint.yml      # NEW — runs lint-imports on every PR
├── docs/                           # design + spec docs (NEW)
│   ├── architecture-design.md      # this document, copied from vault
│   └── component-glossary.md       # daemon vs domain vs engine terminology
│
├── redrobin/                       # daemon package directo en root (4 canonical layers)
│   ├── __init__.py
│   ├── domain/                     # = DOMAIN LAYER (business rules + entities)
│   │   ├── opsec/                  # rules engine, layer_a, layer_b, verdict, severity
│   │   ├── memory/                 # working, episodic, longterm, context_management
│   │   ├── engagement/             # profile, objectives, kill_chain, milestones, ROE
│   │   ├── events/                 # 11 protocol event types (pure dataclasses)
│   │   ├── rules/                  # YAML rule schema + matcher
│   │   ├── kb/                     # retrieval logic (concrete in v0.1)
│   │   ├── tradecraft_graph/       # técnica → bypass → detection → mitigation graph
│   │   └── llm.py                  # LLMProvider ABC — único interface explícito en v0.1
│   │
│   ├── application/                # = APPLICATION LAYER (use cases / pipelines)
│   │   ├── verdict_pipeline.py     # event → Layer A → Layer B → verdict → emit
│   │   ├── chat_handler.py         # operator question → context → LLM → response
│   │   ├── event_dispatcher.py     # incoming event → routes to handlers
│   │   ├── milestone_detector.py   # auto-detect engagement milestones from events
│   │   └── engagement_updater.py   # phase transitions, objective progress
│   │
│   ├── infrastructure/             # = INFRASTRUCTURE LAYER (I/O, frameworks, third-party SDKs)
│   │   ├── llm/
│   │   │   └── anthropic_client.py # implementa LLMProvider ABC del domain
│   │   ├── vector_store/           # numpy + voyage embeddings (concrete)
│   │   ├── persistence/            # SQLite repos (longterm, episodic) — concrete
│   │   ├── transport/              # HTTP client (outbound) — concrete
│   │   ├── filesystem/             # KB watcher, rules folder watcher — concrete
│   │   └── schema/                 # JSON Schema validator — concrete
│   │
│   ├── api/                        # = PRESENTATION LAYER (HTTP entry)
│   │   ├── server.py               # FastAPI / aiohttp app
│   │   ├── auth.py                 # bearer token middleware
│   │   ├── adapters.py             # /v1/adapters/connect, /heartbeat, /disconnect
│   │   └── events.py               # /events POST handler
│   │
│   ├── tui/                        # = PRESENTATION LAYER (terminal entry)
│   │   ├── app.py                  # prompt_toolkit Application
│   │   ├── panels/                 # working_memory.py, event_stream.py, chat.py
│   │   ├── overlays/               # memory_inspector.py
│   │   └── render.py
│   │
│   ├── config.py
│   ├── cli.py
│   └── __main__.py
│
│   # Layout decision: `redrobin/` directo en root (sin `src/` wrapper).
│   # - Imports: `from redrobin.domain.opsec import LayerB`
│   # - Package namespace preservado (Python convention)
│   # - Hierarchy más simple que `src/redrobin/` (evita triple-repetición Red-Robin/src/redrobin/)
│   # - Trade-off vs PyPA `src/<pkg>/` standard: pierde algunos benefits de import-safety
│   #   en tests (tests pueden importar local code accidentalmente vs installed package).
│   # - Mitigación: tests usan `pip install -e .` y CI corre tests contra installed package.
│   # - Easy migrate a `src/redrobin/` en v1.0 si se distribuye seriamente via PyPI.
│
├── tests/                          # tests organized mirroring redrobin/
│   ├── domain/                     # pure unit tests (no I/O)
│   ├── application/                # use case tests (mocked infrastructure)
│   ├── infrastructure/             # integration tests
│   ├── api/                        # HTTP route tests
│   └── tui/                        # UI tests
│
├── protocol/                       # NEW — open standard spec (will become own repo at v1.0)
│   ├── README.md
│   ├── docs/
│   │   ├── v0.1.md                 # narrative spec
│   │   ├── transport.md            # auth, retry, dedup
│   │   ├── versioning-policy.md    # semver rules
│   │   └── adapter-contract.md     # what adapter authors implement
│   ├── schemas/v0.1/               # JSON Schema 2020-12 files
│   │   ├── envelope.schema.json
│   │   ├── beacon.command_input.schema.json
│   │   ├── beacon.command_output.schema.json
│   │   └── ... 11 type schemas total
│   └── tests/
│       ├── fixtures/valid/         # example valid events
│       ├── fixtures/invalid/       # malformed examples
│       └── validate.py             # CI validator
│
├── adapters/                       # NEW — external adapter implementations
│   ├── cs/                         # CS-Aggressor (full F)
│   │   ├── README.md
│   │   ├── manifest.json
│   │   ├── redrobin.cna            # Aggressor script (Sleep)
│   │   ├── connector/              # Python HTTP connector
│   │   │   ├── pyproject.toml
│   │   │   └── src/cs_connector/
│   │   └── tests/
│   │
│   ├── sliver-mock/                # v0.1 demo mock
│   │   ├── README.md               # explicit "mock for v0.1, real in v0.1.1"
│   │   ├── manifest.json
│   │   ├── pyproject.toml
│   │   ├── src/sliver_mock/
│   │   ├── recorded_engagement.json
│   │   └── examples/
│   │
│   └── sliver/                     # PLACEHOLDER for v0.1.1
│       └── README.md               # "Sliver real adapter, target 2026-06-22"
│
├── rules/                          # NEW — bootstrap OPSEC rules pack
│   ├── README.md
│   ├── windows/
│   │   ├── credential-access.yaml
│   │   ├── recon.yaml
│   │   ├── persistence.yaml
│   │   ├── lateral-movement.yaml
│   │   └── anti-forensics.yaml
│   └── linux/
│       └── ...
│
└── kb-bootstrap/                   # NEW — starter KB content (public sources only)
    ├── README.md
    ├── mitre/                      # MITRE ATT&CK technique notes
    ├── opsec/                      # public CS/Sliver OPSEC docs
    └── techniques/                 # curated public technique writeups
```

**Anti-patterns explicitly avoided:**
- ❌ NO `utils/` at top level (Dunghill anti-pattern). Helpers live inside the module that uses them.
- ❌ NO ambiguous `services/` overlapping with `application/`.
- ❌ NO `agents/` separate folder (LLM is a tool used by `domain/opsec/layer_b.py` and lives in `infrastructure/llm/`).
- ❌ NO `models/` mixed with `domain/` (event types and entities are inside `domain/events/`, `domain/opsec/`, etc.).

**Cherry-picks from peer's draft** (preserved with relocation):

| Peer's draft | Goes to |
|---|---|
| Slash commands UX (`/alt`, `/why`, `/save`, etc.) | `tui/` + `application/use_cases/` |
| Token budgeting | `application/chat_handler.py` |
| KB watch-folder pattern | `infrastructure/filesystem/kb_watcher.py` |
| System prompt voice ("colleague not manual") | `domain/opsec/prompts/system.txt` |
| Privacy/data location table | `README.md` (preserved) |
| Resume/replay support | `tui/cli.py` flags |
| TOML config | `infrastructure/config/` |
| Async patterns | `application/event_dispatcher.py` |
| Color theme + render | `tui/render.py` |

**Discarded from peer's draft:**

| Peer's draft | Discarded because |
|---|---|
| `EventKind` (7 generic kinds) | Replaced by 11 typed events in `domain/events/` |
| `LogAdapter` ABC (in-process) | Replaced by external HTTP adapter pattern |
| `Triage` heuristic | Subsumed by Layer A YAML rules engine in `domain/opsec/` |
| Single-component daemon assumption | Replaced by 4-layer + multi-component architecture |

**Why monorepo for v0.1:**
- Single clone for operator + peer working in parallel (no submodule juggling)
- Atomic commits across components (e.g., add new event type → update protocol schema + daemon ingest + adapter emitter in one commit)
- Spec doc lives next to code (audit trail tightly coupled)
- Less DevOps overhead (one CI pipeline, one issue tracker, one releases page)

**Future split (v0.2 or v1.0 — when justified by community traction):**
- `protocol/` extracted to `cfp-cr/Red-Robin-Protocol` (independent versioning)
- `adapters/cs/`, `adapters/sliver/` extracted to their own repos (`cfp-cr/Red-Robin-Adapter-{CS,Sliver}`)
- Daemon stays in `cfp-cr/Red-Robin`

**Roadmap evolution:**
- v0.1 — monorepo, branch `v0.1` → merge to `main` post-DEF CON
- v0.1.1 (2026-06-22) — adapter `sliver/` populated with real implementation
- v0.2 — Tradecraft Graph (L4), local LLM mode, more adapters (Havoc, Mythic), maybe split into multi-repo if community demands

**Reference adapters in v0.1:**

- **CS-Aggressor (full F):** Aggressor script + Python connector + manifest. Pre-flight intercept con block, console annotation, post-exec observation, host/credential extraction. Repo: `Red-Robin-Adapter-CS`.

- **Sliver-Mock:** Python script que postea events pregrabados al daemon como si fuera un Sliver adapter real. Sirve para demostrar agnostic claim live en talk + smoke-test del protocolo end-to-end. Repo: `Red-Robin-Adapter-Sliver-Mock` con README explícito ("mock for v0.1 demo purposes; full implementation v0.1.1").

- **Sliver-Real (v0.1.1, target 2026-06-22):** gRPC subscriber al Sliver server, event translator, host/credential extractor. **Public commitment con fecha concreta.** Repo: `Red-Robin-Adapter-Sliver` (creado en v0.1 con first issue tracking implementation).

- **Adapter Template repo:** ❌ deferred a v0.2. Contrato del manifest documentado en `Red-Robin-Protocol/docs/adapter-contract.md`. Comunidad puede empezar copiando referencias.

**Por qué Sliver-Mock + Real-comitted vs full real adapter en v0.1:**
- Demo impact idéntico (TUI no diferencia entre mock y real adapter)
- README transparente + commitment público con fecha = honest, no vaporware
- Marketing bonus: Sliver real release ~3 semanas post-talk = segundo news cycle
- Reduces v0.1 ship pressure sin diluir agnostic claim

---

## 5. Engagement Lifecycle

Cómo el operador opera Red Robin a través de múltiples engagements, cómo se aísla data entre ellos, y cómo lessons aprendidas se acumulan over time.

### 5.1 Storage layout — Hybrid (Option 3)

Decisión locked: **shared cross-engagement + isolated per-engagement.**

```
~/.redrobin/
├── shared/                          # cross-engagement (curated knowledge)
│   ├── longterm.db                  # facts persistentes, validated rules, vault-indexed lessons
│   ├── tradecraft.db                # MITRE technique → bypass → detection graph
│   └── kb-index/                    # numpy embeddings of KB
│
├── engagements/                     # engagement-specific (isolated)
│   ├── acme-q2-2026/
│   │   ├── engagement.yaml          # profile (objectives, scope, ROE)
│   │   ├── working_memory.json      # hosts, accounts, defenses, kill chain
│   │   ├── episodic.db              # raw lessons of THIS engagement
│   │   ├── events.jsonl             # event log audit trail
│   │   └── summary.txt              # rolling summary
│   ├── globex-q1-2026/
│   │   └── ... (same shape)
│   └── ...
│
├── kb-source/                       # operator's vault notes (markdown)
├── rules/                           # YAML rules (operator-curated)
│   ├── shared/                      # baseline rules pack
│   └── personal/                    # operator overrides
├── config.toml
├── transport.token                  # bearer token for adapter auth (chmod 600)
└── redrobin.log                     # daemon log (chmod 600)
```

### 5.2 Memory layer to storage mapping

| Memory Layer | Naturaleza | Storage location |
|---|---|---|
| L1 Long-term | Cross-engagement (curated) | `shared/longterm.db` |
| L2 Working Memory | Engagement-specific | `engagements/<slug>/working_memory.json` |
| L3 Episodic | Engagement-specific (until promoted) | `engagements/<slug>/episodic.db` |
| L4 Tradecraft Graph | Cross-engagement (curated) | `shared/tradecraft.db` |
| L5 Context Management | Ephemeral + per-engagement summary | In-memory + `engagements/<slug>/summary.txt` |

### 5.3 CLI commands (operator-facing)

**Setup / lifecycle:**
```
# First-run init (creates ~/.redrobin/ tree, fetches rules pack, indexes default KB)
redrobin init

# Preflight check (Anthropic + Voyage reachable, KB indexed, rules loaded, daemon binary OK)
redrobin doctor

# Start / stop daemon
redrobin daemon start
redrobin daemon stop

# Open TUI (full-screen client to daemon)
redrobin tui
```

**Engagement management:**
```
# New engagement (creates engagements/<slug>/ with engagement.yaml template)
redrobin engagement new <slug>

# List engagements (active, archived, current)
redrobin engagement list

# Switch context (loads working_memory + episodic of that engagement; shared longterm + tradecraft remain available)
redrobin engagement use <slug>

# Show current
redrobin engagement current

# Close (preserve state, can resume)
redrobin engagement close [<slug>]

# Archive (export tarball, remove active)
redrobin engagement archive <slug>
  → produces engagements/archived/<slug>-<timestamp>.tar.gz

# Import (cross-laptop transfer; --merge-lessons flag is v0.2 — see §7 roadmap)
redrobin engagement import <archive.tar.gz>

# Delete (require confirmation)
redrobin engagement delete <slug> --confirm
```

**Knowledge base & memory:**
```
# Add a directory to KB index (--watch enables file-system watching for re-index)
redrobin kb add <path> [--watch]

# Edit working memory or episodic manually (opens $EDITOR with validation on save)
redrobin memory edit <file>
```

**Demo / dry-run:**
```
# Replay an archived engagement against the daemon (for stage demos or dry-run)
redrobin replay <archive.tar.gz> [--speed Nx]
```

### 5.4 Lesson promotion workflow

Operator-curated migration de episodic (engagement-specific) → longterm (cross-engagement):

```
# Manual promotion via slash command (during engagement)
:rr promote "<lesson description>"
  → moves entry from episodic.db (current engagement) to shared/longterm.db
  → tagged with source_engagement_slug for audit

# At engagement close, prompted summary
$ redrobin engagement close acme-q2-2026
[?] 12 lessons in episodic. Review for promotion to long-term?
[1/12] "nanodump invisible to CrowdStrike on Win 2022 DC01"
        promote? [y/N]: y
[+] Promoted to longterm.
[2/12] "..."
        ...
```

**Rationale:** mantiene longterm de alta calidad (operator-validated) sin auto-promotion noise.

### 5.5 Cross-laptop transfer (export/import)

Operator switches machines mid-engagement:

```
# On laptop A
$ redrobin engagement archive acme-q2-2026
[+] Created engagements/archived/acme-q2-2026-20260615-143022.tar.gz

# Transfer file (scp, USB, whatever)

# On laptop B
$ redrobin engagement import acme-q2-2026-20260615-143022.tar.gz
[+] Restored to engagements/acme-q2-2026/
[+] Engagement ready: redrobin engagement use acme-q2-2026
```

Shared longterm/tradecraft NOT included in tarball (operator may have different local longterm DB).

### 5.6 Multi-engagement isolation guarantees

| Guarantee | Mechanism |
|---|---|
| Working memory isolated | Filesystem-level — `engagements/<slug>/working_memory.json` per engagement |
| Episodic isolated | Filesystem-level — `engagements/<slug>/episodic.db` per engagement |
| Cannot accidentally query cross-engagement working memory | Daemon loads only current engagement's working memory; no SQL WHERE clause leakage possible |
| Cross-engagement learning via episodic → longterm | Manual `promote` command, audit trail preserved |
| Engagement deletion is clean | `rm -rf engagements/<slug>/` removes all engagement-specific data without touching shared |

### 5.7 File permissions

| Path | chmod | Reason |
|---|---|---|
| `~/.redrobin/` (root) | 700 | Owner-only access |
| `~/.redrobin/transport.token` | 600 | Bearer token for adapters (sensitive) |
| `~/.redrobin/redrobin.log` | 600 | Logs may contain command outputs |
| `~/.redrobin/engagements/<slug>/events.jsonl` | 600 | Engagement audit trail |
| `~/.redrobin/shared/longterm.db` | 600 | Aggregated lessons |
| `~/.redrobin/engagements/<slug>/episodic.db` | 600 | Engagement lessons |
| `~/.redrobin/config.toml` | 600 | May contain operator-sensitive paths/identifiers (engagement slugs, endpoints, redaction patterns). API keys are programmatically refused from this file per Componente 4 — env var only. |

Daemon enforces these on startup; warns if existing files don't match.

### 5.8 Disk encryption recommendation

Documented in README: **operator should use full-disk encryption** (LUKS, FileVault, BitLocker). Red Robin does NOT implement application-level encryption at rest in v0.1 — that responsibility is delegated to the OS. v0.2 may add SQLCipher option for engagement DBs as defense-in-depth.

### 5.9 End-to-end operator workflow

The "day in the life" with Red Robin v0.1 — concrete commands, screen states, and decision points. This section is the operational runbook; everything before it is architecture.

#### 5.9.1 One-time setup (per laptop, ~10 min)

```bash
# Install Red Robin via uv (preferred) or pipx
uv tool install redrobin

# Set Anthropic API key (env var only — never config file, see T2)
export ANTHROPIC_API_KEY="sk-ant-..."
export VOYAGE_API_KEY="pa-..."

# First-run init creates ~/.redrobin/ tree, fetches rule pack, indexes default KB
redrobin init

# Optional: index operator's personal vault as KB source
redrobin kb add ~/Documents/spiedra-vault --watch

# Verify
redrobin doctor
# [+] Anthropic reachable (~340ms)
# [+] Voyage reachable (~210ms)
# [+] KB indexed (1247 chunks)
# [+] Rules loaded (15 rules from rules/shared/, 0 from rules/personal/)
# [+] Daemon binary OK (v0.1.0)
```

**One-time CS Client setup**: copy `redrobin.cna` from the adapter package to a known path; load via Cobalt Strike → Scripts → Load → select `redrobin.cna`. Save the script as auto-loaded so it persists across CS Client restarts.

#### 5.9.2 New engagement creation

```bash
# Create scaffolding for a new engagement
redrobin engagement new acme-q2-2026
# [+] Created engagements/acme-q2-2026/
# [+] Edit engagement.yaml to set scope, ROE, objectives
# [+] Run 'redrobin engagement use acme-q2-2026' when ready

# Operator edits engagement.yaml (scope, ROE, objectives)
$EDITOR ~/.redrobin/engagements/acme-q2-2026/engagement.yaml

# Activate the engagement
redrobin engagement use acme-q2-2026
# [+] Working memory loaded (empty)
# [+] Episodic DB ready (0 lessons)
# [+] Engagement ID set in daemon state
```

`engagement.yaml` minimal example:
```yaml
id: acme-q2-2026
client: Acme Corp
scope:
  in_scope:
    - "*.acme.local"
    - "10.0.0.0/16"
  out_of_scope:
    - "10.0.99.0/24"   # legal/HR subnet
    - "*.acme-legal.com"
objectives:
  - id: obj_1
    goal: "Compromise primary domain controller"
    priority: high
  - id: obj_2
    goal: "Demonstrate access to client-data fileshare"
    priority: high
  - id: obj_3
    goal: "Establish persistent foothold for retest"
    priority: medium
rules_of_engagement:
  - "No destructive actions on production"
  - "No DoS / availability-impacting actions"
  - "Working hours: 09:00-18:00 CR"
  - "Stop and notify if BCRA-regulated systems detected"
```

#### 5.9.3 Daily startup (~1 min)

```bash
# Start daemon (background)
redrobin daemon start
# [+] Daemon listening on localhost:7777
# [+] Engagement: acme-q2-2026 (resumed from yesterday)
# [+] Working memory: 3 hosts, 2 accounts, 1 defense observed
# [+] Phase: credential_access (auto-detected)

# Open TUI in another terminal (optional but recommended)
redrobin tui
# Full-screen TUI: Working Memory pane (left) | Event Stream (center) | Chat (right)

# Open CS Client. redrobin.cna auto-loads.
# Adapter handshake happens silently. TUI banner updates:
#   [adapter cobaltstrike v1.0.0 connected — gate_scope: per_operator]
```

If the operator skipped the TUI, only inline `.cna` console annotations are visible. The TUI is recommended but not required — the gate works without it.

#### 5.9.4 During engagement — Path 1 (CS console gate)

This is the most common interaction. Operator types in the CS console as normal:

```
beacon> mimikatz logonpasswords
[!] [Red Robin] DANGER: lsass touch + plaintext-creds extraction in
    cmdline matches T1003.001 + Sysmon EID 10. Working memory shows
    DC01 has CrowdStrike Falcon (observed 14:32). Recommend: nanodump
    BOF (no lsass open) or Rubeus /tgtdeleg if Kerberos-only goal.
    Override: ':rr override "<reason>"' to proceed.

beacon>
```

The command was **never sent to the team server**. The alias intercepted at parser stage, daemon returned DANGER, alias body printed via `berror` and returned without calling `&bmimikatz`.

Operator decisions:
- Accept the alternative → type `:rr alts` → TUI shows ranked alternatives, operator picks one
- Override → type `:rr override "client requested live demo of credentials"` → original command runs, override logged in episodic
- Pivot → type a different command — the gate re-evaluates

WARN-level example:
```
beacon> shell nxc ldap 192.168.1.0/24 -u svc_backup -p Welcome123!
[*] [Red Robin] WARN: NetExec LDAP scan high-signal; plaintext creds
    visible in cmdline (cleared on next reboot). Proceed if intended.
[*] Tasked beacon to run: nxc ldap 192.168.1.0/24 -u svc_backup -p Welcome123!
```

WARN does not block; the warning is logged and command runs.

INFO-level example:
```
beacon> whoami /priv
[i] [Red Robin] INFO: privilege enumeration. Common pre-privesc step.
[*] Tasked beacon to run: whoami /priv
```

Pass-through (no annotation):
```
beacon> pwd
[*] Tasked beacon to run: pwd
C:\Windows\System32

beacon>
```

`pwd` has no alias — flows native. Path 3 still observes the output for memory.

#### 5.9.5 During engagement — Path 2 (TUI chat)

When the operator wants to think, plan, or ask without committing to a command yet:

```
[Chat pane]
> tengo SYSTEM en DC01. CrowdStrike Falcon activo. quiero credentials sin tocar lsass.
  obj_1 está al 60% — DC ya está comprometido pero falta dump. opciones?

[Red Robin]
4 alternatives ranked by stealth/effort, contextualized to this engagement:

1. **DCSync via Rubeus /tgtdeleg** — no lsass touch, RPC only.
   Risk: requires DA replication rights (you have them). Detection:
   T1003.006, EID 4662 (replication audit if enabled).
2. **Nanodump BOF (in-memory)** — duplicates lsass handle silently.
   Risk: still touches lsass but BOF avoids known signatures.
3. **Shadow Copy via vssadmin** — create snapshot, copy NTDS.dit,
   extract hashes offline. NOT recommended: vssadmin is high-signal
   ransomware precursor (you'd trigger SOC playbook).
4. **Skeleton key** (Mimikatz misc::skeleton) — persistent backdoor.
   Out of v0.1 ROE per engagement.yaml ("no destructive").

obj_3 (persistence) still at 0% — consider option 1 (DCSync gives
you Krbtgt for golden tickets later). Time budget: ~6 days remain.

> ok dame el comando exacto para opción 1

[Red Robin]
   execute-assembly Rubeus.exe dcsync /domain:acme.local
   /user:acme.local\krbtgt /service:cifs/dc01.acme.local

This will trigger Tier 1B alias (execute-assembly + Rubeus pattern) →
WARN, not BLOCK, since you're SYSTEM and obj_1 already validates this
attack path. Proceed when ready.
```

The TUI chat is **conversational planning** — no command leaves the laptop until the operator explicitly executes it (in CS console or via `:rr exec` from TUI for Path 2 dispatch).

#### 5.9.6 During engagement — Path 3 (passive observation)

Working memory updates happen automatically. Operator sees the TUI Working Memory pane refresh as commands flow:

```
[Working Memory pane — auto-updates]

ENGAGEMENT  acme-q2-2026                      Day 4 / ~10
PHASE       credential_access                 ████░░░░ 60%
KILL CHAIN  recon✓ initial✓ priv-esc✓ creds⚙ lateral— exfil—

OBJECTIVES
  obj_1  Compromise primary DC          ████░░ 60%  in_progress
  obj_2  Access client-data fileshare   ░░░░░░  0%  not_started
  obj_3  Persistence foothold           ░░░░░░  0%  not_started

HOSTS (3)
  WS-FINANCE-04  Win10 22H2  Defender (active)  user-token
  DC01           WS2022      CrowdStrike        SYSTEM
  FILESERVER-02  WS2019      n/a                 not yet pwn'd

ACCOUNTS (2)
  acme\jdoe       hash       (workstation)
  acme\svc_sql    plaintext  (DC01 — service account)

DEFENSES OBSERVED
  Defender on workstations
  CrowdStrike Falcon on DC01

TOOLS USED
  nanodump.bof  (DC01, 14:48 — no detection)
  Rubeus.exe    (DC01, 13:22 — kerberoast, no detection)

LESSONS (auto, episodic)
  → nanodump invisible to Falcon on Win 2022 (today, 14:48)
  → shell nxc with cleartext creds visible to Sysmon (yesterday)
```

#### 5.9.7 Common operator slash commands

| Command | What it does |
|---|---|
| `:rr alts` | Show ranked alternatives for last command |
| `:rr explain` | Why was this DANGER/WARN? Show full verdict + episodic context |
| `:rr override "<reason>"` | Override a DANGER block; reason logged in episodic |
| `:rr exec <cmd>` | Dispatch a command from TUI via Path 2 (REST API) |
| `:rr promote "<lesson>"` | Promote an episodic lesson to longterm KB |
| `:rr milestone "<text>"` | Manually mark engagement progress |
| `:rr phase <name>` | Override auto-detected phase |
| `:rr objective <id> <pct>` | Update objective progress manually |
| `:rr inspector` (Ctrl+I) | Open memory inspector overlay (full state) |
| `:rr help` | List all commands |

#### 5.9.8 End of working session (operator pauses)

```bash
# Operator wants to stop for the day, resume tomorrow

# Stop the daemon — engagement state persists on disk
redrobin daemon stop
# [+] Daemon stopped
# [+] Working memory snapshotted to engagements/acme-q2-2026/working_memory.json
# [+] Events.jsonl flushed (827 events today)

# Optionally close CS Client (the .cna unloads automatically)
```

When the operator returns, daemon restart replays events from last snapshot — full state restored.

#### 5.9.9 End of engagement

```bash
# Engagement is complete. Promote permanent lessons.
redrobin engagement close acme-q2-2026
# 12 candidate lessons from this engagement:
#   [1/12] "nanodump invisible to Falcon on Win 2022"  Promote? (y/n) y
#   [2/12] "DCSync /tgtdeleg works without DA on this domain (delegation chain via svc_sql)"  Promote? (y/n) y
#   ...
# [+] 8 lessons promoted to longterm
# [+] 4 declined (engagement-specific noise)
# [+] Engagement closed; data preserved in engagements/acme-q2-2026/

# Optional: archive for cross-laptop transfer or peer share
redrobin engagement archive acme-q2-2026
# [+] Created engagements/archived/acme-q2-2026-20260615-143022.tar.gz
# [+] (does NOT include shared longterm DB)

# After paper write-up, operator may delete (full-disk encryption + chmod 600 already protect, but explicit removal possible)
redrobin engagement delete acme-q2-2026 --confirm
```

#### 5.9.10 Cross-engagement learning

A new engagement starts with **shared longterm intact, episodic empty**:

```bash
redrobin engagement new globex-q3-2026
redrobin engagement use globex-q3-2026

# When operator chats:
> El target tiene Falcon en los DCs. Recomendaciones para creds?

[Red Robin]
Based on previous engagements (longterm KB):
  - Acme Q2 2026: nanodump BOF was invisible to Falcon on Win 2022 DC.
    Confidence: high (1 engagement, validated outcome).
  - <other lesson if relevant>

Recent industry intel (KB index): ...

This is the augmented operator over time. Lessons compound.
```

#### 5.9.11 Team workflow (T11 SOP)

When multiple operators share a teamserver:

1. **Pre-engagement SOP**: Lead distributes `redrobin.cna` to all operators on the engagement. All operators load it. (Script can be loaded auto via Cobalt Strike Profile.)
2. **Per-operator daemon**: Each operator runs their own daemon and TUI. Local state is private.
3. **Cross-operator output observation**: When operator A's beacon emits output, operator B's `BEACON_OUTPUT` hook (if they share the beacon) also fires — both daemons update memory independently.
4. **Engagement archive sharing**: Lead can `engagement archive` and distribute the tarball. Each operator imports their copy. Lessons remain individual until manually merged via `engagement import --merge-lessons` (v0.2 feature).
5. **Unmanaged operators warning**: `redrobin.cna` uses `on beacon_input` (a multiplayer-broadcast observation event — `$2 = operator nick`) to detect commands from operators who haven't completed adapter handshake. The TUI banner reads: `[!] 1 operator gated, N unmanaged operators on teamserver: <nick>, <nick>` — same canonical wording referenced in §6 T11. Adapter self-registers its local nick (via `aggressor/DataUtils.getNick`) at handshake to avoid self-flagging.

#### 5.9.12 Failure modes operators encounter

| Symptom | Likely cause | Recovery |
|---|---|---|
| TUI banner shows "LLM offline" | Anthropic API unreachable (network, rate limit, key) | Layer A rules still apply. Re-run `redrobin doctor` to diagnose. |
| TUI shows "adapter disconnected" | CS Client closed, .cna unloaded, or token rotated | Re-open CS Client; reload .cna; verify `transport.token` matches. |
| Daemon crash | Python exception | Restart with `redrobin daemon start`. Events.jsonl replay restores state since last snapshot (~30s loss max). |
| Verdict says BLOCK on something operator KNOWS is fine | Layer A rule too aggressive | Edit `~/.redrobin/rules/personal/<rule>.yaml` to override severity. Hot-reload picks up immediately, no restart. |
| Wrong working memory state (e.g., wrong host attribution) | Adapter pushed bad event | `redrobin memory edit working_memory.json` (operator-curated). |
| Lost engagement folder accidentally | `rm -rf engagements/...` | Restore from `engagement archive` tarball if exists. Otherwise, lost. (Disk encryption + backups out of Red Robin scope.) |

#### 5.9.13 Rules and KB curation workflow

**Adding a personal rule mid-engagement:**

```bash
# Operator notices a new noisy pattern. Drops a YAML in personal/.
$EDITOR ~/.redrobin/rules/personal/svc_account_enum.yaml
```

```yaml
# rules/personal/svc_account_enum.yaml
id: svc_account_enum
match:
  command: "shell"
  arg_pattern: 'net user .*svc_'
severity: WARN
reason: "Service account enumeration generates 4625/4624 events; pair with stealth where possible"
suggestions:
  - "Use BloodHound's collected data instead of live enumeration"
  - "If live enum needed, target single account: 'net user svc_specific /domain'"
```

Watchdog detects the new file → daemon hot-reloads → next match emits the WARN. No restart needed.

**Adding KB content:**

```bash
# Operator just wrote a new technique note in their vault
# Drop the markdown into kb-source/ (or use --watch on init)
cp ~/Documents/spiedra-vault/Techniques/nanodump-falcon.md ~/.redrobin/kb-source/
# watchdog re-indexes (~1-3s)
# Layer B verdict can now retrieve this as RAG context for future queries
```

#### 5.9.14 Demo / dry-run workflow (for talks)

Special-purpose mock mode for stage demos without a live target:

```bash
# Run sliver-mock adapter with a pre-recorded scenario
redrobin-sliver-mock --scenario linux_demo
# [+] Adapter connected. Replaying acme_corp.json (5min realistic timing)

# Or for Cobalt Strike replay:
redrobin replay engagements/archived/acme-q2-2026-demo.tar.gz --speed 2x
# [+] Daemon now consumes recorded events at 2x speed. Same TUI experience.
```

This is what powers the demo storyboard (Section 4 Componente 6).

---

**This workflow section is the operational contract**: what Red Robin promises the operator their day looks like. The architecture above (Sections 1-4) is the means; this section is the end. Every decision in the spec must serve one of the steps here.

---

## 6. Threat Model

Threats considerados explícitamente para v0.1, sus mitigations, y los que están out-of-scope.

### 6.1 In-scope threats

**T1 — Operator's laptop compromise (post-incident)**

| Aspect | Detail |
|---|---|
| Threat | Malware / physical theft → working memory + episodic + LLM context history exfiltrated |
| Impact | Engagement state visible to attacker (hosts, accounts, defenses observed) |
| Likelihood | Low (operator typically OPSEC-aware) but high impact if occurs |
| Mitigation v0.1 | Filesystem permissions (chmod 600/700). Disk encryption recommendation. Engagement archive deletion option. |
| Mitigation v0.2 | SQLCipher engagement DB encryption. Burn-after-engagement opt-in. |

**T2 — Anthropic API key exfiltration**

| Aspect | Detail |
|---|---|
| Threat | API key leaked via env dump, logs, repo accident → attacker uses key, costs operator $$ |
| Impact | Financial; potential abuse to send embarrassing prompts |
| Likelihood | Medium |
| Mitigation v0.1 | API key from env var only (never config file). Documented rotation process. `.gitignore` patterns for repos. |
| Mitigation v0.2 | Optional credential vault integration (1Password CLI, etc.) |

**T3 — Adapter spoofing**

| Aspect | Detail |
|---|---|
| Threat | Local malware POSTs forged events to `localhost:7777` → fake working memory updates, fake verdicts |
| Impact | Operator misled by injected state |
| Likelihood | Low (requires local foothold) but realistic in IR scenarios |
| Mitigation v0.1 | Bearer token in `Authorization` header. Token in `~/.redrobin/transport.token` (chmod 600). Daemon rejects requests without valid token. Adapter manifest validation at handshake. |
| Mitigation v0.2 | Per-adapter scoped tokens. Mutual TLS optional. |

**T4 — Rules YAML poisoning**

| Aspect | Detail |
|---|---|
| Threat | Local malware modifies `~/.redrobin/rules/*.yaml` → DANGER rules become INFO, operator burned |
| Impact | OPSEC engine gives wrong verdicts |
| Likelihood | Low-medium |
| Mitigation v0.1 | File permissions (rules in chmod 700 directory, files chmod 600). Hash-checking on rules at startup (warn if changed since last validated). |
| Mitigation v0.2 | Optional GPG-signed community rules pack. Rule diff display on detection. |

**T5 — KB poisoning**

| Aspect | Detail |
|---|---|
| Threat | Operator inadvertently adds malicious markdown to `kb-source/` (e.g., from compromised git pull) → LLM retrieves poisoned chunks → bad advice |
| Impact | LLM gives operator-misleading suggestions |
| Likelihood | Low (operator curates KB) |
| Mitigation v0.1 | KB source is operator-curated only (no auto-fetch from internet in v0.1). Citations always shown so operator can verify source. |
| Mitigation v0.2 | KB source provenance tracking. Operator can mark sources as "trusted." |

**T6 — Layer B (LLM) hallucination causing bad verdict**

| Aspect | Detail |
|---|---|
| Threat | LLM hallucinates "this is safe" when actually risky → operator proceeds, gets burned |
| Impact | Engagement compromised; operator reputation damaged |
| Likelihood | Medium (LLMs hallucinate) |
| Mitigation v0.1 | Layer A YAML rules run first (deterministic floor for known-bad). LLM only escalates verdicts for novel cases. Citations required in responses. Operator always retains decision (Red Robin never executes). |
| Mitigation v0.2 | Confidence scoring on verdicts. Cross-checking with Tradecraft Graph. |

**T7 — Daemon crash mid-engagement**

| Aspect | Detail |
|---|---|
| Threat | Python exception kills daemon → working memory in-flight changes lost |
| Impact | Some recent events not in working memory; operator may operate with stale view |
| Likelihood | Low-medium |
| Mitigation v0.1 | All event ingestion writes to `events.jsonl` BEFORE updating working memory (write-ahead log). On restart, daemon replays events.jsonl since last working_memory.json snapshot. Snapshot every N events or T seconds. |
| Mitigation v0.2 | Watchdog process restart, health-check endpoint. |

**T8 — Network unavailable during engagement**

| Aspect | Detail |
|---|---|
| Threat | Anthropic API unreachable → Layer B (LLM judge) fails → no verdicts, no chat |
| Impact | Operator loses LLM-driven features mid-engagement |
| Likelihood | High (DEF CON network, restricted client networks, etc.) |
| Mitigation v0.1 | **Graceful degradation:** Layer A (rules) keeps working. TUI displays "LLM offline, rules-only mode" banner. Operator continues with deterministic verdicts. |
| Mitigation v0.2 | Local LLM mode (Ollama). |

**T9 — ROE violation by operator**

| Aspect | Detail |
|---|---|
| Threat | Operator targets out-of-scope host (defined in engagement.yaml's `out_of_scope`) → contractual breach |
| Impact | Legal / contractual issue with client |
| Likelihood | Low (intentional or negligent) |
| Mitigation v0.1 | OPSEC engine flags commands targeting out-of-scope hosts as DANGER with "ROE violation" reason. Hard override requires explicit reason logged. |
| Mitigation v0.2 | Optional hard-block (no override) for ROE violations. |

**T10 — Sensitive data sent to Anthropic API**

| Aspect | Detail |
|---|---|
| Threat | Working memory + KB chunks + chat context sent to **Anthropic** (LLM) and **Voyage/MongoDB** (embeddings). May contain client-sensitive data. |
| Impact | Privacy / contractual concern with client. Anthropic + Voyage data retention policies apply. |
| Likelihood | High (this is the design — operator accepts this trade-off) |
| Mitigation v0.1 | Documented in README's privacy table (both Anthropic and Voyage flows). Operator's responsibility to verify engagement contracts permit cloud LLM + embeddings. Operator-configured redaction patterns per Section 4 Componente 4 reduce surface. |
| Mitigation v0.2 | **Local mode bundle** — Ollama LLM + sentence-transformers embeddings, single config switch. Zero data leaves laptop in local mode. Quality trade-off ~10-15% retrieval benchmark drop, invisible at single-operator KB scale. |

**T11 — Single-operator gate scope (Aggressor `alias` architectural limit)**

| Aspect | Detail |
|---|---|
| Threat | Path 1 gate (alias-based) lives in the Aggressor runtime of the operator who loaded `redrobin.cna`. Another operator on the same teamserver who has NOT loaded the `.cna` is not gated — their commands reach the teamserver unfiltered. |
| Impact | A second operator (peer, mentor, contractor) can run dangerous commands during the same engagement without Red Robin's awareness. Working memory observes their output (via `BEACON_OUTPUT` if they share the beacon), but the gate is bypassed for their input. |
| Likelihood | Medium in team engagements; Low in solo engagements. |
| Mitigation v0.1 | (a) **Team SOP**: documented in README — "every operator on a Red Robin engagement loads the `.cna`." (b) Adapter manifest declares `gate_scope: "per_operator"` so the TUI banner reads "1 operator gated, 2 unmanaged operators on teamserver" if cross-operator observation detects unmanaged input via `BEACON_OUTPUT` parsing. |
| Mitigation v0.2+ | (a) Team-level enforcement via Mythic adapter (where `opsec_pre` is server-side and gates all operators). (b) Optional CS teamserver-side hook script that broadcasts unmanaged operator warnings via Aggressor `on event_public`. |
| Honesty note | This is an architectural limit of Cobalt Strike's client-side scripting model, not a Red Robin design flaw. Documented openly in the paper. |

### 6.2 Out-of-scope threats (explicitly NOT addressed)

- **Compromised operator** (intentional misuse) — Red Robin assumes operator is the legitimate authority for the engagement
- **Compromised Anthropic / Voyage** (supply-chain) — no in-product mitigation; operator monitors security advisories
- **CS Client compromise** — Red Robin's adapter relies on CS Client being legit. If CS Client is malicious, all bets off.
- **Physical key extraction** — outside threat model for laptop-based deployment
- **Operator typos / fat-finger** — Red Robin's verdicts help but cannot prevent all human error

### 6.3 Trust boundaries

```
[ Operator ] ←→ trusted (human in the loop)
[ Operator's laptop OS ] ←→ trusted (foundation)
[ Red Robin Core ] ←→ trusted (validates all input via JSON Schema; C2-agnostic — never speaks REST/Aggressor)
[ Adapters ] ←→ trusted IF authenticated via bearer token; sole boundary that holds C2 credentials (JWT)
[ Anthropic API ] ←→ trusted-but-third-party (operator accepts data-share)
[ Voyage API ] ←→ trusted-but-third-party (only embeddings)
[ Cobalt Strike teamserver ] ←→ untrusted-but-authenticated (only CS Adapter connects, bearer JWT scoped to operator's CS auth; Core has no credentials)
[ Beacons / targets ] ←→ untrusted (never directly contacted by daemon)
[ Rules YAML files ] ←→ trusted IF chmod 600 + operator-curated
[ KB source files ] ←→ trusted IF operator-curated
```

### 6.4 Compliance posture

Red Robin is **operator-tooling**, not a managed service:
- Operator is responsible for ROE compliance per engagement contract
- Red Robin documents data flow but does not enforce client-specific policies (e.g., GDPR scope) — operator configures scope in engagement.yaml
- No telemetry collection in v0.1 (no anonymous usage stats)
- License posture: MIT for daemon + protocol + reference adapters

---

## 7. Scope Decisions (locked)

Decisiones intencionales sobre qué entra en v0.1 vs roadmap. **NO motivadas por timeline** — motivadas por focus, calidad, y delivery de un producto useful para red team operators.

| Decisión | Por qué |
|---|---|
| **UI: F (prompt_toolkit extendido) en lugar de E (Textual)** | Draft existente ya es TUI competente; mockup sirve como visual target preservando spatial layout. Migrar a Textual = full UI rewrite con poca mejora de UX percibida. |
| **Sliver-Mock en v0.1 + Sliver-Real comitted v0.1.1 (target 2026-06-22)** | Demo impact idéntico (TUI no diferencia mock vs real); commitment público con fecha forces delivery; marketing bonus de segundo news cycle. |
| **Adapter Template repo → v0.2** | Contrato documentado en protocol repo es suficiente para 2 adapters de v0.1. Template repo gana valor real solo cuando hay 5+ adapters en producción y community escalando. |

### v0.1 Scope (final, locked)

**In v0.1 (shippeable for DEF CON):**
- 8 components implemented as updated above
- Wire protocol v0.1.0 with 11 event types + extensions namespace
- 2 reference adapters: CS-Aggressor (full F) + Sliver-Mock (demo)
- 1 placeholder repo: Sliver-Real (with first issue tracking v0.1.1 implementation)
- TUI prompt_toolkit extended with 3 panels (Working Memory + Event Stream + Chat) + memory inspector overlay
- Layer A (YAML rules) bootstrap pack — **floor 12-15 concrete rules** shipped in package (listed in Componente 4); stretch 30+ rules curated during demo prep workstream
- Layer B (LLM judge) via Anthropic Claude (`claude-sonnet-4-6` + `claude-haiku-4-5`) with native `cache_control` prompt caching
- **5-layer memory system in v0.1 (L1, L2, L3, L4, L5 — full B3)** with Tradecraft Graph populated from MITRE ATT&CK + curated public OPSEC docs
- Engagement progress as first-class in L2 Working Memory
- Bilingual TUI (locale-detected labels, EN/ES system prompts)
- `import-linter` enforcement for dependency direction in CI
- Demo environment: Red Team Playground GOAD-Light + Sliver-Mock for talk apertura
- All artifacts: docs, schemas, fixtures, validator, transport.md, versioning-policy.md
- Paper rewrite (audit fixes + AI-tone scrub + ES version for La Villa)

**v0.1.1 commitment (target 2026-06-22, ~3 weeks post-DEF CON):**
- Sliver-Real adapter shipped (full gRPC subscriber + event translator + extractors)
- Replaces Sliver-Mock for community use

**Roadmap v0.2+ (post-v0.1.1):**
- Adapter Template repo + community boilerplate
- **Local mode bundle (LLM + embeddings together)** — preserves OPSEC promise for regulated engagements:
  - Local LLM via Ollama / Llama 3.3 (replaces Anthropic Claude)
  - Local embeddings via `sentence-transformers` (e.g., `BAAI/bge-large-en-v1.5` or `mixedbread-ai/mxbai-embed-large-v1`) — replaces Voyage
  - Both activate as a single switch (`config.toml [mode] local = true`); zero data leaves laptop in this mode
  - Quality trade-off: ~10-15% drop on retrieval benchmarks vs cloud SOTA, invisible at single-operator KB scale
- Sliver full S3 with wrapper shell for pre-flight intercept
- Network.* event types (lateral movement awareness)
- Adapter SDK library (Python helper)
- Havoc adapter
- Mythic adapter (with native OPSEC checks integration)
- Auto-discovery of adapters (mDNS / Bonjour)
- Multi-operator support (`source.operator_id`)
- ChainAST-style sophisticated context summarization
- pgvector / hosted vector store (if numpy bottlenecks)
- Tradecraft Graph data expansions (community contributions)

---

## 8. Implementation Workstreams

Workstream breakdown (sin time-boxing estricto — operator + peer ejecutan a su pace, deadline 2026-05-31 RTV / 2026-06-01 La Villa).

### Dependency-ordered list

**Phase 1 — Foundation (gates everything else):**

0. **Day-1 migrations from jose's draft (must complete before other work):**
   - `setuptools` → `uv` (pyproject.toml updated, lockfile generated, CI updated)
   - Remove `tiktoken` dependency (Anthropic native `cache_control` + narrow `count_tokens` usage covers token concerns per Section 13.3)
   - Remove `tomli`, `tomli-w` (use stdlib `tomllib` since target is 3.11+)
   - Add new deps: `fastapi`, `uvicorn[standard]`, `httpx`, `sqlalchemy[asyncio]`, `alembic`, `jsonschema`, `pyyaml`, `python-ulid`, `import-linter`, `ruff`, `mypy`, `pre-commit`
   - Update model strings: `claude-sonnet-4-5` → `claude-sonnet-4-6` (or current latest at impl time), keep `claude-haiku-4-5` for summarizer
   - **Rewrite `llm/client.py` → `infrastructure/llm/anthropic_client.py` with structured-block `system` array + `cache_control`** (per Section 13.3 pattern). Current peer draft (`dev-jose:src/redrobin/llm/client.py:59-64`) uses plain string `system` — no caching. Must migrate: system prompt block with `{"type": "ephemeral", "ttl": "1h"}`, KB chunks block with `{"type": "ephemeral"}`, engagement state block uncached. Observe `response.usage.cache_*` fields to validate.
   - **Remove `_estimate_tokens` from `context_builder.py:18-20`** (`len(text) // 4` heuristic). Cache hits drive cost optimization, not local estimation. `count_tokens` only used for explicit budget pre-flight, never in hot path.
   - Spike Phase 1 (must run before alias bodies are written): load minimal `.cna` with `alias mimikatz { blog($1, "0=$0 1=$1 2=$2 3=$3"); }` against CS 4.12 client. Type `mimikatz "sekurlsa::logonpasswords"` and `mimikatz logonpasswords` (unquoted variant). Document empirical values of `$2`, `$3` and quoted-arg handling in `docs/aggressor-alias-args.md`. Adjust alias body args accordingly before writing the 27 production stubs.

1. Wire protocol v0.1 spec + JSON schemas + fixtures + validator (with **Draft 2020-12 only**, no fastjsonschema fallback)
2. Repo setup (monorepo structure: `protocol/`, `adapters/cobaltstrike/`, `adapters/sliver-mock/`, `adapters/sliver-real/` placeholder)
3. Event model migration en `Red-Robin/redrobin/domain/events/` + cascade a todos los consumers (normalizer, triage→layer A, context_builder, agent loop, memory, render, tests)
4. **Async Alembic setup** — `uv run alembic init -t async migrations/` per Section 13.3 (non-negotiable; default template causes runtime failures with aiosqlite)

**Phase 2 — Daemon expansion (parallelizable):**
5. HTTP server + bearer auth + adapter sessions (FastAPI)
6. Memory layers L2 working memory schema + L3 episodic + L5 context summarizer extension (L1 extends existing `LongTermMemory`)
7. OPSEC engine (Layer A YAML rules engine + Layer B LLM judge with `cache_control` + verdict aggregator + severity model + override flow)
8. Bilingual i18n (TUI label abstraction + ES system prompts)

**Phase 3 — UI + Adapters (parallelizable after phase 2):**
9. TUI prompt_toolkit extended (3 panels + engagement progress + chat panel + memory inspector update)
10. CS adapter: `redrobin.cna` (Aggressor script with sub-central + ~22 Tier 1 aliases + ~5 Tier 2 + popup beacon rebuild) + `adapter.py` (CS REST API client + envelope normalizer) + `manifest.toml`
11. Sliver-Mock adapter (Python script + recorded scenarios JSON + manifest)
12. Bootstrap KB pack + Layer A rules (12-15 floor enumerated in Componente 4, 30+ stretch)

**Phase 4 — Integration + Demo + Paper:**
13. Integration testing E2E (adapter → daemon → engine → TUI con CS beacon real)
14. Demo prep (GOAD-Light scenario + Sliver-Mock replay + dry runs)
15. Paper rewrite (audit fixes + AI-tone scrub + ES version for La Villa + alias-gate honesty pass)

### Parallelization

Naturalmente parallelizable entre operator + peer una vez Phase 1 completo:

| Workstream group | Can run in parallel with |
|---|---|
| Memory layers | OPSEC engine (different modules) |
| TUI extension | CS adapter (different repos) |
| Sliver-Mock | Integration testing |
| Paper rewrite | Demo prep |

### Scope mitigation valves (si surge bloqueador)

Si algo se traba más de lo esperado, cortar en este orden:

1. Hold bootstrap rules at 12-15 floor (no stretch additions) — floor is non-negotiable but stretch is.
2. Reduce alias coverage to Tier 1 only (~22 aliases, skip Tier 2 warn-only) — gate still cinematic, less polish.
3. Skip dedicated integration testing phase, fold into adapter development.
4. Sliver-Mock se reduce a 2-3 events en vez de 6.
5. Paper rewrite ES version → drop, La Villa con paper EN únicamente.
6. Pre-record demo entirely instead of live.

### Risk areas & mitigations

- **DEF CON network unreliability:** Sliver-Mock with prerecorded engagement is ALSO the network-failure backup. Two birds, one stone.
- **Anthropic API latency on stage:** pre-recorded responses for 3-4 critical demo moments; live for the rest.
- **Bootstrap rules quality:** prefer 12-15 hand-validated rules (each mapped to a real EDR signature) over 30+ mediocre. Bootstrap floor is non-negotiable; stretch is opportunistic during demo prep.
- **Event model cascade complexity:** more files touch `Event` than expected; mitigate by writing migration script + comprehensive tests first.
- **Sliver-Real v0.1.1 commitment slippage:** monitor post-DEF CON; if blocks appear, communicate transparently to community via GitHub issue updates.

---

## 9. Closed Decisions (post-audit)

Cuestiones que estaban abiertas, ahora cerradas:

1. **Bilingual UX:** ✅ LOCKED.
   - TUI labels: detect-locale via `LANG` env var; ES y EN packs.
   - Slash commands: inglés siempre (universal).
   - LLM responses: detected from operator's input language; system prompts en ES y EN.
   - La Villa demo narrative: en ES; tool corre en ES locale.
   - i18n abstraction: ~1 day budget allocated.

2. **Engagement profile YAML format:** ✅ LOCKED — schema en sección 4 Componente 3. Refinements durante implementation pueden agregar campos opcionales sin breaking change (extensions namespace en YAML también).

3. **Bootstrap rules curation:** ✅ LOCKED — floor 12-15 concrete rules shipped in package (enumerated in Componente 4), stretch 30+ during demo prep.
   - Fuentes: MITRE ATT&CK detection mappings, CS OPSEC docs públicos (Cobalt Strike blog), HackTricks "noisy commands", vault Knowledge/ del operador.
   - Cada regla: pattern + context filter + severity + rationale + sources + suggestions.
   - Validación: cada regla mapeada a una detection real (no fantasy rules).
   - **Bootstrap floor is non-negotiable** — v0.1 cannot ship with an empty rules folder; operators must see the gate firing on day 1.

4. **Demo scenario script:** ✅ LOCKED en concepto (Acme Corp del mockup). Storyboard detallado producido durante demo prep workstream.

5. **License:**
   - Protocol repo: ✅ MIT
   - Reference adapters: ✅ MIT
   - Core daemon (Red-Robin): ✅ MIT (consistente con resto del ecosystem)

6. **Triage retire/retain decision:** ✅ LOCKED — Triage retired. Patrón de "interesting markers + debounce" subsumido en Layer A YAML rules. Tests reescritos.

7. **Voyage AI dependency:** ✅ LOCKED — `voyage-4-large` en v0.1 (current SOTA; `voyage-3` rejected as generation-behind). Documented as required dep. Local fallback (`sentence-transformers`) committed for v0.2, activated jointly with local LLM (Ollama) via single config switch — preserves OPSEC promise para regulated engagements.

8. **Existing vector store implementation:** ✅ Use as-is (numpy-based). Migration a pgvector → v0.2 si justifica volumen.

9. **`Event.timestamp` serialization:** ✅ ISO-8601 string en wire protocol; existing `to_dict()` already does `.isoformat()`. JSON Schema spec strictly types as `format: date-time`.

10. **Configurable shortcuts:** ✅ Preserved. Existing `[ui]` config section extends with new commands (`shortcut_alts`, `shortcut_explain`, etc.).

11. **Sliver adapter strategy:** ✅ LOCKED.
   - **v0.1:** Sliver-Mock (Python script con events pregrabados) — sirve para demo en talk + smoke-test del protocolo. README transparente sobre su naturaleza mock.
   - **v0.1.1:** Sliver-Real (gRPC subscriber + extractors + 6 events emitidos), commitment público con fecha **2026-06-22** (3 semanas post-DEF CON).
   - **Repo placeholder** `Red-Robin-Adapter-Sliver` creado en v0.1 con first issue tracking implementation, links cruzados desde Sliver-Mock README + paper.
   - **Razón:** demo impact idéntico (TUI no diferencia mock vs real); commitment forces delivery; segundo news cycle marketing bonus; reduces v0.1 scope sin diluir agnostic claim.

12. **Architecture style + abstractions:** ✅ LOCKED.
   - **Style:** Layered Architecture — 4 layers canónicas (`domain/`, `application/`, `infrastructure/`, `presentation/`). Architecture **deliberadamente simple**, standards **strictly enforced**.
   - **Dependency direction:** strictly enforced via `import-linter` config en CI. Reglas: domain no importa fuera de sí mismo; application no importa presentation; presentation no salta directo a infrastructure. Violation rechaza PR automáticamente.
   - **Interfaces explícitas en v0.1:** **una sola** — `domain/llm.py::LLMProvider` (justificada por multi-impl Anthropic v0.1 + Ollama v0.2 roadmap).
   - **Resto concrete** — VectorStore, Repos, EventEmitter, RulesLoader, etc. son clases concretas. Tests usan duck typing + `unittest.mock`.
   - **Distinción crítica:** standards strictly enforced (CI guards) ≠ architecture complicated (sin DI containers, sin patterns innecesarios, sin ABCs preventivas). Production-grade discipline sobre architecture simple.
   - **Razón:** scope real (single product, 2 personas inicialmente) no justifica el ceremony de DI containers o 6-8 ABCs preventivas. Python duck typing alcanza. Pero `import-linter` cuesta ~30 líneas de config y enforces la simplicidad — barato y alto ROI.

13. **Infrastructure layer naming:** ✅ LOCKED como `infrastructure/`. Descartados `services/` (ambiguity con DDD), `adapters/` (colisiona con root), `external/` / `integrations/` (no canónicos).

14. **Pre-flight gate mechanism for Cobalt Strike:** ✅ LOCKED.
    - **Mechanism:** Aggressor `alias` overrides redefine the **console parser command**; alias body calls the underlying Sleep function `&bmimikatz` / `&bshell` / etc. to dispatch the original task. The `alias` plane and the Sleep function plane are independent — no recursion possible.
    - **Bang-suffix `!`:** Quiet variant of a Sleep function (suppresses "Tasked beacon to run X" in console). Orthogonal to the gate; v0.1 uses non-quiet variant so the operator sees native CS console output unchanged.
    - **Why this and not `on beacon_input`:** `on beacon_input` event DOES exist in Aggressor (verified: Fortra's own `Cobalt-Strike/beacon_health_check/beaconhealth.cna` uses it, plus 76 GitHub `.cna` hits). However it is an **observation hook that fires AFTER the parser dispatches the command** — by the time the handler runs, the task is already encoded by `BeaconTaskBridge` and queued. Returning early does not cancel; the event has no documented blocking semantic. `set BEACON_INPUT` is a console display formatter, not an interceptor. `bcancel` only cancels file downloads. The `alias` mechanism intercepts at the **parser stage before dispatch** — the only production-proven pre-dispatch gate primitive in Cobalt Strike.
    - **Prior art:** SpecterOps OPSEC Profiles (bluscreenofjeff) shipping this pattern since 2018. Still works in CS 4.12 (Nov 2025).
    - **Coverage split — Tier 1A (built-ins) + Tier 1B (wrappers):** ~17 direct command aliases (`mimikatz`, `dcsync`, etc.) + ~7 wrapper aliases (`shell`, `run`, `execute-assembly`, `inline-execute`, `powershell`, `powerpick`) where the daemon does arg-pattern detection against `$0`. Wrappers catch external tools (NetExec, Rubeus, Invoke-Kerberoast, etc.).
    - **Scope:** ~24 Tier 1 aliases + ~5 Tier 2 + popup beacon rebuild. Single `redrobin_gate` sub-routine handles all gate logic; aliases are 4-5 LOC stubs. Total ~225 LOC for the `.cna`.
    - **Safety net:** non-aliased commands fall through to native CS dispatch; Path 3 (`set BEACON_OUTPUT`) captures all output regardless, so memory + retroactive WARN still work for anything outside the alias set. The gate is NOT the only line of defense.
    - **Honest limit:** gate is per-operator (the one who loaded `.cna`); see T11 in Threat Model. Mitigated by team SOP + observed-unmanaged-operator banner.

15. **Wire protocol position relative to C2 boundary:** ✅ LOCKED.
    - **Core never speaks C2 protocols.** Adapters are the sole boundary. REST API JWT, Aggressor primitives, gRPC stubs — all encapsulated inside adapter packages.
    - **Three flows, all routed through adapter:** (1) console gate via `.cna` alias → adapter → core; (2) TUI dispatch core → adapter → REST API; (3) output observation `BEACON_OUTPUT` hook → adapter → core.
    - **Implication:** swap C2 = swap adapter; core is untouched. Tests of core run with mock adapter.

---

## 10. Paper-vs-Reality Alignment

Tracker explícito de qué claims del paper actual son entregables en v0.1 vs cómo reformular:

| Paper claim | v0.1 reality | Action |
|---|---|---|
| "Initial support for Cobalt Strike and Havoc" | CS ✓ full F; Sliver-Mock for protocol demo, Sliver-Real comitted v0.1.1 (2026-06-22); Havoc ❌ (v0.2) | **Reformular** a "v0.1 ships Cobalt Strike adapter (full pre-flight, OPSEC engine, console annotation) plus a Sliver demonstration mock; Sliver real adapter ships in v0.1.1 (3 weeks post-DEF CON). Havoc and Mythic on the v0.2+ roadmap. The protocol itself is open and any operator can build their own adapter today." |
| "Flags risky commands before execution" | CS ✓ via Aggressor `alias` overrides (production-proven primitive, SpecterOps OPSEC Profiles since 2018); Sliver post-exec only (cuando Sliver-Real ship); per-operator scope honestly documented | **Reformular** a "Flags risky commands before execution via the Aggressor `alias` mechanism in Cobalt Strike — a production-proven primitive that intercepts operator input on the CS Client before any command reaches the team server. The gate is scoped to operators who load the `redrobin.cna` script; this client-side limit is an architectural property of Cobalt Strike's scripting model, not a Red Robin design choice. For C2 frameworks without an equivalent primitive (e.g., Sliver), Red Robin flags post-execution; capability degrades gracefully per adapter via the manifest." |
| "Internal knowledge base built from real-world red team engagements" | Public sources only (MITRE, CS docs, HackTricks, vault) | **Reformular** a "Operator-curated knowledge base from public sources (MITRE ATT&CK, public OPSEC documentation, HackTricks) plus the operator's own private vault. No client engagement data ingested." |
| "Carefully tuned LLM pipeline" | Vanilla Anthropic API with structured prompts | **Reformular** a "Anthropic Claude with structured retrieval over operator-curated knowledge base, contextualized by engagement state and episodic memory." |
| "Real-time guidance" | v0.1: push via `.cna` (CS) ~100ms latency. v0.1.1: gRPC subscriber (Sliver-Real) sub-second. | **Keep** but qualify: "Real-time push from the C2 framework via native scripting interfaces (Aggressor for Cobalt Strike, gRPC events for Sliver in v0.1.1)." |

---

## 11. Validation Against Three Axes

| Axis | Status | Evidence |
|---|---|---|
| **Scalable** | ✅ | Spec versioning + extensions namespace + capability negotiation + adapter repo separation; new types/adapters via MINOR releases without breaking changes |
| **Maintainable** | ✅ (with v0.1 artifacts) | Schemas, docs, fixtures, validator all part of v0.1 ship; adapter contract documented |
| **Production-grade** | ✅ | Bearer token auth, strict ingress validation, heartbeat, graceful capability degradation, idempotency via ULID, retry policy documented, audit trail via per-engagement jsonl |
| **Not over-complicated** | ✅ | YAGNI applied: 11 types not 35+, 9 manifest fields not 20+, no JWT/signing/gRPC/multi-protocol transport, prompt_toolkit reused over Textual rewrite, 1 ABC (LLM) instead of 6-8 preventive |

### 11.1 Performance Targets (v0.1 baseline)

SLAs explícitos para validar "production-grade." Cada uno medido en testing antes de marcar v0.1 como done.

| Metric | Target | Measurement |
|---|---|---|
| **Layer A verdict latency** | <100ms p95 | YAML rule match desde event ingest hasta verdict emitted |
| **Layer B verdict latency** (LLM judged) | <3s p95 | End-to-end including Anthropic API call (network latency dominant) |
| **Daemon cold start** | <10s | From `redrobin daemon start` to "listening on 127.0.0.1:7777" |
| **Memory footprint (steady state)** | <800MB | Daemon + TUI + numpy index loaded for 10k-doc KB |
| **Concurrent event throughput** | 20 events/sec sustained | No queueing buildup, no daemon CPU saturation |
| **KB indexing speed** | 1000 docs in <60s | First-time indexing of operator's vault folder |
| **Chat response latency** | <5s p95 | Operator types in TUI chat → response streaming starts |
| **Engagement context-switch** | <2s | `redrobin engagement use <slug>` from invocation to working memory loaded |
| **Working memory snapshot frequency** | Every 30s OR every 50 events | Resilience against daemon crash mid-engagement |
| **TUI render frame** | 30fps minimum | Working memory updates feel responsive, not stuttery |

**Out-of-scope SLAs (no targets v0.1):**
- Multi-engagement parallel performance (single-engagement at a time)
- Distributed deployment latencies (single-laptop only)
- Cold KB indexing for >50k docs (operator's vault expected <10k for foreseeable future)

**Anthropic API rate limit assumptions (must be validated against actual Pro tier limits):**

Anthropic API uses **Tier 1-4 + Custom** scaling (see [platform.claude.com/docs/en/api/rate-limits](https://platform.claude.com/docs/en/api/rate-limits)). NOT the consumer Pro plan (Pro is claude.ai, a different product). Performance targets assume:

| Assumption | Justification |
|---|---|
| Layer A handles ~90% of events without LLM | Rule-based fast path; only escalates novel/ambiguous events to Layer B |
| Layer B escalation rate <10% of events | Verified empirically post-implementation; tunable via rule severity thresholds + passthrough_whitelist |
| **Effective Anthropic API call rate: <2 RPS sustained** | 20 events/sec × 10% escalation = 2 RPS = **120 RPM** |
| **API tier requirement: Tier 2+** | Tier 1 default = 50 RPM Sonnet 4.x → would be exceeded by 2.4×. Tier 2 (1,000 RPM Sonnet) handles 120 RPM sustained with burst headroom for chat. Operator should request tier upgrade BEFORE DEF CON if Anthropic hasn't auto-upgraded based on spend. |
| **Cache hit rate**: aim ≥70% on system prompt + KB chunks via `cache_control` | Hits at ~10% input token cost — keeps cost predictable across the engagement |

**Retry strategy (single layer — SDK only):**

Anthropic Python SDK auto-retries with exponential backoff by default (`max_retries=2`) on connection errors, 408/409/429/5xx. To avoid stacked retries (custom layer + SDK = up to 10 effective retries with weird delays), Red Robin uses **only the SDK retry layer**, configured at client construction:

```python
import anthropic

client = anthropic.AsyncAnthropic(
    max_retries=5,         # 1s, 2s, 4s, 8s, 16s exponential, on 429/5xx/connection errors
)
# Optional per-request override:
# await client.with_options(max_retries=0).messages.create(...)
```

No custom backoff layer. TUI banner shows "LLM rate-limited, retrying..." when SDK enters retry path (detect via `response.headers['retry-after']` or hook into SDK retry callback if needed).

**Graceful degradation expected when targets not met:**
- LLM API timeout → fall back to Layer A only with banner notification
- KB index >800MB → operator-actionable warning to prune kb-source/
- Daemon CPU >80% sustained → operator-actionable warning to investigate (likely runaway adapter)

---

## 12. References

- **Repo:** `cfp-cr/Red-Robin` (private)
- **Branches:**
  - `main` — production target
  - `dev-jose` — peer's original draft (preserved as reference for cherry-picks)
  - `dev-juan` — operator's working branch for v0.1 implementation
- **Local clone path:** `C:\Users\jcspi\Documents\dev-projects\Red-Robin`
- **Implementation branch:** `dev-juan` — operator works here, cherry-picking from `dev-jose` per the cherry-pick table in section 4 component 8. Integration with peer happens via separate PRs once both branches are aligned.
- **Spec doc location:** `Projects/red-robin-architecture-design.md` (vault), to be copied to `docs/architecture-design.md` in the repo on first commit to the implementation branch
- **Project note:** `Projects/red-robin-copilot.md` (vault)
- **Mockup:** `_Claude/redrobin-mockup/index.html` (servir con http.server)
- **Conference paper draft:** `Projects/RedRobin_Conference_Paper_EN.md` (vault) — needs audit fixes per Section 8
- **CFPs:**
  - Red Team Village @ DEF CON 34: https://sessionize.com/rtv-at-dc34 (deadline 2026-05-31)
  - La Villa @ DEF CON 34: https://sessionize.com/la-villa-DEFCON-34/ (deadline 2026-06-01)
- **Related projects:**
  - Red Team Playground: production-grade since 2026-03-27, will host demo lab
  - PentAGI: comparison reference (autonomous, contraste vs Red Robin's augmented thesis)

---

## 13. Technology Stack

Decisiones de tecnología consolidadas para v0.1, con rationale y alternativas consideradas. Cada elección sigue los principios: production-grade + scalable + maintainable + simple (no over-engineered).

### 13.1 Languages & Runtime

| Tech | Version | Rationale | Alternatives considered |
|---|---|---|---|
| **Python** | 3.11+ | Async maturo, type hints solid, `tomllib` en stdlib, ecosystem LLM/AI mejor | 3.10 (rejected: missing tomllib stdlib + match statement) |
| **Sleep / Aggressor Script** | Whatever ships con CS 4.12+ | Required for CS adapter (`.cna` script in CS Client) | None — Sleep es lo que CS exige |
| **Go** | 1.22+ | Para Sliver-Real adapter v0.1.1 (deferred). Sliver mismo es Go, gRPC nativo | Python (rejected for Sliver: less ergonomic gRPC) |

### 13.2 Build & Package Management

| Tech | Rationale | Alternatives considered |
|---|---|---|
| **uv** | 10-100x más rápido que pip/poetry para resolution. Single tool para venv + install + lock + run. Industry trend 2026. Backed by Astral (creator of ruff). | poetry (rejected: slower, fragmented), setuptools+pip (rejected: jose's choice but uv strictly better in 2026), hatch (rejected: less momentum) |
| **pyproject.toml** | PEP 621 standard | None — required |

### 13.3 Daemon Core Dependencies

**Web / API layer:**

| Tech | Version | Rationale | Alternatives considered |
|---|---|---|---|
| **FastAPI** | latest | Type-hint-driven, auto-docs, async-native, industry standard 2026 para Python APIs | aiohttp (rejected: lower-level, less typed), Flask (rejected: sync, dated), Litestar (rejected: less mature) |
| **uvicorn** | latest | ASGI server estándar para FastAPI | hypercorn (rejected: less common) |
| **httpx** | latest | Async-native HTTP client. Mismo API que `requests` + `async/await`. Industry standard async desde 2022. | requests (rejected: síncrono, bloquea event loop), aiohttp (rejected: lower-level API) |

**httpx production pattern (must implement):**

Best practice: **one long-lived `AsyncClient` per process, shared.** If implementation creates `async with httpx.AsyncClient() as client:` per request (common mistake), connection pooling is wasted, every request opens a new TCP connection.

```python
# infrastructure/transport/http.py
from httpx import AsyncClient, Timeout, Limits

# Module-level singleton, initialized at daemon startup
_client: AsyncClient | None = None

DEFAULT_TIMEOUT = Timeout(connect=3.0, read=30.0, write=5.0, pool=3.0)
# read=30s tolerates slow LLM calls; connect/write fast for localhost adapters

DEFAULT_LIMITS = Limits(max_keepalive_connections=20, max_connections=100)

async def get_client() -> AsyncClient:
    global _client
    if _client is None:
        _client = AsyncClient(timeout=DEFAULT_TIMEOUT, limits=DEFAULT_LIMITS)
    return _client

async def shutdown():
    if _client:
        await _client.aclose()
```

Daemon initializes this client at startup, all outbound HTTP (Anthropic, Voyage, optional CS REST API state recovery) shares it.

**LLM / Embeddings:**

| Tech | Version | Rationale | Alternatives considered |
|---|---|---|---|
| **anthropic** | >=0.60.0 | Official Anthropic SDK. **Native prompt caching via `cache_control`** is the production cost/latency strategy — NOT manual `count_tokens` orchestration. SDK auto-retries on 429/5xx with `max_retries=2` default (configurable). Floor `>=0.60` ensures current-era `cache_control` improvements + bug fixes (>=0.39 is from late 2024 and lacks several features). | langchain (rejected: abstraction layer overkill para 1-2 providers) |

**Prompt caching strategy (must implement) — Anthropic native `cache_control`:**

The Anthropic API supports prompt caching natively. Mark stable prompt prefixes with `cache_control: {"type": "ephemeral"}` and the API returns cache hits at ~10% of normal input cost and dramatically reduced TTFT. This **supersedes any manual token-counting + approximation strategy** — `count_tokens` becomes only useful for explicit budget checks at trim time, not for cost optimization.

```python
# infrastructure/llm/anthropic_client.py
import anthropic

client = anthropic.AsyncAnthropic()  # api_key from env

async def verdict(operator_msg: str, kb_chunks: list[str], engagement: dict) -> str:
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=[
            # STABLE: persona + format spec — cache this aggressively (1h TTL)
            {
                "type": "text",
                "text": RED_ROBIN_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
            # SEMI-STABLE: KB chunks for this engagement — cache 5min default
            {
                "type": "text",
                "text": "\n\n".join(kb_chunks),
                "cache_control": {"type": "ephemeral"},
            },
            # DYNAMIC: engagement state — do NOT cache (changes per call)
            {
                "type": "text",
                "text": f"<engagement>{json.dumps(engagement)}</engagement>",
            },
        ],
        messages=[{"role": "user", "content": operator_msg}],
    )
    # response.usage.cache_creation_input_tokens — first call writes cache
    # response.usage.cache_read_input_tokens — subsequent calls read at 10% cost
    return response.content[0].text
```

**Cache invalidation rules:**
- System prompt: invalidated only on version bump (~never during a session)
- KB chunks: invalidated on engagement switch or KB re-index
- Engagement state: never cached (changes per turn)

**`count_tokens` usage (narrow):**

`client.messages.count_tokens()` is a **server-side API call (not local compute)**. It is **free of charge** but consumes RPM quota — so use sparingly in the hot path. Reserve it for explicit pre-flight budget checks before sending oversized contexts (verify the assembled prompt fits in the model context window). For routine verdict latency, the cache hit path dominates — token counting is not needed in the hot path.

This keeps Layer B verdict latency target (<3s p95) viable AND reduces API costs by ~80% on cached prefixes vs naive uncached calls.
| **voyageai** | >=0.3.7 | `voyage-4-large` — current SOTA en MTEB (May 2026). Originally Anthropic partnership confirmed at launch; Voyage adquirido por MongoDB Feb 2025 — partnership status validar al impl time. `voyage-3` es generation-behind y fuera del free tier post-acquisition; no usar. | OpenAI text-embedding-3 (rejected: separate ecosystem), Cohere embed-v3 (rejected: third ecosystem), sentence-transformers (rejected for v0.1 cloud-first; **committed v0.2 candidate para local mode** — paralelo a Ollama) |

**Persistence:**

| Tech | Version | Rationale | Alternatives considered |
|---|---|---|---|
| **SQLAlchemy Core 2.0** | latest | Type-safe query builder sin ORM ceremony. Production standard Python. | Raw SQL (rejected: refactor cost), full ORM (rejected: overkill 6 tablas), peewee (rejected: less mature 2.0 async), sqlc-style codegen (rejected: less Pythonic) |
| **aiosqlite** | >=0.20 | Async wrapper sobre SQLite stdlib. Rock solid. | sqlite3 sync (rejected: bloquea async daemon) |
| **Alembic** | latest | Migration framework standard de SQLAlchemy ecosystem. Versioned, reversible. | yoyo-migrations (rejected: smaller community), DIY migrations (rejected: not production-grade) |

**⚠ Async Alembic — use the OFFICIAL async template (CRITICAL implementation note):**

Alembic ships an **official async template** since 1.9. Initialize the migrations directory with:

```bash
uv run alembic init -t async migrations/
```

This generates the correct `env.py` using `async_engine_from_config()` + `connection.run_sync()` out of the box. **Do NOT use `alembic init migrations/` (default template)** — it generates a sync `env.py` that fails at runtime with `aiosqlite`.

If a migration directory has already been generated with the default template, the manual bridging pattern below brings it to parity with the async template. New work should use `-t async` and skip this entirely.

```python
# migrations/env.py — async template (or manual bridging)
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

async def run_async_migrations():
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()

def run_migrations_online():
    asyncio.run(run_async_migrations())
```

This is **non-negotiable** for v0.1. Document in `migrations/README.md` and link from CONTRIBUTING.md so future contributors don't trip over it.

**Vector store:**

| Tech | Version | Rationale | Alternatives considered |
|---|---|---|---|
| **numpy** | >=1.26 | In-memory vector ops. Suficiente para single-operator KB (~10k-50k docs). | pgvector (rejected: requires Postgres, overkill), faiss (rejected: heavyweight, C++ deps), chromadb (rejected: extra service), Qdrant (rejected: extra service), pinecone (rejected: cloud-only) |

**Schema / Validation:**

| Tech | Version | Rationale | Alternatives considered |
|---|---|---|---|
| **jsonschema** | >=4.20 | JSON Schema 2020-12 validator (`Draft202012Validator`). Industry standard for our chosen draft version. | fastjsonschema (**rejected: does NOT support Draft 2020-12** — only drafts 04/06/07. If perf becomes issue, downgrade protocol to Draft 07 in v0.2 to enable fastjsonschema), pydantic v2 (used implicitly via FastAPI for HTTP, no extra dep) |

**Performance note for jsonschema:** naive use (re-compile per event) is ~100x slower than fastjsonschema. With the compile-once pattern below, the actual gap shrinks to ~7-8x — well under the 50ms p95 budget for our 20 events/sec target. Mitigation: **compile validators once at process startup**, reuse for all events:

```python
# infrastructure/schema/validators.py
from jsonschema import Draft202012Validator

# At module import time, NOT per-call
ENVELOPE_VALIDATOR = Draft202012Validator(envelope_schema)
EVENT_VALIDATORS = {
    "beacon.command_input": Draft202012Validator(beacon_input_schema),
    # ... per type
}

# Per-event hot path
def validate_event(event: dict) -> None:
    ENVELOPE_VALIDATOR.validate(event)
    EVENT_VALIDATORS[event["event"]["type"]].validate(event)
```

For our 20 events/sec target with single-threaded validation, this stays well under 50ms p95 for the validation step.
| **pyyaml** | >=6.0 | YAML loader para rules + manifests + engagement profile. Industry standard. | ruamel.yaml (rejected: slower, less common) |
| **tomllib** | stdlib (3.11+) | TOML reader para `config.toml`. Sin dependency externa. | tomli (rejected: redundant en 3.11+), tomli-w (rejected: no escribimos TOML) |

**File / IO:**

| Tech | Version | Rationale | Alternatives considered |
|---|---|---|---|
| **watchdog** | >=4.0 | File system watching para KB folder + rules folder hot-reload | inotify directo (rejected: linux-only) |
| **aiofiles** | >=23.0 | Async file I/O wrapper | pure stdlib + asyncio.to_thread (rejected: more verbose) |
| **pypdf** | >=4.0 | Parse PDFs in KB folder | pymupdf (rejected: **AGPL v3** — strict copyleft, network use triggers source disclosure obligation for the whole daemon; even commercial license available, but AGPL is incompatible with Red Robin's MIT goal), pdfplumber (rejected: heavier dependency) |

**Identifiers / Time:**

| Tech | Version | Rationale | Alternatives considered |
|---|---|---|---|
| **python-ulid** | latest | ULID para event IDs (time-sortable, globally unique) | UUIDv7 (rejected: less library support), UUIDv4 (rejected: not time-sortable) |

### 13.4 TUI

| Tech | Version | Rationale | Alternatives considered |
|---|---|---|---|
| **prompt_toolkit** | >=3.0.40 | Mature, full-screen TUI capabilities, ya en draft de jose | Textual (rejected: full UI rewrite cost vs marginal benefit, F decision lockeada) |

### 13.5 Adapters

**CS-Aggressor adapter:**

| Tech | Rationale |
|---|---|
| **Sleep / Aggressor Script** | Required by Cobalt Strike Client for hooks |
| **Python 3.11+** (connector) | Reusa daemon stack |
| **httpx** (in connector) | Same async HTTP client |
| **mgeeky `httprequest.cna`** (gist as base) | Production-tested HTTP impl en Sleep |

**Sliver-Mock adapter:**

| Tech | Rationale |
|---|---|
| **Python 3.11+** | Same stack as daemon |
| **httpx** | POST events to daemon (uses same long-lived AsyncClient pattern) |
| **Pre-recorded JSON files** | Multiple scenarios shipped: `acme_corp.json` (Windows AD), `linux_demo.json` (Linux post-exploitation) |
| **CLI scenario selector** | `redrobin-sliver-mock --scenario <name>` loads specific scenario for demo |

**Required scenarios in v0.1 ship:**

1. `linux_demo.json` — Linux post-exploitation (operator on web shell, escalates to root, exfils data). Used for demo Scene 1 to demonstrate cross-C2 protocol agnostic.
2. `acme_corp.json` — Windows AD engagement (matches Acme Corp scenario from mockup). Used as offline demo backup if CS demo network fails.

Each scenario file is JSON array of events with `delay_ms` between them, simulating realistic timing. Scenarios curated by operator + peer during demo prep workstream.

**Sliver-Real adapter (v0.1.1 roadmap):**

| Tech | Rationale |
|---|---|
| **Python 3.11+** | Stack uniforme con daemon. Operator + peer no necesitan Go expertise para v0.1.1 sprint. |
| **grpcio + grpcio-tools** | Standard Python gRPC. `grpcio-tools` regenerates Python stubs from Sliver `.proto` files. |
| **Sliver proto regeneration** | Pin Sliver release version, vendor `.proto` files from upstream `github.com/BishopFox/sliver/protobuf/`, run `python -m grpc_tools.protoc` to generate `_pb2.py` + `_pb2_grpc.py` stubs into `adapter/sliver_pb/`. Documented in adapter README. |
| **mTLS** | Sliver server auth via operator config files (.cfg). Parsed manually (small format) — no library wrapper needed. |

**Decision rationale: protoc-generated client, NOT SliverPy library.**

[SliverPy](https://github.com/sliverarmory/sliver-py) is the historically obvious choice but **stale on master + unreleased newer work**: `master` HEAD is 2023-04-28; `v1.6.x-update` and `v1.6.x` branches have HEADs at 2024-11-25 but no release > v0.0.19 (Dec 2022) cuts that work. README explicitly says "Not all features in Sliver v1.5+ are supported yet." Active Sliver development since the last released wrapper has added protobuf fields and RPCs that SliverPy v0.0.19 does not surface. Using SliverPy means depending on either a stale release or an unreleased branch — both unacceptable for v0.1.1 ship discipline.

The protoc-generated stub approach:
- Adapter owns the Python gRPC stubs (generated, vendored).
- Pin Sliver version in `manifest.toml` requirements: `sliver: ">=1.5,<2.0"`.
- Regenerate stubs on Sliver minor version bumps (1-2 hour task, documented).
- Zero dependency on external Python wrapper that may abandon.

**Implementation skeleton (for v0.1.1 sprint reference):**

```python
# adapters/sliver-real/adapter.py
import grpc
from .sliver_pb import client_pb2, client_pb2_grpc, sliver_pb2

async def stream_session_events(server_addr: str, mtls_creds):
    async with grpc.aio.secure_channel(server_addr, mtls_creds) as channel:
        stub = client_pb2_grpc.SliverRPCStub(channel)
        async for event in stub.Events(client_pb2.Empty()):
            # event.EventType, event.Session, event.Job, etc.
            await push_to_daemon(normalize_event(event))
```

**Trade-off honest:** Go via `grpc-go` would give ~30% better gRPC streaming latency (low-level efficiency) but the difference does not affect our SLAs. If Sliver-Real bottlenecks in gRPC stream consumption post-launch, Go rewrite is a v0.2 candidate.

### 13.6 Testing & Quality

| Tech | Rationale | Alternatives considered |
|---|---|---|
| **pytest** | Industry standard Python testing | unittest (rejected: less ergonomic) |
| **pytest-asyncio** | Async test support | None — required |
| **pytest-cov** | Coverage reporting (target: 80% domain layer minimum, 60% overall) | coverage.py directly (rejected: less integrated) |
| **ruff** | >=0.4 | Linter + formatter unificado. 10-100x más rápido que flake8+black+isort. Industry trend 2026. Single tool. | flake8+black+isort (rejected: 3 tools, slower), pylint (rejected: slow, opinionated) |
| **mypy** | >=1.10 | Type checking. Strict mode en `domain/`, gradual en infrastructure. | pyright (rejected: less Pythonic, more aggressive), pyre (rejected: less mature) |
| **import-linter** | >=2.11 | Enforces dependency direction (domain ↛ infrastructure, etc.) en CI. ~30 lines config. **Critical for "strict standards + simple architecture"**. Current version 2026-03-06. | tach (rejected: less mature), custom AST checks (rejected: reinventing wheel) |
| **pre-commit** | >=3.7 | Git hooks for ruff + mypy + import-linter | husky-style (rejected: not Pythonic) |

### 13.7 CI / Build / Distribution

| Tech | Rationale | Alternatives considered |
|---|---|---|
| **GitHub Actions** | Native con repo, free para open source, mature ecosystem | GitLab CI, CircleCI (rejected: extra services) |
| **Dependabot** | Weekly dependency updates con auto-PR | Renovate (rejected: more setup), manual (rejected: not maintainable) |
| **uv lock** | Reproducible builds via lockfile | requirements.txt (rejected: less precise) |
| **PyPI distribution** | Standard Python distribution (post v0.1.1, optional) | Single binary via PyInstaller (deferred to v0.2 if needed) |

### 13.8 Observability

| Tech | v0.1 | v0.2 roadmap | Rationale |
|---|---|---|---|
| **Logging** | stdlib `logging` con JSON formatter | Same | Operator on laptop, no observability platform needed |
| **Metrics** | None | None planned (single-operator) | Overkill para single-operator deployment |
| **Tracing** | None | OpenTelemetry traces (opt-in) | Solo si community demand emerges |

### 13.9 Documentation

| Tech | Rationale |
|---|---|
| **README.md** | Per repo/component. Multi-component overview en root. |
| **`docs/` Markdown** | Spec, architecture, runbooks. Versionado con código. |
| **MkDocs Material** (post-v0.1) | Si community demanda site, MkDocs es más friendly que Sphinx |
| **JSON Schema → docs auto-gen** | Protocol docs generados de schemas (single source of truth) |

### 13.10 External Cloud Dependencies (the only outbound)

| Service | Used by | Frequency | Cost |
|---|---|---|---|
| **Anthropic API** | Daemon (LLM judge + chat) | Per verdict + per chat turn | Token-based ($) |
| **Voyage AI API** | Daemon (KB indexing) | Solo cuando se actualiza el KB folder (esporádico) | Free tier generally suficiente |
| **GitHub** | Repo hosting + CI | Continuous | Free open source |
| **Anthropic / Voyage status** | Their respective status pages | Si daemon falla | N/A |

### 13.11 Demo Infrastructure (DEF CON / La Villa)

| Tech | Purpose |
|---|---|
| **AWS** | GOAD-Light hosting (operator's existing Red Team Playground production-grade desde 2026-03-27) |
| **Cobalt Strike 4.12+** | Operator's licensed copy (1 año licencia activa) |
| **Terraform + Ansible** | Red Team Playground IaC (existing) |
| **Netbird** | Mesh networking entre operator laptop ↔ engagement infra (existing) |

### 13.12 Network Footprint Inventory

**Local-first architecture: todo el daemon corre on operator's laptop. Solo dos conexiones outbound regularly:**

```
LOCAL (operator's laptop)                       OUTBOUND (only when needed)
═══════════════════════════                     ═══════════════════════════════
Daemon (Python process)        ─── HTTPS ───►  api.anthropic.com (LLM)
TUI (Python process)
HTTP server (localhost:7777)   ─── HTTPS ───►  api.voyageai.com (embeddings,
                                                only during KB indexing)
SQLite databases
numpy vector store
KB source folder
Rules YAML files
Engagement state
Logs

CS Client (operator's app)
+ redrobin.cna (loaded inside) ─── (CS native flow) ──► Teamserver (DO/AWS)
                                                        ⚠ NOT a Red Robin connection
                                                        ⚠ Daemon NEVER touches teamserver
```

**Implicación crítica:** auditor inspecciona teamserver y target → cero rastro de Red Robin. Promesa OPSEC del paper se cumple por diseño técnico, no por policy.

**Graceful degradation:**
- Operator offline / red corporativa bloquea Anthropic → Layer A (rules) sigue funcionando + working memory + TUI. Layer B (LLM) y chat se desactivan con banner amarillo "LLM offline".
- DEF CON network unreliable → Sliver-Mock con replay engagement local sirve como demo backup.

### 13.13 OS Support

| OS | v0.1 status |
|---|---|
| **Linux (Kali, Ubuntu)** | ✅ Primary target. Daemon + TUI + adapters tested. |
| **macOS (Apple Silicon + Intel)** | ✅ Target. Operator's MBP scenario. |
| **Windows** | 🟡 CS Client + `.cna` corre nativo. Daemon + TUI corren via WSL2 (recommended) o native Python (best effort). |

### 13.14 Dependencies summary (pyproject.toml v0.1)

```toml
[project]
name = "redrobin"
version = "0.1.0"
requires-python = ">=3.11"

dependencies = [
    # Web / HTTP
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "httpx>=0.27",

    # LLM / Embeddings
    "anthropic>=0.39",
    "voyageai>=0.3",

    # Persistence
    "sqlalchemy[asyncio]>=2.0",
    "aiosqlite>=0.20",
    "alembic>=1.13",

    # Schemas / config
    "jsonschema>=4.20",
    "pyyaml>=6.0",
    # tomllib in stdlib (3.11+)

    # Vector / numerics
    "numpy>=1.26",

    # Filesystem / I/O
    "watchdog>=4.0",
    "aiofiles>=23.0",
    "pypdf>=4.0",

    # IDs / utilities
    "python-ulid>=2.0",

    # TUI
    "prompt-toolkit>=3.0.40",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-cov>=4.1",
    "ruff>=0.4",
    "mypy>=1.10",
    "import-linter>=2.0",
    "pre-commit>=3.7",
]
```

### 13.15 Decisions explicitly NOT made (deferred to community / v0.2+)

- **Local mode (Ollama LLM + sentence-transformers embeddings)** — v0.2 roadmap, activación conjunta. Preserva OPSEC promise (zero cloud) para regulated engagements.
- **pgvector / external vector DB** — v0.2 if numpy bottlenecks (>50k docs).
- **OpenTelemetry full** — only if community demands.
- **Multi-language adapter SDK** — v0.2 si community contributes adapters in non-Python.
- **Container/Docker images** — single-binary distribution v0.2 candidate.

---

## 14. Sign-Off

This document represents decisions locked through brainstorming session 2026-04-29 to 2026-05-08 between operator (jcspiedra) and Claude, audited via auditing-plans skill on 2026-05-08.

**Audit applied:** 3 CRITICAL + 5 DESIGN + 4 MINOR issues addressed inline. Scope decisions locked for focus + quality (UI = F prompt_toolkit, Sliver-Mock + Sliver-Real comitted v0.1.1 by 2026-06-22, 5-layer memory completo en v0.1 incluyendo Tradecraft Graph, Adapter Template = v0.2 roadmap, bilingual i18n locked, paper rewrite includes ES version for La Villa). Topology corrected to clarify that the daemon never connects to teamserver — only adapters do, and only through their C2's native flow.

**Architecture foundations locked (post-audit refinement):**
- **Focus:** ship a useful product for red team operators that is scalable, maintainable, production-grade — NOT optimize for fitting an arbitrary timeline.
- **Language:** Python (locked after explicit deliberation, not by inheritance from peer's draft).
- **Architecture style:** Layered (4 canonical layers — `domain/`, `application/`, `infrastructure/`, `presentation/` split as `api/` + `tui/`). Architecture deliberadamente simple (no DI containers, no 6-8 ABCs preventivas) **PERO standards strictly enforced** vía `import-linter` en CI. Distinción clave: enforced standards ≠ complicated architecture.
- **Dependency direction:** strictly enforced. Domain no importa de afuera de sí mismo; application no importa presentation; presentation no salta directo a infrastructure. Violation rechaza PR.
- **Interfaces / abstractions:** ONE explicit ABC en v0.1 (`LLMProvider` for Anthropic + Ollama swap). Everything else concrete. YAGNI hasta que YANI ("you actually now need it").
- **Memory:** 5 layers completos en v0.1 (Long-term, Working, Episodic, Tradecraft Graph, Context Management). Sin cuts.
- **Anti-patterns explicitly rejected:** `utils/` at top-level, ambiguous `services/application/agents/` overlap, mixing of `models/` with `domain/`, premature interfaces, DI containers, repository pattern theater.
- **Cherry-pick approach** to peer's draft: preserve UX patterns + KB infrastructure + token budgeting + system prompt voice; discard outdated event model + in-process adapter pattern + Triage heuristic.
- **Implementation branch:** not yet created — operator + peer create at start of implementation per their workflow preference.

**Pending peer review.** No implementation should begin until peer (collaborator) has reviewed and aligned on this design and the timeline mitigations.

**Next step after sign-off:** writing-plans skill produces detailed implementation plan with task breakdown, dependencies, and acceptance criteria, mapped to the parallelization plan in §8.
