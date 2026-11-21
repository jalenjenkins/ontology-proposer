import pytest
from ontology.data import demo, profile
from ontology.semantic import validate, visible, answer, scoped

@pytest.fixture
def db(tmp_path):
    return demo(tmp_path / "demo.duckdb")

def test_equal_cost_claims_still_fan_out(db):
    metric = dict(kind="metric", name="cost", table="claims", key="id",
                  op="sum", column="cost", joins=[], confidence=1, roles=["admin"])
    assert validate(db, metric)["value"] == 1400
    metric["joins"] = [dict(table="claim_lines", left="id", right="claim")]
    result = validate(db, metric)
    assert not result["ok"]
    assert result["rows"] > result["distinct_keys"]

def test_inner_join_drops_unmatched_claims(db):
    metric = dict(kind="metric", name="cost", table="claims", key="id",
                  op="sum", column="cost", confidence=1, roles=["admin"],
                  joins=[dict(table="claim_lines", left="id", right="id")])
    assert not validate(db, metric)["ok"]

def test_query_context_and_database_exclude_protected_fields(db):
    objects = [dict(kind="property", name="patients.city", table="patients", column="city", roles=["analyst"]),
               dict(kind="property", name="patients.private_note", table="patients", column="private_note", roles=["admin"])]
    role_objects = visible(objects, "analyst")
    assert "private_note" not in str(role_objects)
    with scoped(db, role_objects) as session:
        assert session.execute("select city from patients limit 1").fetchone()
        assert session.execute("select column_name from information_schema.columns where table_name='patients'").fetchall() == [("city",)]
    with pytest.raises(ValueError):
        answer(db, role_objects, {"metric": "patients.private_note"})


def test_review_rejects_inflation_and_preserves_history(db, tmp_path):
    from ontology.proposer import fixture_proposal
    from ontology.store import Store

    store = Store(tmp_path / "review.sqlite")
    proposal = fixture_proposal(db)
    batch = store.stage(db, proposal)
    stale = store.stage(db, proposal)
    for pending in (batch, stale):
        for change in store.changes(pending):
            if not change["validation"]["ok"]:
                with pytest.raises(ValueError, match="Validation failed"):
                    store.decide(change["id"], "approve")
                store.decide(change["id"], "reject")
            else:
                store.decide(change["id"], "approve")
    version = store.publish(batch)
    assert store.publish(batch) == version
    assert store.snapshot(0) == []
    assert "inflated_claim_cost" not in {obj["name"] for obj in store.snapshot(version)}
    with pytest.raises(ValueError, match="Stale batch"):
        store.publish(stale)
