# WM AI Enrichment — Plan A: Deltas Pathway (deterministic) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic data pathway that lets Working Memory ingest validated structured "deltas" (hosts/credentials/findings/tools/defenses) and merge them single-writer — fully testable with synthetic deltas, no LLM. This is the substrate the Plan B Enricher will produce deltas into.

**Architecture:** A new `WorkingMemoryDeltas` domain value object (with whitelisting `from_payload` / `to_payload`) is the contract. `WorkingMemoryRepo.apply_deltas` merges a deltas batch in one atomic load→merge→save (dedup by natural key, non-null overwrite, IP set-union, append-only findings). The `WorkingMemoryReducer` gains a `working_memory.deltas` event handler that calls `apply_deltas` — wired into its existing single-writer loop. The existing regex extraction in the reducer is **left untouched** (Plan B replaces it), so there is never a window where the WM stops being populated.

**Tech Stack:** Python 3.12, dataclasses, `asyncio` event bus, JSON-file repo (atomic temp+replace), pytest (`@pytest.mark.asyncio`), `uv run`.

**Spec:** `docs/superpowers/specs/2026-05-26-working-memory-ai-enrichment-design.md` (§5 contract, §6 merge + schema).

**Branch:** `wm/ai-enrichment` (already created off `dev-juan`).

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `redrobin/domain/memory.py` | Memory schemas | **Modify** — extend `Host` (+domain/ip_addresses/os_version), generalize `Credential` (+secret_type/secret), add `WorkingMemoryDeltas` value object |
| `redrobin/infrastructure/persistence/working_memory_repo.py` | JSON-backed WM read/write | **Modify** — add `apply_deltas` (batch merge, single load→save). Existing `upsert_*` untouched. |
| `redrobin/application/working_memory_reducer.py` | Bus→WM reducer (single writer) | **Modify** — add `working_memory.deltas` to `_dispatch` + `_on_enrichment_deltas`. Regex path untouched. |
| `tests/unit/test_working_memory_repo.py` | Repo unit tests | **Modify** — schema round-trip + back-compat + `apply_deltas` merge tests |
| `tests/unit/test_memory_deltas.py` | `WorkingMemoryDeltas` unit tests | **Create** — from_payload whitelisting / provenance / round-trip |
| `tests/integration/test_working_memory_reducer.py` | Reducer integration tests | **Modify** — `working_memory.deltas` event → WM merge + snapshot re-emit |

**Out of scope for Plan A (Plan B):** `EnrichmentTrigger`, chunker, `DeltaParser` (JSON-string parsing), `WorkingMemoryEnricher`, the Haiku LLM client, lifespan wiring of the enricher, observability events, and **deleting** the regex + `upsert_host`/`upsert_credential`. Plan A only builds + wires the *receiver*.

---

### Task 1: Extend `Host` schema (domain + NIC IPs + OS version)

**Why:** #149 cannot be fixed without fields to hold the domain / NIC IPs / OS version that `systeminfo` yields. Additive with defaults → old `working_memory.json` still loads (`_from_dict` builds `Host(**h)`; missing keys fall back to defaults).

**Files:**
- Modify: `redrobin/domain/memory.py` (the `Host` dataclass, currently lines 24-30)
- Test: `tests/unit/test_working_memory_repo.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_working_memory_repo.py`:

