from redrobin.application.engagement_loader import Engagement
from redrobin.application.working_memory_seed import apply_engagement_scope
from redrobin.domain.memory import WorkingMemory


def test_apply_engagement_scope_sets_client_and_scope():
    wm = WorkingMemory(engagement_id="e2e", current_phase="recon")
    eng = Engagement(
        slug="e2e",
        name="E2E",
        in_scope=["EC2AMAZ-IJVFMFN", "10.100.0.0/24"],
        out_of_scope=["10.99.0.0/16"],
        client="Acme",
    )
    out = apply_engagement_scope(wm, eng)
    assert out.client == "Acme"
    assert out.scope_in_scope == ["EC2AMAZ-IJVFMFN", "10.100.0.0/24"]
    assert out.scope_out_of_scope == ["10.99.0.0/16"]


def test_apply_engagement_scope_preserves_other_fields():
    wm = WorkingMemory(engagement_id="e2e", current_phase="recon")
    eng = Engagement(slug="e2e", name="E2E", in_scope=["a"], out_of_scope=[], client="X")
    out = apply_engagement_scope(wm, eng)
    assert out.engagement_id == "e2e"
    assert out.current_phase == "recon"


def test_apply_engagement_scope_empty_engagement_is_safe():
    out = apply_engagement_scope(WorkingMemory(engagement_id="e"), Engagement(slug="e", name="E"))
    assert out.client == ""
    assert out.scope_in_scope == []
    assert out.scope_out_of_scope == []
