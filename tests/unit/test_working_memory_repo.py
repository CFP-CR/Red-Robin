"""Working memory repo — JSON read/write round-trip."""

import pytest

from redrobin.domain.memory import (
    Account,
    Credential,
    Finding,
    Host,
    Objective,
    ToolUsage,
    WorkingMemory,
    WorkingMemoryDeltas,
)
from redrobin.infrastructure.persistence.working_memory_repo import WorkingMemoryRepo


def test_save_and_load_round_trip(tmp_path):
    wm = WorkingMemory(
        engagement_id="acme-q2-2026",
        client="Acme Corp",
        scope_in_scope=["*.acme.local"],
        scope_out_of_scope=["10.0.99.0/24"],
        objectives=[
            Objective(id="obj_1", goal="Compromise DC", priority="high", progress_pct=60),
        ],
        current_phase="credential_access",
        hosts=[Host(name="DC01", os="Win2022", edr_detected="CrowdStrike", access_level="SYSTEM")],
        accounts=[Account(name="acme\\svc_sql", kind="plaintext", source_host="DC01")],
        defenses_observed=["CrowdStrike Falcon"],
    )
    repo = WorkingMemoryRepo(tmp_path / "wm.json")
    repo.save(wm)
    loaded = repo.load()
    assert loaded.engagement_id == "acme-q2-2026"
    assert loaded.objectives[0].progress_pct == 60
    assert loaded.hosts[0].edr_detected == "CrowdStrike"
    assert loaded.accounts[0].kind == "plaintext"


def test_load_missing_file_raises(tmp_path):
    repo = WorkingMemoryRepo(tmp_path / "nonexistent.json")
    with pytest.raises(FileNotFoundError):
        repo.load()


def test_save_creates_parent_dirs(tmp_path):
    nested = tmp_path / "engagements" / "acme" / "wm.json"
    wm = WorkingMemory(engagement_id="acme")
    WorkingMemoryRepo(nested).save(wm)
    assert nested.exists()


def test_exists_returns_false_for_missing_file(tmp_path):
    repo = WorkingMemoryRepo(tmp_path / "nope.json")
    assert repo.exists() is False


def test_save_leaves_no_temp_file(tmp_path):
    """Atomic save() cleans up its temp via the replace — no leftover .tmp."""
    repo = WorkingMemoryRepo(tmp_path / "wm.json")
    repo.save(WorkingMemory(engagement_id="acme"))
    assert list(tmp_path.glob("*.tmp")) == []


def test_save_overwrites_stale_temp_file(tmp_path):
    """A leftover temp from a crashed prior save must not break the next save."""
    target = tmp_path / "wm.json"
    (tmp_path / "wm.json.tmp").write_text("garbage-half-write")
    repo = WorkingMemoryRepo(target)
    repo.save(WorkingMemory(engagement_id="acme"))
    assert repo.load().engagement_id == "acme"
    assert list(tmp_path.glob("*.tmp")) == []


def test_host_new_fields_round_trip(tmp_path):
    """Host gains domain / ip_addresses / os_version; they survive save+load."""
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


def test_credential_generalized_round_trip(tmp_path):
    """Credential gains secret_type + secret; plaintext is captured, not dropped."""
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
    assert cred.ntlm_hash is None  # plaintext cred must not bleed into ntlm_hash


def test_credential_back_compat_old_json_defaults_to_ntlm(tmp_path):
    """A pre-existing credential dict (ntlm_hash only) loads with secret_type='ntlm'."""
    import json

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


def test_apply_deltas_merges_host_nonnull_overwrite_and_ip_union(tmp_path):
    repo = WorkingMemoryRepo(tmp_path / "wm.json")
    wm = WorkingMemory(engagement_id="eng")
    wm.hosts.append(Host(name="WS01", os="Windows 10", ip_addresses=["10.0.0.5"]))
    repo.save(wm)

    # Second observation: same host (case-insensitive), new domain + a new NIC.
    repo.apply_deltas(
        WorkingMemoryDeltas(hosts=[Host(name="ws01", domain="ACME", ip_addresses=["10.0.0.6"])])
    )

    hosts = repo.load().hosts
    assert len(hosts) == 1  # merged, not duplicated
    assert hosts[0].os == "Windows 10"  # preserved (incoming was None)
    assert hosts[0].domain == "ACME"  # filled
    assert hosts[0].ip_addresses == ["10.0.0.5", "10.0.0.6"]  # union


def test_apply_deltas_credentials_dedup_by_domain_user_secrettype(tmp_path):
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
            credentials=[
                Credential(domain="ACME", username="jdoe", secret_type="ntlm", secret="hashB")
            ]
        )
    )

    creds = repo.load().credentials
    by_type = {c.secret_type: c.secret for c in creds}
    assert by_type == {"ntlm": "hashB", "plaintext": "pw"}  # ntlm replaced, plaintext kept


def test_apply_deltas_findings_append_defenses_union_tools_dedup(tmp_path):
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
    assert wm.tools_used[0].used_at == "t"  # first-wins dedup, not overwritten