```python
def test_host_new_fields_round_trip(tmp_path):
    """Host gains domain / ip_addresses / os_version; they survive save+load."""
    from redrobin.domain.memory import Host, WorkingMemory
    from redrobin.infrastructure.persistence.working_memory_repo import WorkingMemoryRepo

    repo = WorkingMemoryRepo(tmp_path / "wm.json")
    wm = WorkingMemory(engagement_id="eng-host-fields")
    wm.hosts.append(
        Host(
            name="WS01",
            os="Windows 10 Pro",
            os_version="10.0.19045",
            domain="ACME",
            ip_addresses=["10.0.0.5", "10.0.0.6"],
        )
    )
    repo.save(wm)

    loaded = repo.load().hosts[0]
    assert loaded.domain == "ACME"
    assert loaded.os_version == "10.0.19045"
    assert loaded.ip_addresses == ["10.0.0.5", "10.0.0.6"]


def test_host_back_compat_old_json_without_new_fields(tmp_path):
    """A pre-existing wm.json whose host lacks the new keys loads with defaults."""
    import json

    from redrobin.infrastructure.persistence.working_memory_repo import WorkingMemoryRepo

    path = tmp_path / "wm.json"
    path.write_text(
        json.dumps(
            {
                "engagement_id": "eng-old",
                "hosts": [{"name": "DC01", "os": "Windows Server 2019"}],
            }
        )
    )
    host = WorkingMemoryRepo(path).load().hosts[0]
    assert host.name == "DC01"
    assert host.domain is None
    assert host.ip_addresses == []
    assert host.os_version is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_working_memory_repo.py::test_host_new_fields_round_trip -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'domain'`

- [ ] **Step 3: Add the fields to `Host`**

In `redrobin/domain/memory.py`, replace the `Host` dataclass with:

```python
@dataclass
class Host:
    name: str
    os: str | None = None
    integrity_level: str | None = None
    edr_detected: str | None = None
    access_level: str | None = None  # e.g., "user-token", "SYSTEM"
    domain: str | None = None  # AD domain (systeminfo "Domain:") — drives AD reasoning
    ip_addresses: list[str] = field(default_factory=list)  # NIC IPs — lateral movement
    os_version: str | None = None  # build/patch level — exploit selection
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_working_memory_repo.py::test_host_new_fields_round_trip tests/unit/test_working_memory_repo.py::test_host_back_compat_old_json_without_new_fields -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add redrobin/domain/memory.py tests/unit/test_working_memory_repo.py
git commit -m "feat(memory): extend Host with domain, ip_addresses, os_version"
```

---

### Task 2: Generalize `Credential` beyond NTLM

**Why:** `mimikatz` yields plaintext passwords, AES keys, and Kerberos tickets — not only NTLM. Capturing only `ntlm_hash` silently drops the prize. `secret_type` is an **open `str`** (not an Enum) so cloud/web material needs no schema change. `ntlm_hash` is retained for back-compat with existing `wm.json`.

**Files:**
- Modify: `redrobin/domain/memory.py` (the `Credential` dataclass, currently lines 49-62)
- Test: `tests/unit/test_working_memory_repo.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_working_memory_repo.py`:

```python
def test_credential_generalized_round_trip(tmp_path):
    """Credential gains secret_type + secret; plaintext is captured, not dropped."""
    from redrobin.domain.memory import Credential, WorkingMemory
    from redrobin.infrastructure.persistence.working_memory_repo import WorkingMemoryRepo

    repo = WorkingMemoryRepo(tmp_path / "wm.json")
    wm = WorkingMemory(engagement_id="eng-cred")
    wm.credentials.append(
        Credential(
            domain="ACME",
            username="jdoe",
            secret_type="plaintext",
            secret="Summer2026!",
            source_beacon_id="B-1",
        )
    )
    repo.save(wm)

    cred = repo.load().credentials[0]
    assert cred.secret_type == "plaintext"
    assert cred.secret == "Summer2026!"
    assert cred.source_beacon_id == "B-1"


def test_credential_back_compat_old_json_defaults_to_ntlm(tmp_path):
    """A pre-existing credential dict (ntlm_hash only) loads with secret_type='ntlm'."""
    import json

    from redrobin.infrastructure.persistence.working_memory_repo import WorkingMemoryRepo

    path = tmp_path / "wm.json"
    path.write_text(
        json.dumps(
            {
                "engagement_id": "eng-old",
                "credentials": [
                    {"domain": "CORP", "username": "admin", "ntlm_hash": "a" * 32 + ":" + "b" * 32}
                ],
            }
        )
    )
    cred = WorkingMemoryRepo(path).load().credentials[0]
    assert cred.secret_type == "ntlm"
    assert cred.secret is None
    assert cred.ntlm_hash == "a" * 32 + ":" + "b" * 32
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_working_memory_repo.py::test_credential_generalized_round_trip -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'secret_type'`

