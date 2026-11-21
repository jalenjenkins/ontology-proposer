from pathlib import Path

from ontology.data import catalog, open_db
from ontology.gate import compare_replays, load_suite, pinned_source
from ontology.proposer import synthea_proposal

SUITE = Path(__file__).resolve().parent.parent / "eval" / "synthea-gate-cases.json"


def proposal(path, baseline, scenario):
    by_name = {obj["name"]: obj for obj in baseline}
    metric = by_name["encounter_claim_cost"]
    if scenario == "permissions":
        return [dict(by_name["patients.city"], roles=["admin"])]
    if scenario == "harmless":
        return [dict(metric, description="Sum of recorded encounter claim costs")]
    if scenario == "untracked":
        return [dict(metric, where=dict(column=next(obj["column"] for obj in baseline
                                                    if obj["name"] == "encounters.encounterclass"),
                                       value="outpatient"))]
    with open_db(path, True) as db:
        column = next(c for t, c, _ in catalog(db) if t == "encounters" and c.lower() == "base_encounter_cost")
    property_ = dict(kind="property", name="encounters.base_encounter_cost", table="encounters",
                     column=column, confidence=1, roles=["analyst", "admin"], evidence=[])
    return [property_, dict(metric, column=column, description="Use the base encounter charge")]


def stage_demo(path, store, scenario):
    if not store.snapshot():
        batch = store.stage(path, synthea_proposal(path))
        for change in store.changes(batch):
            store.decide(change["id"], "approve" if change["validation"]["ok"] else "reject")
        store.publish(batch)
    batch = store.stage(path, proposal(path, store.snapshot(), scenario))
    return dict(batch=batch, scenario=scenario, suite=str(SUITE))


def compare_demo(path):
    cases = load_suite(SUITE)
    with pinned_source(path):
        initial = synthea_proposal(path)
        baseline = [obj for obj in initial if not obj["name"].startswith("inflated_")]
        reports = {}
        for scenario in ("cost", "permissions", "harmless", "untracked", "fanout"):
            updated = {obj["name"]: obj for obj in baseline}
            if scenario == "fanout":
                bad = next(obj for obj in initial if obj["name"].startswith("inflated_"))
                updated["encounter_claim_cost"] = dict(bad, name="encounter_claim_cost")
            else:
                updated.update({obj["name"]: obj for obj in proposal(path, baseline, scenario)})
            suite = [dict(case) for case in cases]
            if scenario == "untracked":
                suite[0].pop("dependencies")
            reports[scenario] = compare_replays(path, baseline, list(updated.values()), suite)
    return reports
