from redrobin.application.engagement_loader import Engagement
from redrobin.application.working_memory_seed import apply_engagement_scope
from redrobin.domain.memory import Objective, WorkingMemory


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


def test_apply_engagement_scope_seeds_roe_and_objectives_clean() -> None:
    wm = WorkingMemory(engagement_id="e")
    eng = Engagement(
        slug="e",
        name="E",
        rules_of_engagement=["No DoS", "Business hours only"],
        objectives=[Objective(id="o1", goal="Domain admin", priority="high")],
    )
    out = apply_engagement_scope(wm, eng)
    assert out.rules_of_engagement == ["No DoS", "Business hours only"]
    assert len(out.objectives) == 1
    assert out.objectives[0].id == "o1"
    assert out.objectives[0].goal == "Domain admin"
    assert out.objectives[0].priority == "high"
    assert out.objectives[0].status == "not_started"  # clean interpretation
    assert out.objectives[0].progress_pct == 0


def test_apply_engagement_scope_preserves_objective_interpretation_on_reseed() -> None:
    # WM already has o1 with interpretation written (as the future assistant would).
    wm = WorkingMemory(
        engagement_id="e",
        objectives=[
            Objective(
                id="o1", goal="old goal", priority="low", status="in_progress", progress_pct=40
            )
        ],
    )
    # The yaml re-seeds o1 with a refreshed definition.
    eng = Engagement(
        slug="e", name="E", objectives=[Objective(id="o1", goal="new goal", priority="high")]
    )
    out = apply_engagement_scope(wm, eng)
    o = out.objectives[0]
    assert o.goal == "new goal"  # definition refreshed from yaml
    assert o.priority == "high"  # definition refreshed
    assert o.status == "in_progress"  # interpretation PRESERVED
    assert o.progress_pct == 40  # interpretation PRESERVED


def test_apply_engagement_scope_adds_new_objective_clean_keeps_existing() -> None:
    wm = WorkingMemory(
        engagement_id="e",
        objectives=[Objective(id="o1", goal="g1", status="done", progress_pct=100)],
    )
    eng = Engagement(
        slug="e",
        name="E",
        objectives=[
            Objective(id="o1", goal="g1", priority="medium"),
            Objective(id="o2", goal="g2", priority="low"),
        ],
    )
    out = apply_engagement_scope(wm, eng)
    by_id = {o.id: o for o in out.objectives}
    assert by_id["o1"].status == "done"  # preserved
    assert by_id["o2"].status == "not_started"  # new, clean


def test_apply_engagement_scope_drops_objective_removed_from_yaml() -> None:
    wm = WorkingMemory(engagement_id="e", objectives=[Objective(id="o1", goal="g1")])
    eng = Engagement(slug="e", name="E", objectives=[])  # o1 removed from yaml
    out = apply_engagement_scope(wm, eng)
    assert out.objectives == []


def test_apply_engagement_scope_leaves_phase_and_kill_chain_untouched() -> None:
    wm = WorkingMemory(
        engagement_id="e",
        current_phase="lateral-movement",
        kill_chain_status={"recon": "done"},
    )
    eng = Engagement(slug="e", name="E", objectives=[Objective(id="o1", goal="g")])
    out = apply_engagement_scope(wm, eng)
    assert out.current_phase == "lateral-movement"  # interpretation untouched
    assert out.kill_chain_status == {"recon": "done"}