- [ ] **Step 3: Generalize `Credential`**

In `redrobin/domain/memory.py`, replace the `Credential` dataclass body fields with (keep the docstring above it):

```python
@dataclass
class Credential:
    """Credential observed on the wire (parsed from beacon output).

    Distinct from ``Account``: ``Account`` is operator-curated and may include
    plaintext/ticket material captured by hand; ``Credential`` is reducer-derived
    from event parsing and always carries a ``source_beacon_id`` so the operator
    can trace provenance.

    ``secret_type`` is an OPEN vocabulary (not an Enum) so non-AD material
    (aws_key, azure_token, ...) needs no schema change. ``ntlm_hash`` is retained
    for back-compat with working_memory.json written before this generalization.
    """

    domain: str
    username: str
    ntlm_hash: str | None = None
    secret_type: str = "ntlm"  # ntlm | plaintext | aes256 | dcc2 | kerberos_ticket | aws_key | ...
    secret: str | None = None  # the captured material (hash/password/key/ticket ref)
    source_beacon_id: str | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_working_memory_repo.py::test_credential_generalized_round_trip tests/unit/test_working_memory_repo.py::test_credential_back_compat_old_json_defaults_to_ntlm -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add redrobin/domain/memory.py tests/unit/test_working_memory_repo.py
git commit -m "feat(memory): generalize Credential with secret_type + secret (keep ntlm_hash)"
```

---

### Task 3: `WorkingMemoryDeltas` value object (the contract)

**Why:** The contract the Enricher produces and the reducer consumes. Lives in `domain` so both `application` (reducer/enricher) and `infrastructure` (repo) can import it without crossing layers. `from_payload` **whitelists** keys (unknown keys ignored → anti-hallucination + crash-safe) and **system-attaches** provenance (`source_beacon_id`) so the LLM cannot fabricate it.

**Files:**
- Modify: `redrobin/domain/memory.py` (add the class + a small helper near the bottom, before `working_memory_to_snapshot`)
- Test: `tests/unit/test_memory_deltas.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_memory_deltas.py`:

```python
"""Unit tests for the WorkingMemoryDeltas contract (whitelisting + provenance)."""

from __future__ import annotations

from redrobin.domain.memory import WorkingMemoryDeltas


def test_from_payload_builds_typed_objects_and_attaches_provenance():
    deltas = WorkingMemoryDeltas.from_payload(
        {
            "hosts": [
                {
                    "name": "ws01",
                    "os": "Windows 10",
                    "os_version": "10.0.19045",
                    "domain": "ACME",
                    "ip_addresses": ["10.0.0.5"],
                }
            ],
            "credentials": [
                {"domain": "ACME", "username": "jdoe", "secret_type": "plaintext", "secret": "pw"}
            ],
            "findings": [{"kind": "gpp_password", "summary": "Groups.xml cpassword"}],
            "defenses_observed": ["Defender"],
        },
        source_beacon_id="B-7",
    )
    assert deltas.hosts[0].domain == "ACME"
    assert deltas.hosts[0].ip_addresses == ["10.0.0.5"]
    # Provenance is system-supplied, never from the LLM payload.
    assert deltas.credentials[0].source_beacon_id == "B-7"
    assert deltas.findings[0].source_beacon_id == "B-7"
    assert deltas.defenses_observed == ["Defender"]


def test_from_payload_ignores_unknown_keys_and_bad_rows():
    deltas = WorkingMemoryDeltas.from_payload(
        {
            "hosts": [
                {"name": "ws01", "evil_field": "DROP TABLE", "os": "Win"},  # extra key ignored
                {"os": "no name"},  # no name -> skipped
            ],
            "credentials": [{"domain": "ACME"}],  # no username -> skipped
            "junk_top_level_key": 123,  # ignored
        },
        source_beacon_id=None,
    )
    assert len(deltas.hosts) == 1
    assert deltas.hosts[0].name == "ws01"
    assert not hasattr(deltas.hosts[0], "evil_field")
    assert deltas.credentials == []


def test_from_payload_handles_non_dict_and_empty():
    assert WorkingMemoryDeltas.from_payload(None, source_beacon_id=None).hosts == []
    assert WorkingMemoryDeltas.from_payload({}, source_beacon_id=None).credentials == []


def test_round_trip_to_payload_from_payload_is_stable():
    src = WorkingMemoryDeltas.from_payload(
        {"hosts": [{"name": "WS01", "domain": "ACME", "ip_addresses": ["10.0.0.5"]}]},
        source_beacon_id="B-1",
    )
    again = WorkingMemoryDeltas.from_payload(src.to_payload(), source_beacon_id="B-1")
    assert again.hosts[0].name == "WS01"
    assert again.hosts[0].ip_addresses == ["10.0.0.5"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_memory_deltas.py -v`
