# Data-Quality Group A — Extraction Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the enricher from polluting working memory with junk — a username extracted as a host (#105) and a username-only line extracted as a secret-less credential (#114) — so the future AI assistant reads a clean substrate.

**Architecture:** Two defects, one root cause: the enricher LLM emits junk and nothing rejects it. #114 is a clean deterministic rule in `WorkingMemoryDeltas.from_payload` (drop a credential with no secret material). #105 is three layers, all derived from live state with **zero hardcoded victim data**: (1) de-personalize the static enricher prompt (remove the hardcoded `'jcspi'`), (2) inject the current beacon's own host/user into each extraction call's prompt as a small dynamic grounding (the enricher caches `bid → (host, user)` from `beacon.initial`, mirroring its existing `bid → command` cache), (3) a deterministic backstop in `apply_deltas` that drops an incoming host whose name matches any known username in live working memory (the big username set lives in deterministic code, never the prompt).

**Tech Stack:** Python 3.12, asyncio, Anthropic SDK (Haiku enricher), pytest, ruff/mypy/import-linter, `uv`.

---

## Pre-existing context (read once before starting)

- **Spec:** `docs/superpowers/specs/2026-05-28-data-quality-design.md` — §2 (writer-split: the enricher writes *observation*; this plan keeps it honest), §3 (constraints: no hardcoded victim data, deterministic-first, C2-portable, stateless enricher), §6 (Group A — this plan), §11 (acceptance).
- **Branch:** `wm/dq-a-extraction`, off `dev-juan` `ef811b0` (Group D merged). Already checked out.
- **The enricher** (`redrobin/application/enrichment/enricher.py`): an async bus consumer. `run()` subscribes to the bus and filters by `type`: `beacon.command_input` → `_remember_command` (caches `bid→command` in `_cmd_by_bid` + `corr→command` in `_cmd_by_corr`), `beacon.command_output` → `_enrich`, all else → ignored. `_enrich` recovers the command, strips self-annotations, runs `should_enrich`, chunks, calls `_extract_chunk` per chunk (which calls `self._llm.extract(command=, beacon_id=, output=)`), merges, and publishes `working_memory.deltas`. **The grounding cache mirrors `_cmd_by_bid` exactly.**
- **The enricher LLM contract** (`redrobin/application/enrichment/llm.py`): `class EnricherLLM(Protocol): async def extract(self, *, command: str, beacon_id: str | None, output: str) -> str: ...`.
- **The Anthropic enricher client** (`redrobin/infrastructure/llm/anthropic_client.py`): `AnthropicEnricherClient._SYSTEM_PROMPT` is the static cached prompt — it currently hardcodes `'jcspi'`. `extract` builds the user message as `f"Command: {command}\nBeacon: {beacon_id}\nOutput:\n{output}"`.
- **`from_payload`** (`redrobin/domain/memory.py`): `WorkingMemoryDeltas.from_payload(payload, *, source_beacon_id)` whitelists keys and builds the deltas. `_opt_str` / `_str_list` helpers live in the same module.
- **`apply_deltas`** (`redrobin/infrastructure/persistence/working_memory_repo.py`): `wm = self.load()`, then a host loop (`for host in deltas.hosts: self._merge_host(wm, host)`), then credentials/findings/tools/defenses, then `self.save(wm)`. `_merge_host` dedups by `upper(name)`.
- **Models** (`redrobin/domain/memory.py`): `Session(beacon_id, hostname, user, integrity)`; `Credential(domain, username, ntlm_hash, secret_type, secret, source_beacon_id)`; `Host(name, os, ...)`.

## Standing constraints (apply to EVERY command/step)

- All `uv` invocations use `--no-sync`. Prepend `REDROBIN_ALLOW_DEGRADED=1` to the full-suite pytest.
- Atomic conventional commits, one per task. **NO AI/Claude co-author trailer.**
- TDD: failing test first → run-fail → implement → run-pass → commit. Suite green after every task.
- Production-grade, no dead code. **No hardcoded victim/machine data anywhere** — all #105 grounding/backstop data is derived at runtime from live state.
- `.cna` is NOT touched in this plan. Layer rule (import-linter): `from_payload` = domain; backstop = infrastructure; enricher = application; anthropic_client = infrastructure (`application → infrastructure` allowed). mypy gate = `redrobin/domain` + `redrobin/application` only.
- Baseline pytest = **571** (post-Group-D). Each task notes its delta.

## File structure

