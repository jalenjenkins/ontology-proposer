import json

import pytest

from ontology.data import demo, open_db
from ontology.gate import compare_replays
from ontology.proposer import fixture_proposal
from ontology.store import Store


@pytest.fixture
def release(tmp_path):
    path = demo(tmp_path / "fixture.duckdb")
    store = Store(tmp_path / "review.sqlite")
    cases = [
        dict(id="cost", role="analyst", plan={"metric": "claim_cost"}, dependencies="structured_v1"),
        dict(id="city", role="analyst", plan={"metric": "patient_count",
             "where": {"column": "city", "value": "Boston"}}, dependencies="structured_v1"),
        dict(id="control", role="admin", plan={"metric": "patient_count",
             "where": {"column": "city", "value": "Boston"}}, dependencies="structured_v1"),
    ]
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps(cases))
    stage(store, path, fixture_proposal(path))
    assert store.publish(1) == 1
    return store, path, suite, cases


def stage(store, path, objects):
    batch = store.stage(path, objects)
    for change in store.changes(batch):
        store.decide(change["id"], "approve" if change["validation"]["ok"] else "reject")
    return batch


def test_targeted_matches_full_and_missing_dependencies_fall_back(release):
    store, path, _, cases = release
    before = store.snapshot()
    after = [dict(obj, op="avg") if obj["name"] == "claim_cost" else obj for obj in before]
    reports = compare_replays(path, before, after, cases)
    assert reports["targeted"]["checked"] == 1
    assert reports["full"]["checked"] == 3
    assert reports["targeted"]["findings"][0]["before"] == 1400
    assert reports["targeted"]["findings"][0]["after"] == 87.5
    assert reports["missed"] == []
    cases[0].pop("dependencies")
    assert compare_replays(path, before, after, cases)["targeted"]["mode"] == "fallback"


def test_answer_change_requires_ack_and_evidence_expires_on_data_change(release):
    store, path, suite, _ = release
    metric = next(obj for obj in store.snapshot() if obj["name"] == "claim_cost")
    batch = stage(store, path, [dict(metric, op="avg")])
    with pytest.raises(ValueError, match="Run the change gate"):
        store.publish(batch, path, suite)
    report = store.check_gate(batch, path, suite)
    with pytest.raises(ValueError, match="Acknowledge"):
        store.publish(batch, path, suite)
    store.acknowledge_gate(batch, report["evidence_key"])
    with open_db(path) as db:
        db.execute("update claims set cost=cost+1 where id=1")
    with pytest.raises(ValueError, match="Run the change gate"):
        store.publish(batch, path, suite)
    new_report = store.check_gate(batch, path, suite)
    store.acknowledge_gate(batch, new_report["evidence_key"])
    assert store.publish(batch, path, suite) == 2


def test_permission_changes_are_redacted_and_cannot_be_acknowledged(release):
    store, path, suite, cases = release
    prop = next(obj for obj in store.snapshot() if obj["name"] == "patients.city")
    before = store.snapshot()
    after = [dict(obj, roles=["admin"]) if obj["name"] == prop["name"] else obj for obj in before]
    grants = compare_replays(path, after, before, cases)
    assert grants["targeted"]["findings"] == [
        dict(id="city", role="analyst", status="access_changed", before="denied", after="ok")]
    batch = stage(store, path, [dict(prop, roles=["admin"])])
    report = store.check_gate(batch, path, suite)
    assert report["blocked"]
    assert report["findings"] == [dict(id="city", role="analyst", status="access_changed",
                                      before="ok", after="denied")]
    with pytest.raises(ValueError, match="Only answer changes"):
        store.acknowledge_gate(batch, report["evidence_key"])
    with pytest.raises(ValueError, match="blocked"):
        store.publish(batch, path, suite)


def test_suite_edit_invalidates_gate_evidence(release):
    store, path, suite, cases = release
    metric = next(obj for obj in store.snapshot() if obj["name"] == "claim_cost")
    batch = stage(store, path, [dict(metric, description="Claims total")])
    assert store.check_gate(batch, path, suite)["checked"] == 0
    cases[0].pop("dependencies")
    suite.write_text(json.dumps(cases))
    with pytest.raises(ValueError, match="Run the change gate"):
        store.publish(batch, path, suite)
    assert store.check_gate(batch, path, suite)["checked"] == len(cases)
    assert store.publish(batch, path, suite) == 2