Expected: FAIL with `ImportError: cannot import name 'WorkingMemoryDeltas' from 'redrobin.domain.memory'`

- [ ] **Step 3: Add `WorkingMemoryDeltas` to `domain/memory.py`**

In `redrobin/domain/memory.py`, add these helpers + class immediately **before** the `working_memory_to_snapshot` function (the `datetime` import already exists at the top of the file):

```python
def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x) for x in value if isinstance(x, (str, int, float))]


@dataclass
class WorkingMemoryDeltas:
    """A batch of facts extracted from one beacon command output.

    Produced by the enrichment path (Plan B) and merged into WorkingMemory by
    the reducer via :meth:`WorkingMemoryRepo.apply_deltas`. ``from_payload``
    whitelists keys (unknown keys are dropped — the LLM cannot inject fields)
    and system-attaches ``source_beacon_id`` to credentials/findings so
    provenance is never LLM-supplied. Only output-derived fact types appear
    here; objectives/phase/kill-chain (interpretation) and sessions/scope
    (other event sources) are intentionally absent.
    """

    hosts: list[Host] = field(default_factory=list)
    credentials: list[Credential] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    tools_used: list[ToolUsage] = field(default_factory=list)
    defenses_observed: list[str] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: Any, *, source_beacon_id: str | None) -> WorkingMemoryDeltas:
        if not isinstance(payload, dict):
            return cls()
        hosts: list[Host] = []
        for h in payload.get("hosts") or []:
            if not isinstance(h, dict) or not h.get("name"):
                continue
            hosts.append(
                Host(
                    name=str(h["name"]),
                    os=_opt_str(h.get("os")),
                    integrity_level=_opt_str(h.get("integrity_level")),
                    edr_detected=_opt_str(h.get("edr_detected")),
                    access_level=_opt_str(h.get("access_level")),
                    domain=_opt_str(h.get("domain")),
                    ip_addresses=_str_list(h.get("ip_addresses")),
                    os_version=_opt_str(h.get("os_version")),
                )
            )
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
        findings: list[Finding] = []
        for f in payload.get("findings") or []:
            if not isinstance(f, dict) or not f.get("kind"):
                continue
            findings.append(
                Finding(
                    kind=str(f["kind"]),
                    summary=str(f.get("summary", "")),
                    source_beacon_id=source_beacon_id,
                )
            )
        tools_used: list[ToolUsage] = []
        now_iso = datetime.now().isoformat()
        for t in payload.get("tools_used") or []:
            if not isinstance(t, dict) or not t.get("name"):
                continue
            tools_used.append(
                ToolUsage(
                    name=str(t["name"]),
                    used_on=str(t.get("used_on", "")),
                    used_at=str(t.get("used_at") or now_iso),
                    detection_observed=str(t.get("detection_observed", "none")),
                )
            )
        return cls(
            hosts=hosts,
            credentials=credentials,
            findings=findings,
            tools_used=tools_used,
            defenses_observed=_str_list(payload.get("defenses_observed")),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "hosts": [asdict(h) for h in self.hosts],
            "credentials": [asdict(c) for c in self.credentials],
            "findings": [asdict(f) for f in self.findings],
            "tools_used": [asdict(t) for t in self.tools_used],
            "defenses_observed": list(self.defenses_observed),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_memory_deltas.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add redrobin/domain/memory.py tests/unit/test_memory_deltas.py
git commit -m "feat(memory): add WorkingMemoryDeltas contract (whitelisting + provenance)"
```