| File | Change | Layer |
|---|---|---|
| `redrobin/domain/memory.py` | MODIFY — `from_payload`: drop a credential with no `secret` AND no `ntlm_hash` (#114). | domain |
| `redrobin/infrastructure/llm/anthropic_client.py` | MODIFY — de-personalize `_SYSTEM_PROMPT` (remove `'jcspi'`); add a pure `_build_enricher_user_message(...)` that injects per-beacon grounding; `extract` gains `host`/`user`. | infrastructure |
| `redrobin/application/enrichment/llm.py` | MODIFY — extend `EnricherLLM.extract` protocol with `host`/`user`. | application |
| `redrobin/application/enrichment/enricher.py` | MODIFY — `bid → (host, user)` cache from `beacon.initial`; thread host/user into `extract`. | application |
| `redrobin/infrastructure/persistence/working_memory_repo.py` | MODIFY — deterministic host-vs-username backstop in `apply_deltas` (+ `_bare_username`, `_known_usernames`, a module logger). | infrastructure |
| `tests/unit/test_memory_deltas.py` | EXTEND — #114 rule tests. | tests |
| `tests/unit/enrichment/test_llm.py` | EXTEND — prompt de-personalization + `_build_enricher_user_message` grounding tests. | tests |
| `tests/integration/test_enricher.py` | EXTEND — enricher caches `beacon.initial` + passes host/user to `extract`. | tests |
| `tests/unit/test_working_memory_repo.py` | EXTEND — host backstop tests. | tests |

Order: **T1 (#114) → T2 (de-personalize) → T3 (grounding) → T4 (backstop) → T5 (gate+smoke)**. T1/T2 are isolated one-file changes; T3 is the multi-file grounding; T4 is the deterministic net; T5 verifies + hands off.

---

## Task 1: #114 — drop the secret-less credential in `from_payload`

**Files:**
- Modify: `redrobin/domain/memory.py` (`WorkingMemoryDeltas.from_payload`, the credentials loop)
- Test: `tests/unit/test_memory_deltas.py`

The current credentials loop in `from_payload`:

```python
        credentials: list[Credential] = []
        for c in payload.get("credentials") or []:
            if not isinstance(c, dict) or not c.get("username"):
                continue
            credentials.append(
                Credential(
                    domain=str(c.get("domain", "")),
                    username=str(c["username"]),
                    ntlm_hash=_opt_str(c.get("ntlm_hash")),
                    secret_type=str(c.get("secret_type") or "ntlm"),
                    secret=_opt_str(c.get("secret")),
                    source_beacon_id=source_beacon_id,
                )
            )
```

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_memory_deltas.py` (the file already imports `WorkingMemoryDeltas`; if not, add `from redrobin.domain.memory import WorkingMemoryDeltas`):

```python
def test_from_payload_drops_credential_with_no_secret() -> None:
    # #114: a username with no secret material is a username observation, not a
    # credential. The enricher sometimes emits the logged-in user this way.
    deltas = WorkingMemoryDeltas.from_payload(
        {"credentials": [{"domain": "CORP", "username": "jdoe"}]},
        source_beacon_id="B-1",
    )
    assert deltas.credentials == []


def test_from_payload_keeps_credential_with_plaintext_secret() -> None:
    deltas = WorkingMemoryDeltas.from_payload(
        {"credentials": [{"username": "svc_sql", "secret": "Summer2026!", "secret_type": "plaintext"}]},
        source_beacon_id="B-1",
    )
    assert len(deltas.credentials) == 1
    assert deltas.credentials[0].username == "svc_sql"
    assert deltas.credentials[0].secret == "Summer2026!"


def test_from_payload_keeps_credential_with_ntlm_hash() -> None:
    deltas = WorkingMemoryDeltas.from_payload(
        {"credentials": [{"username": "admin", "ntlm_hash": "aad3b435b51404ee:31d6cfe0d16ae931"}]},
        source_beacon_id="B-1",
    )
    assert len(deltas.credentials) == 1
    assert deltas.credentials[0].ntlm_hash == "aad3b435b51404ee:31d6cfe0d16ae931"


def test_from_payload_drops_credential_with_empty_secret_strings() -> None:
    deltas = WorkingMemoryDeltas.from_payload(
        {"credentials": [{"username": "jdoe", "secret": "", "ntlm_hash": ""}]},
        source_beacon_id="B-1",
    )
    assert deltas.credentials == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/test_memory_deltas.py::test_from_payload_drops_credential_with_no_secret tests/unit/test_memory_deltas.py::test_from_payload_drops_credential_with_empty_secret_strings -v`
Expected: FAIL — the current code appends the secret-less credential (the two "drops" tests fail; the two "keeps" tests already pass).

- [ ] **Step 3: Implement the #114 rule**

In `redrobin/domain/memory.py`, replace the credentials loop body so a credential with no `secret` and no `ntlm_hash` is skipped:

```python
        credentials: list[Credential] = []
        for c in payload.get("credentials") or []:
            if not isinstance(c, dict) or not c.get("username"):
                continue
            secret = _opt_str(c.get("secret"))
            ntlm_hash = _opt_str(c.get("ntlm_hash"))
            # #114: a credential with no secret material is just a username
            # observation (the enricher sometimes emits the logged-in user). Drop
            # it — CREDENTIALS is for usable secrets, not bare usernames.
            if not secret and not ntlm_hash:
                continue
            credentials.append(
                Credential(
                    domain=str(c.get("domain", "")),
                    username=str(c["username"]),
                    ntlm_hash=ntlm_hash,
                    secret_type=str(c.get("secret_type") or "ntlm"),
                    secret=secret,
                    source_beacon_id=source_beacon_id,
                )
            )
```

(Note: `_opt_str` returns `None` for `None` and `str(value)` otherwise; an empty string `""` stays `""`, which is falsy → correctly dropped.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/test_memory_deltas.py -v`
Expected: all pass (the 4 new + every pre-existing from_payload test).

- [ ] **Step 5: Run the full CI gate**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q
```
Expected: green. Pytest 571 → 575 (+4).

- [ ] **Step 6: Commit**

```bash
git add redrobin/domain/memory.py tests/unit/test_memory_deltas.py
git commit -m "fix(enrich): drop secret-less credentials in from_payload (#114)"
```

---

## Task 2: De-personalize the enricher static prompt (#105 layer 1)

**Files:**
- Modify: `redrobin/infrastructure/llm/anthropic_client.py` (`AnthropicEnricherClient._SYSTEM_PROMPT`)
- Test: `tests/unit/enrichment/test_llm.py`

The static prompt currently contains (in the not-a-host examples): `'jcspi' (a username)`. `'jcspi'` is the operator's real username — hardcoded victim data baked into the cached prompt (violates spec §3.1). Replace it with a generic example.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/enrichment/test_llm.py` (add `from redrobin.infrastructure.llm.anthropic_client import AnthropicEnricherClient` to the imports if absent — `_SYSTEM_PROMPT` is a class attribute, so no instantiation / API key is needed):

```python
def test_enricher_system_prompt_has_no_hardcoded_victim_username() -> None:
    # #105 layer 1: the static (cached) prompt must NOT embed any specific
    # operator/victim username. It teaches "usernames are not hosts" with a
    # generic example; the live beacon's actual user is injected dynamically
    # per call (see _build_enricher_user_message), never hardcoded here.
    prompt = AnthropicEnricherClient._SYSTEM_PROMPT.lower()
    assert "jcspi" not in prompt
    # The not-a-host teaching is still present.
    assert "username" in prompt
    assert "host" in prompt
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync pytest tests/unit/enrichment/test_llm.py::test_enricher_system_prompt_has_no_hardcoded_victim_username -v`
Expected: FAIL on `assert "jcspi" not in prompt`.

- [ ] **Step 3: De-personalize the prompt**

In `redrobin/infrastructure/llm/anthropic_client.py`, in `AnthropicEnricherClient._SYSTEM_PROMPT`, the not-a-host examples are on this string-literal line (verified — `anthropic_client.py:192`; the prompt is built from concatenated literals, so this is the EXACT contiguous literal):

```python
        "of NOT-a-host: 'jcspi' (a username), 'Windows' or 'C:' (a drive/volume "
```

`'jcspi'` occurs EXACTLY ONCE in `redrobin/` (only here), so the simplest robust edit is to replace just the token. Change that line to:

```python
        "of NOT-a-host: 'administrator' (a username), 'Windows' or 'C:' (a drive/volume "
```

Result: the full sentence becomes `"...Cross-OS examples of NOT-a-host: 'administrator' (a username), 'Windows' or 'C:' (a drive/volume label, e.g. from 'Volume in drive C is Windows'), 'web-svc' (a service name), '/etc/passwd' (a path)."` — a universal example, no victim data. (If your editor matches the bare token, replacing `'jcspi'` → `'administrator'` is unambiguous since it appears once.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --no-sync pytest tests/unit/enrichment/test_llm.py -v`
Expected: pass.

- [ ] **Step 5: Run the full CI gate** (same 5 gates as Task 1 Step 5.)
Expected: green. Pytest 575 → 576 (+1).

- [ ] **Step 6: Commit**

```bash
git add redrobin/infrastructure/llm/anthropic_client.py tests/unit/enrichment/test_llm.py
git commit -m "fix(enrich): de-personalize enricher prompt — drop hardcoded username (#105)"
```

---

## Task 3: Dynamic per-beacon grounding (#105 layer 2)

**Files:**
- Modify: `redrobin/application/enrichment/llm.py` (`EnricherLLM.extract` protocol)
- Modify: `redrobin/infrastructure/llm/anthropic_client.py` (pure `_build_enricher_user_message` + `extract` gains `host`/`user`)
- Modify: `redrobin/application/enrichment/enricher.py` (`bid → (host, user)` cache + thread into `extract`)
- Test: `tests/unit/enrichment/test_llm.py`, `tests/integration/test_enricher.py`

Inject the **current beacon's own host/user** into each extraction call so the LLM is told not to re-extract them as hosts. Derived at runtime from `beacon.initial` (the enricher already sees these on the bus). Small payload (two values), O(1).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/enrichment/test_llm.py` (add `from redrobin.infrastructure.llm.anthropic_client import _build_enricher_user_message`):

```python
def test_build_user_message_without_grounding() -> None:
    msg = _build_enricher_user_message(
        command="whoami", beacon_id="B-1", output="CORP\\jdoe", host=None, user=None
    )
    assert "Command: whoami" in msg
    assert "Output:\nCORP\\jdoe" in msg
    # No grounding line when host/user are unknown.
    assert "do NOT emit" not in msg


def test_build_user_message_with_grounding() -> None:
    msg = _build_enricher_user_message(
        command="dir", beacon_id="B-1", output="...", host="WS-FINANCE-03", user="CORP\\jdoe"
    )
    assert "WS-FINANCE-03" in msg
    assert "CORP\\jdoe" in msg
    assert "do NOT emit" in msg  # the grounding instruction is present
```

Append to `tests/integration/test_enricher.py` (add imports as needed: `import pytest`, `from redrobin.application.enrichment.enricher import WorkingMemoryEnricher`):

```python
class _GroundingRecordingLLM:
    """EnricherLLM stub that records every extract() call and returns empty deltas."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def extract(
        self, *, command: str, beacon_id: str | None, output: str,
        host: str | None = None, user: str | None = None,
    ) -> str:
        self.calls.append(
            {"command": command, "beacon_id": beacon_id, "output": output, "host": host, "user": user}
        )
        return "{}"


class _NoopBus:
    async def publish(self, event: dict) -> None:
        return None


@pytest.mark.asyncio
async def test_enricher_grounds_extract_with_beacon_host_user() -> None:
    llm = _GroundingRecordingLLM()
    enricher = WorkingMemoryEnricher(
        bus=_NoopBus(),  # type: ignore[arg-type]
        llm=llm,  # type: ignore[arg-type]
        denylist=[],
        min_output_chars=0,
        chunk_max_chars=10000,
    )
    # The enricher learns this beacon's own host/user from beacon.initial.
    enricher._remember_session({"bid": "B-1", "host": "WS-FINANCE-03", "user": "CORP\\jdoe"})
    await enricher._enrich({"bid": "B-1", "correlation_id": "c1", "output": "some command output here"})

    assert llm.calls, "extract was never called"
    assert llm.calls[0]["host"] == "WS-FINANCE-03"
    assert llm.calls[0]["user"] == "CORP\\jdoe"


@pytest.mark.asyncio
async def test_enricher_grounding_is_none_for_unknown_beacon() -> None:
    llm = _GroundingRecordingLLM()
    enricher = WorkingMemoryEnricher(
        bus=_NoopBus(),  # type: ignore[arg-type]
        llm=llm,  # type: ignore[arg-type]
        denylist=[],
        min_output_chars=0,
        chunk_max_chars=10000,
    )
    # No beacon.initial seen for B-9 → grounding is None, no crash.
    await enricher._enrich({"bid": "B-9", "correlation_id": "c2", "output": "some command output here"})
    assert llm.calls
    assert llm.calls[0]["host"] is None
    assert llm.calls[0]["user"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/unit/enrichment/test_llm.py::test_build_user_message_with_grounding tests/integration/test_enricher.py::test_enricher_grounds_extract_with_beacon_host_user -v`
Expected: FAIL — `_build_enricher_user_message` doesn't exist; `_remember_session` doesn't exist; `extract` doesn't accept `host`/`user`.

- [ ] **Step 3: Extend the `EnricherLLM` protocol**

In `redrobin/application/enrichment/llm.py`, change the protocol method signature:

```python
class EnricherLLM(Protocol):
    """Single-call extraction: command output -> JSON deltas (as a string)."""

    async def extract(
        self,
        *,
        command: str,
        beacon_id: str | None,
        output: str,
        host: str | None = None,
        user: str | None = None,
    ) -> str: ...
```

- [ ] **Step 4: Add the pure message builder + grounding in `anthropic_client.py`**

In `redrobin/infrastructure/llm/anthropic_client.py`, add a module-level pure function (near the top, after imports), and use it in `AnthropicEnricherClient.extract`:

```python
def _build_enricher_user_message(
    *,
    command: str,
    beacon_id: str | None,
    output: str,
    host: str | None = None,
    user: str | None = None,
) -> str:
    """Build the enricher's per-call user message. Injects a small dynamic
    grounding line naming THIS beacon's own host/user (derived at runtime from
    its session — never hardcoded) so the LLM does not re-extract the operator's
    own identity as a newly-discovered host (#105). The grounding line is omitted
    when neither is known."""
    lines = [f"Command: {command}", f"Beacon: {beacon_id}"]
    own: list[str] = []
    if host:
        own.append(f"host={host}")
    if user:
        own.append(f"user={user}")
    if own:
        lines.append(
            "This beacon's own identity (" + ", ".join(own) + "); do NOT emit its "
            "own host or user as a newly-discovered host."
        )
    lines.append(f"Output:\n{output}")
    return "\n".join(lines)
```

Then change `AnthropicEnricherClient.extract` to accept `host`/`user` and use the builder:

```python
    async def extract(
        self,
        *,
        command: str,
        beacon_id: str | None,
        output: str,
        host: str | None = None,
        user: str | None = None,
    ) -> str:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=[
                {
                    "type": "text",
                    "text": self._SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": _build_enricher_user_message(
                        command=command,
                        beacon_id=beacon_id,
                        output=output,
                        host=host,
                        user=user,
                    ),
                }
            ],
        )
        return response.content[0].text
```

- [ ] **Step 5: Add the `bid → (host, user)` cache + thread it through the enricher**

In `redrobin/application/enrichment/enricher.py`:

(a) In `__init__`, add the cache (after `self._cmd_by_bid: dict[str, str] = {}`):

```python
        # Per-bid own-identity cache from beacon.initial (host, user), used to
        # ground each extraction so the LLM doesn't re-extract this beacon's own
        # host/user as a newly-discovered host (#105). One entry per beacon —
        # naturally bounded, no eviction needed.
        self._host_user_by_bid: dict[str, tuple[str | None, str | None]] = {}
```

(b) In `run()`, add a `beacon.initial` branch (before the `beacon.command_output` check, alongside the `beacon.command_input` branch):

```python
                if etype == "beacon.initial":
                    self._remember_session(evt.get("payload") or {})
                    continue
```

(c) Add the `_remember_session` method (next to `_remember_command`):

```python
    def _remember_session(self, payload: dict[str, Any]) -> None:
        """Cache this beacon's own (host, user) from beacon.initial for grounding."""
        bid = payload.get("bid")
        if bid is None:
            return
        host = payload.get("host")
        user = payload.get("user")
        self._host_user_by_bid[str(bid)] = (
            str(host) if host is not None else None,
            str(user) if user is not None else None,
        )
```

(d) In `_enrich`, after `src` is computed (`src = str(bid) if bid is not None else None`), look up the grounding and thread it into the per-chunk gather:

```python
        host, user = (
            self._host_user_by_bid.get(src, (None, None)) if src is not None else (None, None)
        )
        ...
        per_chunk = await asyncio.gather(
            *(self._extract_chunk(command, src, c, host, user) for c in chunks)
        )
```

(e) Update `_extract_chunk` to accept + forward `host`/`user`:

```python
    async def _extract_chunk(
        self, command: str, src: str | None, chunk: str,
        host: str | None = None, user: str | None = None,
    ) -> WorkingMemoryDeltas | None:
        assert self._llm is not None
        for attempt in (1, 2):
            async with self._sem:
                raw = await self._llm.extract(
                    command=command, beacon_id=src, output=chunk, host=host, user=user
                )
            try:
                return parse_deltas(raw, source_beacon_id=src)
            except DeltaParseError as exc:
                if attempt == 2:
                    logger.warning("enrichment: unparseable LLM output, dropping chunk: %s", exc)
                    return None
        return None
```

(Keep the existing docstring/comment in `_extract_chunk`; only the signature + the `extract(...)` call gain `host`/`user`.)

(f) **CRITICAL — update the existing enricher-driven test fakes (audit C2).** Once `_extract_chunk` calls `extract(..., host=host, user=user)`, EVERY fake LLM that the real enricher drives must accept those kwargs or it raises `TypeError: extract() got an unexpected keyword argument 'host'`. `EnricherLLM` is a structural Protocol, so the defaults on the protocol/client do NOT propagate to hand-written fakes — each fake's own signature must be widened. In `tests/integration/test_enricher.py` there are **five** such fakes (the audit found them at ~lines 19 `_StubLLM`, 259 `_RecordingLLM`, 348 `_PairRecordingLLM`, 455 `_RecordingLLM`, 500 `_GatingLLM` — verify by grepping `async def extract` in that file). For EACH one, widen its `extract` signature to accept and ignore the new kwargs:

```python
    async def extract(
        self, *, command: str, beacon_id: str | None, output: str,
        host: str | None = None, user: str | None = None,
    ) -> str:
        ...  # existing body unchanged — these fakes don't assert on grounding
```

(Do NOT touch `_StubLLM` in `tests/unit/enrichment/test_llm.py:~12` — it is called directly, never through the enricher, so it never receives `host`/`user` and stays conformant.) This widening lands in THIS task's commit (keep-green): the enricher change and the fakes must move together.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/unit/enrichment/test_llm.py tests/integration/test_enricher.py -v`
Expected: all pass — the 4 new tests AND every pre-existing enricher/llm test. The pre-existing tests pass ONLY because Step 5f widened the five driven fakes to accept `host`/`user`; without 5f, ~12 existing enricher tests would fail with an unexpected-keyword `TypeError` (audit C2). The `extract` defaults make the *production* path back-compatible; the *test fakes* needed the explicit widening.

- [ ] **Step 7: Run the full CI gate** (5 gates.)
Expected: green. mypy must stay clean (the protocol + enricher are in domain/application scope). Pytest 576 → 580 (+4).

- [ ] **Step 8: Commit**

```bash
git add redrobin/application/enrichment/llm.py redrobin/infrastructure/llm/anthropic_client.py redrobin/application/enrichment/enricher.py tests/unit/enrichment/test_llm.py tests/integration/test_enricher.py
git commit -m "feat(enrich): per-beacon dynamic grounding — inject own host/user into extraction (#105)"
```

---

## Task 4: Deterministic host-vs-username backstop in `apply_deltas` (#105 layer 3)

**Files:**
- Modify: `redrobin/infrastructure/persistence/working_memory_repo.py` (`apply_deltas` + helpers)
- Test: `tests/unit/test_working_memory_repo.py`

Even if the LLM ignores the grounding, drop an incoming host whose name matches a known username in live working memory. Deterministic, O(1) set-membership — the username set lives here, never in the prompt.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_working_memory_repo.py` (verified by the audit: it ALREADY imports `WorkingMemory, Host, Credential, Session, WorkingMemoryDeltas` and constructs `WorkingMemoryRepo(tmp_path / ...)` with the `tmp_path` fixture — no new imports needed):

```python
def _seed_wm(repo, *, sessions=(), credentials=()):
    wm = WorkingMemory(engagement_id="eng-test")
    wm.sessions = list(sessions)
    wm.credentials = list(credentials)
    repo.save(wm)


def test_apply_deltas_drops_host_matching_a_session_username(tmp_path) -> None:
    repo = WorkingMemoryRepo(tmp_path / "wm.json")
    _seed_wm(repo, sessions=[Session(beacon_id="B-1", hostname="JCSP", user="jcspi")])
    # The LLM mis-extracted the username "JCSPI" as a host AND a real host "DC01".
    deltas = WorkingMemoryDeltas(hosts=[Host(name="JCSPI"), Host(name="DC01")])
    repo.apply_deltas(deltas)
    names = {h.name.upper() for h in repo.load().hosts}
    assert "JCSPI" not in names  # dropped — it's a known username
    assert "DC01" in names       # kept — a real host


def test_apply_deltas_drops_host_matching_a_credential_username(tmp_path) -> None:
    repo = WorkingMemoryRepo(tmp_path / "wm.json")
    _seed_wm(repo, credentials=[Credential(domain="CORP", username="svc_sql", secret="x", secret_type="plaintext")])
    repo.apply_deltas(WorkingMemoryDeltas(hosts=[Host(name="svc_sql")]))
    assert repo.load().hosts == []


def test_apply_deltas_drops_host_matching_domain_qualified_username(tmp_path) -> None:
    repo = WorkingMemoryRepo(tmp_path / "wm.json")
    # session user is domain-qualified; the junk host is the bare name.
    _seed_wm(repo, sessions=[Session(beacon_id="B-1", hostname="WS1", user="CORP\\jdoe")])
    repo.apply_deltas(WorkingMemoryDeltas(hosts=[Host(name="jdoe")]))
    assert repo.load().hosts == []


def test_apply_deltas_drops_host_matching_a_username_in_same_batch(tmp_path) -> None:
    repo = WorkingMemoryRepo(tmp_path / "wm.json")
    _seed_wm(repo)  # empty WM
    # Same enrichment batch carries both a credential for "bob" and a junk host "bob".
    deltas = WorkingMemoryDeltas(
        hosts=[Host(name="bob")],
        credentials=[Credential(domain="", username="bob", secret="pw", secret_type="plaintext")],
    )
    repo.apply_deltas(deltas)
    assert repo.load().hosts == []
    assert len(repo.load().credentials) == 1  # the credential still lands


def test_apply_deltas_keeps_real_host_when_no_username_collision(tmp_path) -> None:
    repo = WorkingMemoryRepo(tmp_path / "wm.json")
    _seed_wm(repo, sessions=[Session(beacon_id="B-1", hostname="WS1", user="jdoe")])
    repo.apply_deltas(WorkingMemoryDeltas(hosts=[Host(name="DC01")]))
    names = {h.name.upper() for h in repo.load().hosts}
    assert "DC01" in names
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync pytest tests/unit/test_working_memory_repo.py -k "drops_host or keeps_real_host" -v`
Expected: the four "drops_host" tests FAIL (the junk host is currently merged); `keeps_real_host` passes.

- [ ] **Step 3: Implement the backstop**

In `redrobin/infrastructure/persistence/working_memory_repo.py`:

(a) Add a module logger (after the imports, before the class) if not already present:

```python
import logging

logger = logging.getLogger(__name__)
```

(b) Add a module-level `_bare_username` helper (near the logger):

```python
def _bare_username(name: str) -> str:
    """Bare, upper-cased username: strip a ``DOMAIN\\`` prefix and an ``@domain``
    suffix so ``CORP\\jdoe``, ``jdoe@corp.local`` and ``jdoe`` all compare equal."""
    return name.split("\\")[-1].split("@")[0].strip().upper()
```

(c) Add a `_known_usernames` static method on `WorkingMemoryRepo`:

```python
    @staticmethod
    def _known_usernames(wm: WorkingMemory, deltas: WorkingMemoryDeltas) -> set[str]:
        """Bare-upper usernames known in live WM + this incoming batch — used to
        reject a host whose name is actually a username (#105 backstop)."""
        users: set[str] = set()
        for s in wm.sessions:
            if s.user:
                users.add(_bare_username(s.user))
        for c in wm.credentials:
            if c.username:
                users.add(_bare_username(c.username))
        for c in deltas.credentials:
            if c.username:
                users.add(_bare_username(c.username))
        return users
```

(d) In `apply_deltas`, replace the host loop:

```python
        for host in deltas.hosts:
            self._merge_host(wm, host)
```

with:

```python
        known_users = self._known_usernames(wm, deltas)
        for host in deltas.hosts:
            # #105 backstop: a host whose name is actually a known username is
            # junk (the LLM mis-read the logged-in user as a host). Drop it.
            if host.name and _bare_username(host.name) in known_users:
                logger.info(
                    "apply_deltas: dropping junk host %r (matches a known username)",
                    host.name,
                )
                continue
            self._merge_host(wm, host)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync pytest tests/unit/test_working_memory_repo.py -v`
Expected: all pass (the 5 new + every pre-existing repo test — `_merge_host` behavior is unchanged for non-username hosts).

- [ ] **Step 5: Run the full CI gate** (5 gates.)
Expected: green. Pytest 580 → 585 (+5).

- [ ] **Step 6: Commit**

```bash
git add redrobin/infrastructure/persistence/working_memory_repo.py tests/unit/test_working_memory_repo.py
git commit -m "fix(enrich): drop hosts that are actually known usernames in apply_deltas (#105 backstop)"
```

---

## Task 5: Full CI gate + smoke runbook + handoff

**Files:** Create `docs/superpowers/runbooks/2026-05-28-dq-a-smoke.md`.

- [ ] **Step 1: Run every CI gate**

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy redrobin/domain redrobin/application
uv run --no-sync lint-imports --config importlinter.cfg
REDROBIN_ALLOW_DEGRADED=1 uv run --no-sync pytest tests/ -q
```
Expected: green. Pytest = 571 (post-D baseline) + 4 (T1) + 1 (T2) + 4 (T3) + 5 (T4) = **585**.

- [ ] **Step 2: Grep-prove no hardcoded victim username remains**

Run: `grep -rn "jcspi" redrobin/`
Expected: ZERO hits in `redrobin/` (the prompt example was the only one; the grounding + backstop derive everything at runtime).

- [ ] **Step 3: Write the smoke runbook**

Create `docs/superpowers/runbooks/2026-05-28-dq-a-smoke.md` with EXACTLY this content:

```markdown
# Data-Quality Group A Live-Beacon Smoke Runbook

**Acceptance gate** for Group A (#105 + #114) = these checks pass live, on the merged branch.

## Pre-conditions
- Branch: `wm/dq-a-extraction` (or after merge, `dev-juan`).
- `ANTHROPIC_API_KEY` set; NOT `REDROBIN_ALLOW_DEGRADED=1` (we need the real enricher).
- `manual-smoke` engagement with the real-machine scope.

## Setup

```powershell
# Stop the daemon (SQLite lock), then clean (errors surfaced).
redrobin daemon stop -ErrorAction SilentlyContinue
Get-Process redrobin -ErrorAction SilentlyContinue | Stop-Process -Force
$e = "$env:USERPROFILE\.redrobin\engagements\manual-smoke"
foreach ($f in @("working_memory.json","events.jsonl","episodic.db","dedup_keys.json")) {
    $p = "$e\$f"
    if (Test-Path $p) { try { Remove-Item $p -ErrorAction Stop; "deleted $f" } catch { "FAILED $f -> $($_.Exception.Message)" } }
    else { "skip $f" }
}
Get-ChildItem $e | Select-Object Name   # expect only engagement.yaml
.\.venv\Scripts\Activate.ps1
redrobin daemon start --host 127.0.0.1 --port 7777
# Reload redrobin.cna in CS Script Manager (re-seeds the session — Group D).
```

## Checks

| # | Action | Expected |
|---|---|---|
| 1 | `shell systeminfo` (or any recon that names the user, e.g. a `C:\Users\<you>\` path) | HOSTS shows the real host(s); the operator's own username does NOT appear as a host (#105 grounding + backstop) |
| 2 | `shell whoami` (a username with no secret) | CREDENTIALS does NOT gain a username-only row (#114) |
| 3 | `type creds.txt` (a real `user:secret`) | CREDENTIALS gains the real credential with its secret (the rule only drops secret-less entries) |
| 4 | Inspect the daemon log for an enrichment call | the per-beacon grounding line names the LIVE beacon's user/host, NOT `jcspi` |

Verify with `python $env:USERPROFILE\.redrobin\inspect_layers.py` after each step.
```

- [ ] **Step 4: Commit the runbook**

```bash
git add docs/superpowers/runbooks/2026-05-28-dq-a-smoke.md
git commit -m "docs(dq-a): smoke runbook for the extraction-validation acceptance gate"
```

- [ ] **Step 5: Dispatch the final whole-branch review** (subagent-driven-development handles this) and address every finding.

- [ ] **Step 6: Hand the runbook to the operator.** Do NOT auto-merge. After the operator's live smoke is green, merge `wm/dq-a-extraction` → `dev-juan` (PR, same pattern; CI fires on the `dev-*` push post-merge). Then Group C branches off the updated `dev-juan`.

---

## Self-Review

**1. Spec coverage (§6 Group A):**
- §6.1 #114 (drop cred with no secret/ntlm) → T1. ✓
- §6.2 layer 1 (de-personalize prompt, remove `'jcspi'`) → T2. ✓
- §6.2 layer 2 (dynamic per-beacon grounding from sessions) → T3 (the `bid→(host,user)` cache mirrors the existing command cache; the protocol + client + enricher all threaded). ✓
- §6.2 layer 3 (deterministic exact-match-username backstop in apply_deltas, NOT in the prompt) → T4. ✓
- §6 scalability (grounding small/in-prompt; username set deterministic/out-of-prompt; no file-read-per-call — the cache is in-memory from the bus) → T3 cache + T4 set. ✓
- §3 no-hardcoded-victim-data → T2 removes `'jcspi'`; T5 Step 2 grep-proves zero hits. ✓
- §11 acceptance (username not a host; username-only not a cred; real cred lands; prompt carries live user/host) → T5 runbook. ✓

**2. Placeholder scan:** No TBD/TODO. Every code step has complete before/after code; every test step has full test bodies; every command has expected output.

**3. Type consistency:**
- `EnricherLLM.extract(*, command, beacon_id, output, host=None, user=None) -> str` — defined in T3 Step 3, matched by `AnthropicEnricherClient.extract` (T3 Step 4), `_extract_chunk`'s call (T3 Step 5e), and `_GroundingRecordingLLM.extract` in the test (T3 Step 1). Consistent.
- `_build_enricher_user_message(*, command, beacon_id, output, host=None, user=None) -> str` — defined T3 Step 4, used by `extract` (same step) and tested T3 Step 1. Consistent.
- `_host_user_by_bid: dict[str, tuple[str | None, str | None]]` — defined T3 Step 5a, written by `_remember_session` (5c), read in `_enrich` (5d). Consistent.
- `_bare_username(name) -> str` + `_known_usernames(wm, deltas) -> set[str]` — defined T4 Step 3, used in the apply_deltas host loop (same step). Consistent.

**4. Keep-green-between-tasks:** T1 (domain rule + tests, isolated). T2 (prompt string + test, isolated). T3 (grounding — protocol + client + enricher + tests all in one commit; existing callers unaffected because `host`/`user` default to `None`). T4 (backstop + tests, isolated; `_merge_host` unchanged for non-username hosts). T5 (verification + runbook, no behavior change). Suite green after each.
