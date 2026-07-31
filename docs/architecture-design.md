# Architecture

Red Robin is an operation-aware AI copilot that sits *beside* a red-team operator's
C2, never in front of it. This document explains the shape of the system and why it
is shaped that way. It is background reading, not a procedure.

Facts owned elsewhere are linked, not repeated:

| For | Read |
|---|---|
| Event types, routes, enums, field names | [`.claude/rules/wire-protocol.md`](../.claude/rules/wire-protocol.md) |
| Settings and the on-disk layout | [configuration.md](configuration.md) |
| The layer contracts CI enforces | [`importlinter.cfg`](../importlinter.cfg) |
| Getting it running | [operator-setup.md](operator-setup.md) |
| Loading the Cobalt Strike hook | [adapters/cobaltstrike/README.md](../adapters/cobaltstrike/README.md) |
| Proving the capture path works without a Beacon | [smoke-test.md](smoke-test.md) |
| A code-level tour | [developer-onboarding.md](developer-onboarding.md) |

---

## The thesis: capture, then advise

Red Robin **observes** what the operator does and **advises** in a chat pane. It
does not sit in the command path, never holds a command while it thinks, and has no
way to dispatch one.

That is a deliberate reversal. The project began as a synchronous OPSEC pre-flight
gate that judged every Beacon command before it left the console. That gate was
deleted — see [What was removed](#what-was-removed-and-why), which is the single
most important section here if you are wondering why an OPSEC copilot blocks
nothing. What is left fits in one line:

> **The adapter captures. The core advises.**

Two properties follow, and most of the design is downstream of them.

**Nothing can block the operator.** There is no code path from Red Robin into a C2 —
no dispatch route on the daemon, no outbound client in any adapter, no Aggressor
`alias` override. Advice is text in a pane: a suggested action, the tradecraft
rationale, a detection-risk rating, and what the assistant is explicitly unsure
about. The operator decides. The assistant's system prompt
(`application/agents/assistant/agent.py`) opens by saying so — *"You are ADVISORY
ONLY: you propose the next step, you never execute it and never dispatch to the
beacon."*

**Nothing waits on a decision.** No component blocks on a Red Robin verdict, because
there are no verdicts. An LLM call that takes four seconds, an expired API key, or a
daemon that is down changes what the operator *sees*, never what they can *do*. Every
consumer degrades to silence rather than failing.

---

## Topology

Three processes on the operator's own machine, and nothing else:

```
  OPERATOR'S MACHINE
  ┌──────────────────────────┐
  │  Cobalt Strike client    │              ┌──────────────────┐
  │   └─ redrobin.cna  ──────┼──POST───────▶│ Red Robin daemon │
  └──────────┬───────────────┘  /v1/events  │ (FastAPI, 7777)  │
             │ native CS RPC                └────────┬─────────┘
             │ (Red Robin is not in this path)       │ WS /v1/tui
             ▼                                       ▼
     CS team server ──▶ Beacon              ┌──────────────────┐
                                            │  Red Robin TUI   │
                                            └──────────────────┘
```

The daemon binds `127.0.0.1:7777` by default and all state lives under
`~/.redrobin/`; the only outbound traffic is to the model providers (Anthropic for
generation, Voyage for embeddings). There is no team-server footprint and no shared
service. The core holds **no C2 credentials** and does not know a team server
exists — a compromised daemon still cannot run a command.

---

## The capture path

The `.cna` registers three hooks in the Cobalt Strike client — `on beacon_input`,
`on beacon_initial`, and a `set BEACON_OUTPUT` filter — and backfills
`beacon.initial` for Beacons that already existed when it loaded. Each hook builds a
wire envelope and POSTs it to `/v1/events`.

`api/events_router.py` then does three things, in this order:

1. **Validates** the envelope and the event body against `protocol/schemas/v0.1/`.
   Every schema is `additionalProperties: false`, so an unknown key is a hard
   reject and the wire contract cannot drift silently.
2. **Appends** the raw envelope to the active engagement's `events.jsonl`. This is
   the audit trail and the only complete record of the operation.
3. **Publishes** a `{type, payload}` dict on the in-process event bus.

Order matters: persist-then-publish means a crash between the two loses a bus event,
never the record. The inverse mistake — publishing without persisting, or persisting
without republishing — has been made once already and is called out in the
wire-protocol rules. Ahead of all three sits a dedup gate
(`application/dedup_keys.py`), because a C2 can re-emit the same output.

**The capture is fail-open by construction.** Emits are synchronous on the Aggressor
hook thread with a short HTTP timeout, so a slow or absent daemon shows up as brief
console lag and a warning line, never as a blocked command. A failed POST is a
permanently lost event — capture emits are not retried — and every emit is wrapped so
that an exception cannot escape the `BEACON_OUTPUT` filter before it returns the
operator's output unchanged. Losing an event always beats swallowing output.

One implementation detail is load-bearing enough to name: Sleep cannot reach
`java.net.http` without JVM flags, so a small helper JAR
(`adapters/cobaltstrike/helper-jar/`) wraps the HTTP client and mints envelope IDs.
Without it there is no capture at all.

---

## What runs inside the daemon

`api/lifespan_helpers.py` holds one `start_*` function per startup concern, each
returning a close hook; `api/app.py` calls them in a fixed order and tears them down
in reverse:

```
event bus → engagement → dedup keys → working memory (+ reducer)
          → enricher → assistant (+ chat recorder) → L5 summariser
```

The order is load-bearing. The engagement resolves first because everything after it
needs to know which engagement directory to open. Working memory starts before the
enricher because the enricher only *produces* facts — the reducer must already be
subscribed to merge them.

The bus (`application/event_bus.py`) is deliberately unremarkable: in-process
fan-out, one bounded queue per subscriber, and a full queue drops its **oldest**
message. A slow consumer degrades itself, never the producer. There is no broker and
no delivery guarantee, because the durable record is `events.jsonl`.

Four consumers subscribe:

**The working-memory reducer** (`application/working_memory_reducer.py`) is the
single writer to working memory. It turns `beacon.initial` into a session, records
one episode per `beacon.command_input`, and merges `working_memory.deltas` batches.
Exactly one writer is what makes working memory safe to read without locking, and it
is why the enricher publishes deltas instead of writing them.

**The LLM enricher** (`application/agents/enricher/`) reads `beacon.command_output`
and asks a cheap model to extract structured facts — hosts, credentials, findings,
tools, defences — then publishes them as `working_memory.deltas`. It is the only
producer of those deltas; there is no regex fallback. The domain type whitelists keys
and system-attaches provenance, so the model can populate fields but cannot invent
them.

**The assistant** (`application/agents/assistant/`) consumes `enrichment.completed`
and publishes `advisor.recommendation`. Before spending a token it runs a cheap
deterministic gate (`triggers.py`): dedup on correlation ID, novelty against the last
utterance, and a cooldown — speaking is the expensive path, so silence is the
default. Then one agentic cycle with a push/pull context split: a small resident set
is *pushed* (working memory, the story so far, recent conversation) and everything
else is *pulled* through tools (`kb_retrieve`, `search_journal`, `get_lessons`). Like
the reducer's single-writer rule, the assistant never writes memory; it publishes.

**The chat recorder** (`application/chat/recorder.py`) is the single writer to the
chat transcript, mapping the assistant stream onto durable turns.

One subsystem is not a bus consumer: the **L5 summariser**
(`application/agents/summarizer.py`) wakes on a timer and, once enough new episodes
have accumulated, compresses that window into one summary row.

All of them share a never-crash contract — subscribe, loop, dispatch, exit on the
bus's close sentinel, log and swallow every per-event exception. A failure enriching
one command must not take down the daemon recording the operation.

---

## Memory

Red Robin's value is that it remembers the operation. That is several stores rather
than one, because they answer different questions and have different lifetimes.

| Layer | What it holds | Single writer | Scope |
|---|---|---|---|
| **L1 long-term** | Operator corrections: what to do differently, and why | The assistant, when the operator corrects it | Shared file; rows scoped `operator` (cumulative) or `engagement` (quarantined) |
| **L2 working memory** | The live picture: hosts, credentials, findings, sessions, objectives, phase, scope, ROE | The reducer | Per engagement |
| **L3 episodic** | One row per command the operator ran | The reducer | Per engagement |
| **L5 context summary** | A rolling prose summary of recent episodes — the story so far | The L5 summariser | Shared file, rows keyed by engagement |
| **Operational journal** | The verbatim command/output transcript, vector-indexed | Derived from `events.jsonl`, refreshed incrementally | Per engagement |
| **Chat transcript** | The operator↔assistant conversation | The chat recorder | Per engagement |

Paths are in [configuration.md](configuration.md#on-disk-layout). The numbering is
historical: L4, a tradecraft graph, was specified and never built, and the journal and
chat transcript arrived after the numbered set. What matters is the axes they cut on.

**Distilled versus verbatim.** L2 and L5 are lossy by design — a compact picture small
enough to carry in every prompt. The journal is the opposite: the exact text of what
happened, far too large to push, so it is retrieved on demand through a tool. An
assistant with only the distillation would confabulate details; one with only the
transcript would drown in them.

**Per-engagement versus cross-engagement.** Working memory, episodes, the journal and
the chat transcript are quarantined per engagement, so switching clients never mixes
hosts or credentials and a fact learned inside a honeypot cannot follow you to the
next operation. Two things deliberately cross: operator-scoped lessons — accumulating
is the entire point — and the curated knowledge base.

**Curated versus observed.** The knowledge base is markdown the operator wrote or
collected, embedded into a vector index under `shared/` — the only memory that does
not come off the wire, and the only one identical on every engagement. It and the
journal are the two vector-backed stores (Voyage embeddings over a small numpy cosine
index); everything else is plain JSON, JSONL, or SQLite. There is no database server.

---

## The layering

`redrobin/` is a conventional layered package: `api | tui` → `application` →
`domain`, with four contracts in [`importlinter.cfg`](../importlinter.cfg) enforced
in CI. The *why* is worth more than the rules:

- **`domain/` is pure** — dataclasses, parsing, selection, and nothing else. It
  imports nothing from the rest of the package, which is what lets it stay under
  mypy strict and be tested with no daemon, no filesystem, and no API key.
- **`application/` owns orchestration** — the reducer, the agents, the services; also
  mypy strict. It may not import `api/` or `tui/`, which is why the chat recorder
  re-implements a slice of advisory formatting rather than reusing the TUI's
  renderer. That duplication is the price of the boundary, and cheaper than the
  coupling.
- **`api/` and `tui/` never reach `infrastructure/` directly.** They go through an
  application facade — `application/transport_auth.py` is the smallest example, a
  three-line pass-through that exists purely to keep the contract honest. This is
  what keeps presentation swappable: the TUI and the HTTP API are two clients of one
  application layer, not two forks of it.

`infrastructure/` holds the I/O concretes — SQLite repos, the Anthropic and Voyage
clients, the vector store, the filesystem layout. mypy is gradual there.

The seam that makes this pay off is the narrow Protocol. Instead of one large LLM
interface, each consumer declares only the call it needs — `EnricherLLM.extract`,
`AgentLLM.run`, `L5LLM.summarize`, `LessonStore` — and its factory returns `None`
when the key is missing. Tests inject a plain async stub, production injects an
Anthropic client, neither knows about the other, and degraded operation falls out of
the same seam instead of needing its own machinery.

---

## The adapter boundary

C2-specific knowledge lives entirely in `adapters/<name>/`. The core speaks the wire
protocol and nothing else, and does not import the adapters package at all — swapping
the C2 changes only the adapter.

An adapter announces itself over `POST /v1/adapters/connect` with a manifest — name,
version, supported spec range, events emitted, capabilities — and then POSTs events
to `/v1/events`. That handshake is the whole contract; the `manifest.toml` beside
each adapter documents the same declaration for humans. There are two:

- **`cobaltstrike/`** — the Aggressor script above, plus its helper JAR.
- **`sliver-mock/`** — a Python replayer that pushes recorded scenarios at the
  daemon. It exists to prove the wire protocol is not secretly Cobalt-Strike-shaped,
  and it doubles as the way to exercise the whole pipeline with no Beacon
  ([smoke-test.md](smoke-test.md)).

That second adapter is the load-bearing part of the claim, because a boundary nothing
has ever crossed is a boundary nobody has tested.

Being honest about the boundary's limits: it is clean structurally — no imports, no
C2 API surface, no credentials in core — but the *vocabulary* is Beacon-shaped. The
wire protocol says `bid`, `beacon.initial`, `beacon.command_output`, and the domain
types follow it. That is a real assumption baked into the protocol rather than a
layering violation, and it is what a third adapter would test hardest.

---

## The TUI

`redrobin/tui/textual_ui/` is a Textual app connected to `WS /v1/tui`, authenticated
by a `?token=` query parameter read from `~/.redrobin/transport.token`. Three panes —
sessions, assistant chat, working memory — plus a modal inspector and a help overlay.

Inbound frames are routed by type through a small registry (`frames.py`): handlers
register against a frame type, and an unknown type is dropped rather than crashed on.
That indirection is what lets the chat module handle its own frames without importing
the app module, keeping the import graph acyclic, and it makes adding a frame type a
pure append.

On connect the daemon replays two frames before streaming live events: a
`working_memory_snapshot`, so a TUI attached mid-operation is not blank, and a
`chat_history`, so the conversation survives a restart. Both are re-sent on every
reconnect; the snapshot is a full replace and the chat pane hydrates once.

The socket is **bidirectional but asymmetric**. The daemon forwards every bus event
downstream. Upstream, the endpoint allowlists exactly two types —
`operator.question` and `operator.answer` — and stamps them `source: "operator"`, so
the TUI cannot forge an engagement, memory, or capture event. It has no HTTP client
at all.

There is one confirmation affordance, and it is worth being precise about given the
advisory-only thesis: when the assistant proposes a milestone or phase change it asks
for an explicit `/y` or `/n`. That confirms a change to *working memory*. It gates
nothing operational, and every recommendation the TUI renders says so in as many
words.

---

## What was removed, and why

Red Robin was born as a **synchronous OPSEC pre-flight gate**. Aggressor `alias`
overrides intercepted the operator's dangerous commands — `mimikatz`, `dcsync`,
`psexec`, `jump`, `getsystem` and more — and held each one while the daemon returned
a verdict: a Sigma rule pack ("Layer A") on the fast path, an LLM judge ("Layer B")
behind it, producing PASS / WARN / BLOCK / ESCALATE. A DANGER verdict hard-blocked;
the command never reached the Beacon.

It was removed on 2026-07-26 (PR #36): the gate, the verdict vocabulary, the Sigma
rule pack, the automatic lesson promoter, and all 28 alias overrides. The outbound
Cobalt Strike REST dispatch client followed shortly after (PR #44), which is what
makes "no code path into a C2" true by construction rather than by convention.

Three reasons:

**The product had moved.** The value is the copilot that explains, not the checker
that rules. The gate answered a question the operator had already answered by typing
the command.

**It stood between the operator and their C2.** An LLM call in the critical path of a
live operation is latency nobody asked for and a failure mode nobody can debug
mid-op. Worse, it failed closed: with the daemon unreachable the gate returned
`UNAVAILABLE`, which the alias tier treated as dangerous and blocked on.

**It cost more to explain than to run.** Roughly 1400 lines across eight application
modules and two API surfaces, plus a two-tier verdict protocol every reader had to
learn before they could understand what the tool did.

**What replaced it** is advice with the *why*, in the chat pane, alongside the
operation instead of inside it. An operator who disagrees corrects the assistant, and
the correction is stored as a lesson read back on later cycles — a learning loop the
gate never had, because a gate that gets overridden teaches nothing.

**The one non-obvious consequence.** Because the gate fired on every command, the
verdict stream had accidentally become the system's record of operator activity —
episodes were derived from it, and the L5 summary from episodes. Deleting the gate cut
the vehicle, not the need, so L3 was re-sourced from `beacon.command_input`, which is
what it should have been all along. The full reasoning is in
`docs/superpowers/specs/2026-07-25-remove-preflight-gate-design.md`.

Traces of the removal survive in the tree and are not bugs: an `Episode` docstring
explaining why it is no longer a verdict row, an env var
(`REDROBIN_ALLOW_DEGRADED`) whose name outlived the layer it named, and a couple of
SQLite tables still created but never read.

---

## Degraded operation

Missing keys are a supported state, because a capture-only daemon still records the
operation. Boot fails fast on missing secrets unless the operator sets
`REDROBIN_ALLOW_DEGRADED`; past that, every LLM-backed component independently
returns `None` from its factory and goes quiet:

- **No `ANTHROPIC_API_KEY`** — no enrichment, no advice, no summaries. Capture, the
  audit trail, sessions and the TUI all still work, but working memory holds only
  what the engagement definition seeded plus what the reducer takes straight off the
  wire.
- **No `VOYAGE_API_KEY`** — the assistant still runs, but `kb_retrieve` and
  `search_journal` report themselves unavailable, so it reasons from the pushed
  context alone.
- **No active engagement** — per-engagement paths fall back to the root and
  `events.jsonl` is not written at all; the ingest endpoint reports `stored: false`.

The assistant is additionally **off by default** (`REDROBIN_ASSISTANT_ENABLED`) — it
was dark-launched and the flag has not been flipped. A daemon that captures but never
speaks is usually that, not a bug.