---

### Task 4: `WorkingMemoryRepo.apply_deltas` (single-writer batch merge)

**Why:** One atomic `load → merge whole batch → save` (vs N reloads). Merge rules: host dedup by upper-name with non-null scalar overwrite + IP **set-union**; credential dedup by `(domain, username, secret_type)`; finding **append** (audit stream); defenses **set-union**; tool dedup by `(name, used_on)`. Reuses the merge semantics already proven in `upsert_host`. (`upsert_host`/`upsert_credential` stay for now — Plan B deletes them once they are orphaned.)

**Files:**
- Modify: `redrobin/infrastructure/persistence/working_memory_repo.py`
- Test: `tests/unit/test_working_memory_repo.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_working_memory_repo.py`:

```python
def test_apply_deltas_merges_host_nonnull_overwrite_and_ip_union(tmp_path):
    from redrobin.domain.memory import Host, WorkingMemory, WorkingMemoryDeltas
    from redrobin.infrastructure.persistence.working_memory_repo import WorkingMemoryRepo

    repo = WorkingMemoryRepo(tmp_path / "wm.json")
    wm = WorkingMemory(engagement_id="eng")
    wm.hosts.append(Host(name="WS01", os="Windows 10", ip_addresses=["10.0.0.5"]))
    repo.save(wm)

    # Second observation: same host (case-insensitive), new domain + a new NIC.
    repo.apply_deltas(
        WorkingMemoryDeltas(
            hosts=[Host(name="ws01", domain="ACME", ip_addresses=["10.0.0.6"])]
        )
    )

    hosts = repo.load().hosts
    assert len(hosts) == 1  # merged, not duplicated
    assert hosts[0].os == "Windows 10"  # preserved (incoming was None)
    assert hosts[0].domain == "ACME"  # filled
    assert hosts[0].ip_addresses == ["10.0.0.5", "10.0.0.6"]  # union


def test_apply_deltas_credentials_dedup_by_domain_user_secrettype(tmp_path):
    from redrobin.domain.memory import Credential, WorkingMemory, WorkingMemoryDeltas
    from redrobin.infrastructure.persistence.working_memory_repo import WorkingMemoryRepo

    repo = WorkingMemoryRepo(tmp_path / "wm.json")
    repo.save(WorkingMemory(engagement_id="eng"))

    # Same user, two DIFFERENT secret types -> both kept. Same user+type twice -> deduped.
    repo.apply_deltas(
        WorkingMemoryDeltas(
            credentials=[
                Credential(domain="ACME", username="jdoe", secret_type="ntlm", secret="hashA"),
                Credential(domain="ACME", username="jdoe", secret_type="plaintext", secret="pw"),
            ]
        )
    )
    repo.apply_deltas(
        WorkingMemoryDeltas(
            credentials=[Credential(domain="ACME", username="jdoe", secret_type="ntlm", secret="hashB")]
        )
    )

    creds = repo.load().credentials
    by_type = {c.secret_type: c.secret for c in creds}
    assert by_type == {"ntlm": "hashB", "plaintext": "pw"}  # ntlm replaced, plaintext kept


def test_apply_deltas_findings_append_defenses_union_tools_dedup(tmp_path):
    from redrobin.domain.memory import Finding, ToolUsage, WorkingMemory, WorkingMemoryDeltas
    from redrobin.infrastructure.persistence.working_memory_repo import WorkingMemoryRepo

    repo = WorkingMemoryRepo(tmp_path / "wm.json")
    repo.save(WorkingMemory(engagement_id="eng"))

    repo.apply_deltas(
        WorkingMemoryDeltas(
            findings=[Finding(kind="k1", summary="a")],
            defenses_observed=["Defender"],
            tools_used=[ToolUsage(name="nanodump", used_on="WS01", used_at="t")],
        )
    )
    repo.apply_deltas(
        WorkingMemoryDeltas(
            findings=[Finding(kind="k1", summary="a")],  # findings always append
            defenses_observed=["Defender", "AMSI"],  # union
            tools_used=[ToolUsage(name="nanodump", used_on="WS01", used_at="t2")],  # dedup
        )
    )

    wm = repo.load()
    assert len(wm.findings) == 2  # append-only audit stream
    assert sorted(wm.defenses_observed) == ["AMSI", "Defender"]
    assert len(wm.tools_used) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_working_memory_repo.py::test_apply_deltas_merges_host_nonnull_overwrite_and_ip_union -v`
