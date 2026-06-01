# B3 — `.cna` Event Dedup + Auto-obs ROOT (design)

> Companion fix to B2: kill the producer-side amplifier (#152 re-emission) and the producer-side self-ingestion (auto-obs ROOT) that B1.5 T2's enricher-side `strip_self_annotations` patched downstream.

## 1. Problem

### 1.1 #152 — `.cna` re-emission amplifier

The CS Aggressor `.cna` re-emits ALL backlogged beacon events to the daemon on every CS-client reconnect or script reload. Evidence from the live B2 smoke (2026-05-27):

- One `shell dir /s C:\` produced **536 duplicate `beacon.command_output` events** (correlation_id `bc74dbf5...` × 536).
- `events.jsonl` inflated to **254 MB** for a single engagement.
- Downstream cost: ~16,000 unnecessary Haiku calls (chunker ≈ 30 chunks × 536 dups) per such command.
- Episodic + promoter occurrence counts skewed by duplicates.

B2 killed the kerberos-FP symptom (regex retired) and B1.5 `apply_deltas` dedups WM merges; neither addresses the **producer-side flood**.

### 1.2 Auto-obs ROOT — self-ingestion of Red Robin's own `blog`ged lines

When the daemon issues a verdict (e.g., mimikatz BLOCK), the `.cna` calls `blog($bid, "[Red Robin] Blocked: ...")` to surface the reasoning in the beacon console. CS treats that `blog`ed output as beacon output → fires the `BEACON_OUTPUT` hook → `.cna` POSTs Red Robin's own annotation back to the daemon as `beacon.command_output`.

B1.5 T2 (`strip_self_annotations`) drops `[Red Robin]`-prefixed lines enricher-side, but the round-trip + ingest + bus publish + enricher chunking-attempt still happen for every self-blog. The root fix is to stop the emission at the producer.

## 2. Goals / non-goals

### Goals
- Daemon processes each producer event **exactly once**, regardless of CS reconnects or script reloads.
- The `.cna` does NOT POST its own `[Red Robin]`-prefixed lines back to the daemon (display in the beacon console is preserved).
- Dedup state survives daemon restarts **within an engagement**.
- No regression of B1.5/B2: existing enricher path + `apply_deltas` + opsec verdict flow + B1.5 T2 stay intact.

### Non-goals
- Doctor enrichment counters (B2.1, task #108 — separate plan).
- Producer-side cursor (`.cna` persists "what it already sent") — explicit non-goal: extra complexity in Sleep, marginal benefit on localhost (daemon dedup achieves the cost goal).
- Schema changes — none. Reuses `correlation_id` (B1.5 T9 added it to both `beacon.command_input` and `beacon.command_output`).
- TUI visibility of dedup hits — debug-log only for v1.
- Multi-tenant / SaaS hardening (true UUID keys) — n/a for local single-tenant.

## 3. Industry alignment

This is a textbook **Idempotent Receiver** (Hohpe & Woolf, [Enterprise Integration Patterns](https://www.enterpriseintegrationpatterns.com/patterns/messaging/IdempotentReceiver.html)) — also called Idempotent Consumer (microservices.io) or Idempotent Reader (Confluent). The producer attaches a per-logical-event key (`correlation_id` from Aggressor's `$5`); the receiver dedups before applying side effects.

Specific decisions we mirror (cited in §9):

| Decision | Industry precedent |
|---|---|
| Producer-attached key | Stripe `Idempotency-Key`, AWS SQS `MessageDeduplicationId`, Kafka producer-id+seq, GitHub `X-GitHub-Delivery` |
| Dedup BEFORE side effects | Hohpe; Stripe ("save results only after execution begins"); microservices.io |
| 200-ack + silent drop on duplicate | Stripe re-returns original 200; SQS silently accepts |
| Composite key with `event_type` | Stripe ("use the ID… along with `event.type`") |
| Persistent dedup state | SQS, Kafka, Stripe all persist; Kafka explicitly caveats in-session-only loses idempotence on restart |
| Retention bounded by producer retry window | Ours = engagement-lifetime (matches the practical reconnect span for one CS engagement) |

## 4. Design

### 4.1 Piece 1 — #152 daemon-side dedup (Option A')

**Where:** `redrobin/api/events_router.py` — in the `ingest` / `replay_event` handlers, BEFORE append-to-`events.jsonl` and BEFORE bus publish.

**Dedup key (per event type):**

| Event type | Key tuple | Notes |
|---|---|---|
| `beacon.command_input` | `(beacon_id, correlation_id, event_type)` | `correlation_id` from B1.5 T9 |
| `beacon.command_output` | `(beacon_id, correlation_id, event_type)` | same |
| `opsec.verdict` | `(beacon_id, correlation_id, event_type)` | forwards the same `$5` |
| `beacon.initial` | `(beacon_id, event_type)` | no `correlation_id` — exactly 1 per beacon by definition |
| Any other type without `correlation_id` | (pass-through, no dedup) | audit-only, low volume |

> **SUPERSEDED (2026-05-28, data-quality Group D / #107):** `beacon.initial` is now **pass-through (no dedup)** — see `docs/superpowers/specs/2026-05-28-data-quality-design.md` §5. It is idempotent at the reducer (`upsert_session` replaces by `bid`), so deduping it gained nothing and dropped the `.cna` backfill's re-emit that re-seeds sessions after a working-memory clean. The `beacon.initial` row above and the §4.1 / §7 mentions of it being deduped are historical. Command-event dedup is unchanged.

**Composite-key rationale:** `correlation_id` is 16-hex-chars = 64 bits of entropy from Aggressor's `$5`. `beacon_id` adds a per-beacon namespace. `event_type` separates input from output for the same command. Collision probability ≈ 0 at our scale (thousands of events per engagement, not billions). For local single-tenant; if v2 goes multi-tenant SaaS, upgrade to true UUID.

**Storage:**
- Per-engagement file `~/.redrobin/engagements/<slug>/dedup_keys.json`.
- Format: `{"keys": ["<bid>|<corr>|<type>", ...]}` (JSON list of pipe-delimited strings — sets aren't JSON-serializable; reconstruct as `set[str]` in memory).
- Loaded into memory at daemon boot (a new `start_dedup` lifespan helper).
- Atomic write: the proven `temp+replace` pattern from `WorkingMemoryRepo.save` (2026-05-26 lesson: serialize to `.tmp`, `os.replace()`, retry `PermissionError` on Windows).

**Retention — engagement-lifetime:**
- Keys persist while the engagement is open.
- Cleared on `engagement.archive` / `engagement.delete` / `engagement.new` (fresh slug starts empty).
- Storage is trivial — ~50 B per key × thousands of commands = KBs.

**Concurrency:**
- `asyncio.Lock` around the `check + insert + persist` block in `ingest`.
- FastAPI runs a single event loop today — the lock is defensive against any future thread-pool offload.

**Persistence frequency:**
- In-memory set updated immediately on accept.
- Disk flushed after each accept (atomic write).
- Per-event disk I/O is acceptable — the file is small (KBs), atomic-write is fast. A batched periodic-sync optimization is documented as a future-only path; not built.

**Audit:**
- `logger.debug("dedup hit: bid=%s corr=%s type=%s")` on every duplicate.
- No bus event for hits in v1 — keeps the bus stream clean. TUI visibility deferred.

**Side-effect ordering** (canonical Idempotent Receiver):

```python
async def ingest(envelope):
    key = derive_key(envelope.event)
    if key is None:  # event type without dedup key (rare, low-volume)
        return await _accept(envelope)

    async with self._lock:
        if key in self._seen:
            logger.debug("dedup hit: %s", key)
            return {"status": "duplicate"}, 200
        self._seen.add(key)
        self._repo.save(self._seen)  # atomic temp+replace

    # only past the gate → real side effects
    await _accept(envelope)
    return {"status": "accepted"}, 202

async def _accept(envelope):
    _append_to_jsonl(envelope)
    await self._bus.publish(envelope.event_to_bus())
```

**Failure modes:**

| Failure | Behavior |
|---|---|
| Daemon crash mid-persist | `temp+replace` is atomic → old or new file, never partial. Worst case: lose the last 1–2 keys, next reconnect re-ingests them. Negligible. |
| Disk full | Persistence raises; ingest still appends in-memory key but logs ERROR. Re-ingests on next reboot. Acceptable degradation. |
| Key collision | 64-bit `correlation_id` + bid namespace + type → effectively impossible at our scale. |
| Engagement re-opened with stale dedup file | On `engagement.new` we clear; on `engagement.use` of an existing engagement we reload the file as-is. |

### 4.2 Piece 2 — Auto-obs ROOT (Option b: sentinel content-check)

**Where:** `adapters/cobaltstrike/redrobin.cna` — the `BEACON_OUTPUT` filter hook (last modified by B1.5 T9 to forward `$5`).

**Change:** add a sentinel check at the top of the hook. If `$2` starts with `[Red Robin]`, return `$2` for **display** (operator still sees it in the beacon console) but skip the call to `redrobin_emit_command_output(...)` (no POST to daemon).

**Sketch (delta against the post-B1.5-T9 hook):**

```sleep
set BEACON_OUTPUT {
    local('$bid $out $correlation_id');
    $bid = $1;
    $out = $2;
    $correlation_id = iff($5 !is $null, $5, "");

    # B3 auto-obs ROOT: do not re-emit Red Robin's own blogged lines.
    # Display is preserved (return $out unchanged); only emission is suppressed.
    if ($out isnot $null && [$out startsWith "[Red Robin]"]) {
        return $out;
    }

    redrobin_emit_command_output($bid, $out, $correlation_id);
    return $out;
}
```

**Why content-check (not flag-based):**
- Aggressor hooks fire asynchronously; a `set $flag = 1` → `blog` → `set $flag = 0` pattern races against the `BEACON_OUTPUT` hook firing for unrelated output, producing unreliable filtering.
- Content-based detection is deterministic.
- The sentinel `[Red Robin]` is a stable prefix already used by all daemon-issued `blog` calls (B1.5 enforced this consistency).

**Belt+suspenders:** B1.5 T2 `strip_self_annotations` (enricher-side) **stays as a safety net**. If a daemon-side blog ever lacks the prefix (regression), the enricher-side filter still drops it before the LLM call. Same defense-in-depth symmetry as #152 (producer-attached key + receiver dedup).

**Edge case:** real beacon output legitimately starting with `[Red Robin]` would be false-filtered. Probability is negligible (no normal command output begins with that string). If ever encountered, change the sentinel to something more unique (e.g., a non-printable prefix byte).

## 5. Files affected

| File | Change |
|---|---|
| `redrobin/api/events_router.py` | Add dedup gate in `ingest`/`replay_event` (before append + bus publish); receive a `DedupKeys` from app state |
| `redrobin/infrastructure/persistence/dedup_keys_repo.py` | NEW — JSON-backed set, atomic temp+replace, per-engagement file |
| `redrobin/application/dedup_keys.py` | NEW — `DedupKeys` (set + asyncio.Lock + persistence delegate); `derive_key()` helper |
| `redrobin/api/lifespan_helpers.py` | `start_dedup` — load keys at boot, attach to `app.state.dedup_keys` |
| `redrobin/application/engagement_service.py` | Clear dedup file on `archive` / `delete` / `new` |
| `adapters/cobaltstrike/redrobin.cna` | Add `[Red Robin]` sentinel check in `BEACON_OUTPUT` hook |
| `tests/unit/infrastructure/test_dedup_keys_repo.py` | NEW — round-trip, atomic write, corrupt-file recovery |
| `tests/unit/application/test_dedup_keys.py` | NEW — `derive_key()` per event type; lock semantics |
| `tests/integration/test_events_router_dedup.py` | NEW — POST same envelope twice → second 200 + dropped; daemon-restart preserves state |
| `tests/integration/test_dedup_engagement_lifecycle.py` | NEW — engagement archive clears dedup keys |

The `.cna` change is smoke-only (project convention: no CI for Aggressor).

## 6. Test plan

### Unit (deterministic, no I/O)
- `DedupKeysRepo` round-trip: save → load → equal set.
- `DedupKeysRepo` atomic-write contract: simulated crash mid-write leaves either old or new, never partial; stale `.tmp` is overwritten on next save.
- `derive_key(beacon.command_output)` → `(bid, corr_id, "beacon.command_output")`.
- `derive_key(beacon.initial)` → `(bid, "beacon.initial")`.
- `derive_key(event_without_corr_id)` → `None` (pass-through).
- `DedupKeys.check_and_insert` is atomic under concurrent coroutines (both call simultaneously → exactly one returns "inserted").

### Integration (FastAPI TestClient + EventBus)
- POST envelope to `/v1/events/replay` twice → first 202 + appended to jsonl + published; second 200 + NOT appended + NOT published.
- POST a `beacon.command_output` → close `lifespan_context` → reopen with the same root → re-POST the same envelope → still 200 + dropped (persistence works).
- POST → engagement.archive → POST same envelope → 202 + accepted (fresh engagement context).
- Two coroutines POST the same envelope concurrently → exactly one ingested (lock works).
- POST `beacon.initial` twice for same `bid` → second dropped (the `(bid, type)` key handles no-`correlation_id` events).

### Smoke (live-beacon — the real acceptance gate)
The operator's runbook on the merged branch (post-B3):
1. Clean `manual-smoke` (same PowerShell block as B2's smoke).
2. Restart daemon with `ANTHROPIC_API_KEY` set (NOT degraded).
3. Reload `redrobin.cna` in CS Script Manager.
4. Run `shell systeminfo` once. Inspect `events.jsonl` → exactly **1** `beacon.command_output` for it.
5. Force a CS-client reconnect (close + reopen CS client). Inspect → still **1**.
6. Reload `.cna` in Script Manager. Inspect → still **1**.
7. Run `mimikatz sekurlsa::logonpasswords` → Layer A BLOCK. Verify in the beacon console: `[Red Robin] Blocked: mimikatz — ...` IS displayed. Inspect `events.jsonl`: NO `beacon.command_output` entry whose `output` starts with `[Red Robin]`.
8. Restart the daemon. Reconnect CS. Re-run step 4-7's verifications — state must survive.

## 7. Acceptance criteria

- ✅ The daemon ingests **exactly one** `beacon.command_output` per real command, regardless of CS reconnects or script reloads.
- ✅ The daemon ingests **zero** `[Red Robin]`-prefixed `beacon.command_output` events (operator still sees them in the beacon console).
- ✅ Dedup state survives a daemon restart within the same engagement.
- ✅ Dedup state is cleared on engagement `archive` / `delete` / `new`.
- ✅ All 5 CI gates green: `ruff check .` / `ruff format --check .` / `mypy redrobin/domain redrobin/application` / `lint-imports --config importlinter.cfg` / `pytest tests/`.
- ✅ No regression of B1.5/B2: cred-recovery, denylist, T7 grounding, enricher dedup, regex-retired ignored-test, opsec verdict flow, B1.5 T2 enricher-side `strip_self_annotations` all still pass.

## 8. Risks + mitigations

| Risk | Mitigation |
|---|---|
| Aggressor `$5` semantics differ from our empirical reading | The B1.5 debug spike (commit `243d50e`) verified `$5` is per-command-unique + survives rapid-fire. Re-run that spike in the smoke if doubts arise. |
| Per-event disk I/O too slow | File is KBs; atomic-write is fast. If empirically slow under load, swap to a batched periodic-sync task (documented as a future, not built). |
| Sentinel false-positive (real output begins with `[Red Robin]`) | Probability negligible; B1.5 T2 enricher-side filter is a safety net; sentinel can be made more unique if ever observed. |
| Persistence file corruption | Repo loads with `try/except`; on corruption, log ERROR, start with empty set, accept the temporary regression (one engagement of re-ingestion). |
| Daemon and `.cna` versions mismatch (operator runs old `.cna` against new daemon, or vice versa) | The daemon dedup works regardless of `.cna` version (uses existing `correlation_id`). The `.cna` change is independent. Forward-compat. |

## 9. References

- [Idempotent Receiver — Hohpe & Woolf](https://www.enterpriseintegrationpatterns.com/patterns/messaging/IdempotentReceiver.html)
- [Stripe — Idempotent Requests](https://docs.stripe.com/api/idempotent_requests)
- [Stripe — Webhooks (Handle duplicate events)](https://docs.stripe.com/webhooks)
- [AWS SQS — Exactly-once processing (FIFO)](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/FIFO-queues-exactly-once-processing.html)
- [AWS SQS — Using the MessageDeduplicationId property](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/using-messagededuplicationid-property.html)
- [Apache Kafka — Idempotent Producer (4.0 javadoc)](https://kafka.apache.org/40/javadoc/org/apache/kafka/clients/producer/KafkaProducer.html)
- [GitHub — Webhook events & payloads](https://docs.github.com/en/webhooks/webhook-events-and-payloads)
- [microservices.io — Idempotent Consumer](https://microservices.io/patterns/communication-style/idempotent-consumer.html)
- [Confluent — Idempotent Reader](https://developer.confluent.io/patterns/event-processing/idempotent-reader/)