Expected: FAIL with `AttributeError: 'WorkingMemoryRepo' object has no attribute 'apply_deltas'`

- [ ] **Step 3: Implement `apply_deltas`**

In `redrobin/infrastructure/persistence/working_memory_repo.py`, add `WorkingMemoryDeltas` to the domain import block, then add the method (place it after `append_finding`, before the `# --- (de)serialization ---` section):

Import change (top of file, in the existing `from redrobin.domain.memory import (...)`):
```python
    WorkingMemory,
    WorkingMemoryDeltas,
)
```

Method:
```python
    def apply_deltas(self, deltas: WorkingMemoryDeltas) -> None:
        """Merge a whole deltas batch in one load->merge->save (single writer).

        Host: dedup by upper(name), non-null scalar overwrite, IP set-union.
        Credential: dedup/replace by (domain, username, secret_type).
        Finding: append-only (audit stream). Defenses: set-union.
        Tool: dedup by (name, used_on).
        """
        wm = self.load()
        for host in deltas.hosts:
            self._merge_host(wm, host)
        for cred in deltas.credentials:
            self._merge_credential(wm, cred)
        for finding in deltas.findings:
            wm.findings.append(finding)
        for tool in deltas.tools_used:
            self._merge_tool(wm, tool)
        for defense in deltas.defenses_observed:
            if defense not in wm.defenses_observed:
                wm.defenses_observed.append(defense)
        self.save(wm)

    @staticmethod
    def _merge_host(wm: WorkingMemory, host: Host) -> None:
        normalized = host.name.upper()
        for existing in wm.hosts:
            if existing.name.upper() == normalized:
                existing.os = host.os or existing.os
                existing.os_version = host.os_version or existing.os_version
                existing.domain = host.domain or existing.domain
                existing.integrity_level = host.integrity_level or existing.integrity_level
                existing.edr_detected = host.edr_detected or existing.edr_detected
                existing.access_level = host.access_level or existing.access_level
                for ip in host.ip_addresses:
                    if ip not in existing.ip_addresses:
                        existing.ip_addresses.append(ip)
                return
        host.name = normalized
        wm.hosts.append(host)

    @staticmethod
    def _merge_credential(wm: WorkingMemory, cred: Credential) -> None:
        key = (cred.domain.lower(), cred.username.lower(), cred.secret_type.lower())
        wm.credentials = [
            c
            for c in wm.credentials
            if (c.domain.lower(), c.username.lower(), c.secret_type.lower()) != key
        ]
        wm.credentials.append(cred)

    @staticmethod
    def _merge_tool(wm: WorkingMemory, tool: ToolUsage) -> None:
        key = (tool.name.lower(), tool.used_on.lower())
        for existing in wm.tools_used:
            if (existing.name.lower(), existing.used_on.lower()) == key:
                return
        wm.tools_used.append(tool)
```

Note: `Host`, `ToolUsage` are already imported in this file's domain import block; only `WorkingMemoryDeltas` is new.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_working_memory_repo.py -k apply_deltas -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add redrobin/infrastructure/persistence/working_memory_repo.py tests/unit/test_working_memory_repo.py
git commit -m "feat(memory): WorkingMemoryRepo.apply_deltas (single-writer batch merge)"
```

---

### Task 5: Reducer `working_memory.deltas` handler

**Why:** The reducer is the single WM writer. It gains one handler that turns a `working_memory.deltas` bus event into `repo.apply_deltas`, merged serially alongside the existing event types. The Plan B Enricher publishes these events; for Plan A the handler is exercised with synthetic events. The existing regex path (`_on_beacon_command_output`) is **untouched**.

**Files:**
- Modify: `redrobin/application/working_memory_reducer.py`
- Test: `tests/integration/test_working_memory_reducer.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/integration/test_working_memory_reducer.py` (reuses the module's existing `_new_repo` / `_start_reducer` / `_drain` helpers):

```python
@pytest.mark.asyncio
async def test_working_memory_deltas_event_merges_and_reemits(tmp_path):
    """A working_memory.deltas event is merged via apply_deltas + re-emits a snapshot."""
    bus = EventBus()
    repo = _new_repo(tmp_path, "eng-deltas")
    observer = await bus.subscribe()  # stands in for a connected TUI
    _, task = await _start_reducer(bus, repo, "eng-deltas")
    try:
        await bus.publish(
            {
                "type": "working_memory.deltas",
                "payload": {
                    "source_beacon_id": "B-7",
                    "hosts": [
                        {
                            "name": "ws01",
                            "os": "Windows 10",
                            "os_version": "10.0.19045",
                            "domain": "ACME",
                            "ip_addresses": ["10.0.0.5"],
                        }
                    ],
                    "credentials": [
                        {"domain": "ACME", "username": "jdoe", "secret_type": "plaintext", "secret": "pw"}
                    ],
                    "findings": [{"kind": "gpp_password", "summary": "Groups.xml cpassword"}],
                },
            }
        )
        host = None
        for _ in range(50):
            await asyncio.sleep(0.02)
            hosts = repo.load().hosts
            if hosts:
                host = hosts[0]
                break
        assert host is not None and host.domain == "ACME"
        assert host.ip_addresses == ["10.0.0.5"]
        wm = repo.load()
        assert any(
            c.username == "jdoe" and c.secret_type == "plaintext" and c.source_beacon_id == "B-7"
            for c in wm.credentials
        )
        assert any(f.kind == "gpp_password" and f.source_beacon_id == "B-7" for f in wm.findings)

        # #146: a snapshot must be re-emitted so the TUI updates live.
        saw_snapshot = False
        for _ in range(50):
            try:
                evt = observer.get_nowait()
            except Exception:
                await asyncio.sleep(0.02)
                continue
            if evt.get("type") == "working_memory_snapshot":
                saw_snapshot = True
                break
        assert saw_snapshot, "reducer did not re-publish a snapshot after deltas merge"
    finally:
        await _drain(bus, task)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_working_memory_reducer.py::test_working_memory_deltas_event_merges_and_reemits -v`
Expected: FAIL — the host never appears (the `working_memory.deltas` type is not in `_dispatch`, so the event is ignored and `repo.load().hosts` stays empty → assertion `host is not None` fails).

- [ ] **Step 3: Wire the handler**

In `redrobin/application/working_memory_reducer.py`:

(a) Add `WorkingMemoryDeltas` to the domain import block:
```python
from redrobin.domain.memory import (
    Credential,
    Finding,
    Host,
    Session,
    WorkingMemoryDeltas,
    working_memory_to_snapshot,
)
```

(b) In `_dispatch`, add a branch after the `opsec.verdict` branch (before the `# else` comment):
```python
        elif etype == "working_memory.deltas":
            self._on_enrichment_deltas(payload)
            return True
```

(c) Add the handler method (place it after `_on_verdict`):
```python
    def _on_enrichment_deltas(self, payload: dict[str, Any]) -> None:
        """Merge an enrichment deltas batch. Provenance is taken from the
        event's top-level ``source_beacon_id`` (system-supplied), not from any
        per-item value, so the producer cannot spoof it. The reducer is the
        single writer to working memory (see ``apply_deltas``)."""
        src = payload.get("source_beacon_id")
        deltas = WorkingMemoryDeltas.from_payload(
            payload, source_beacon_id=str(src) if src is not None else None
        )
        self._repo.apply_deltas(deltas)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_working_memory_reducer.py -v`
Expected: PASS (all existing reducer tests + the new one)

- [ ] **Step 5: Commit**

```bash
git add redrobin/application/working_memory_reducer.py tests/integration/test_working_memory_reducer.py
git commit -m "feat(reducer): handle working_memory.deltas events via apply_deltas (single writer)"
```

---

### Task 6: Full-suite gate + quality checks

**Why:** Confirm Plan A introduces no regressions across the whole suite and passes the same gates CI runs (ruff, format, mypy, import-linter). The existing regex reducer tests (NTLM/hostname/kerberos) must still pass — Plan A did not touch that path.

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS — the prior suite count + 12 new tests (2 Host + 2 Credential + 4 deltas + 3 apply_deltas + 1 reducer), 0 failures. The pre-existing `test_beacon_command_output_*` reducer tests still pass (regex path untouched).

- [ ] **Step 2: Run the static gates**

Run:
```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy redrobin
uv run lint-imports
```
Expected: all clean. (If `ruff format --check` flags the new code, run `uv run ruff format .` and re-commit. `mypy`: `WorkingMemoryDeltas.from_payload` takes `Any` and returns the typed object — no `# type: ignore` needed. `lint-imports`: `WorkingMemoryDeltas` is in `domain`, so the infra repo importing it satisfies the contracts.)

- [ ] **Step 3: Commit any formatting fixups**

```bash
git add -A
git commit -m "chore: ruff format Plan A enrichment-deltas pathway" || echo "nothing to format"
```

---

## Self-Review

**1. Spec coverage (§5 contract, §6 merge + schema, of the Plan-A slice):**
- Host +domain/ip_addresses/os_version → Task 1 ✓
- Credential generalized (secret_type/secret, keep ntlm_hash) → Task 2 ✓
- Delta contract + whitelisting + system-supplied provenance → Task 3 ✓
- `apply_deltas` single-writer merge (host union/overwrite, cred dedup by 3-key, finding append, defense union, tool dedup) → Task 4 ✓
- Reducer single-writer consumes `working_memory.deltas` + re-emits snapshot (#146) → Task 5 ✓
- Back-compat (old wm.json loads) → Tasks 1 & 2 ✓
- **Deferred to Plan B (correctly out of scope here):** DeltaParser (JSON-string parsing), EnrichmentTrigger, chunker, WorkingMemoryEnricher, Haiku client, lifespan wiring, observability events, and deletion of the regex + `upsert_host`/`upsert_credential`.

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; every run step shows the command + expected result. ✓

**3. Type consistency:** `WorkingMemoryDeltas.from_payload(payload, *, source_beacon_id)` / `to_payload()` used identically in Tasks 3 & 5. `apply_deltas(deltas: WorkingMemoryDeltas)` defined in Task 4, called in Task 5. `secret_type` field (Task 2) used as the 3rd dedup key in Task 4 and asserted in Task 5. Host fields (Task 1) populated by `from_payload` (Task 3) and merged in Task 4. Consistent. ✓

**4. No-dead-code note:** Plan A leaves `upsert_host`/`upsert_credential` and the regex in place **intentionally** — they remain wired (the regex path still runs) until Plan B replaces them. `WorkingMemoryDeltas.to_payload` is the symmetric half of the contract (used by the Plan B enricher) and is exercised by the round-trip test in Task 3. Nothing in Plan A is orphaned.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-26-wm-enrichment-plan-a-deltas-pathway.md`. Recommended next step per the operator's standing workflow: **`/auditing-plans`** on this plan (verify against the real codebase), then **subagent-driven-development** (Opus 4.7, fresh subagent per task + two-stage review), then a smoke check (the deltas pathway is testable without a live beacon — synthetic `working_memory.deltas` event). Plan B (the Enricher + regex retirement) follows after Plan A merges.
