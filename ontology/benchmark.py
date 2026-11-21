import json
import re
from pathlib import Path

from ontology.data import open_db
from ontology.query import live_answer
from ontology.semantic import equivalent, execute_plan, scoped, visible
from ontology.sql import read_sql


QUESTIONS = Path(__file__).resolve().parent.parent / "eval" / "questions.json"


def offline_answer(db, question, objects, semantic, verify):
    match = re.fullmatch(
        r"(How many patients|How many encounters|How many claims|What is the total claim cost|"
        r"What is the average claim cost)(?: with (city|gender|encounterclass|status) ([\w-]+))?\?",
        question,
    )
    if not match:
        return dict(status="abstain")
    intent, column, value = match.groups()
    metric, aggregate, table = {
        "How many patients": ("patient_count", "count(*)", "patients"),
        "How many encounters": ("encounter_count", "count(*)", "encounters"),
        "How many claims": ("claim_count", "count(*)", "claims"),
        "What is the total claim cost": ("claim_cost", "sum(cost)", "claims"),
        "What is the average claim cost": ("average_claim_cost", "avg(cost)", "claims"),
    }[intent]
    if semantic:
        plan = {"metric": metric}
        if column:
            plan["where"] = dict(column=column, value=value)
        return execute_plan(db, objects, plan, verify)

    # A reproducible schema-only heuristic takes an unnecessary claim-line join
    # when totaling cost. Verification evaluates the aggregate directly at claim grain.
    joined = " join claim_lines on claims.id = claim_lines.claim" if metric == "claim_cost" else ""
    predicate = f" where {column}='{value}'" if column else ""
    sql = f"select {aggregate} from {table}{joined}{predicate}"
    primary = read_sql(db, sql)[0][0]
    if verify:
        secondary = read_sql(db, f"select {aggregate} from {table}{predicate}")[0][0]
        if not equivalent(primary, secondary):
            return dict(status="disagreement", sql=sql)
    return dict(status="ok", value=primary, sql=sql)


def evaluate(path, objects, live=False):
    cases = json.loads(QUESTIONS.read_text())
    with open_db(path, True) as raw:
        expected = [read_sql(raw, case["gold_sql"])[0][0] for case in cases]
    objects = visible(objects, "analyst")
    results = {}
    with scoped(path, objects) as db:
        for semantic in (False, True):
            for verify in (False, True):
                key = f"{'ontology' if semantic else 'schema'}_{'verified' if verify else 'single'}"
                rows = []
                for case, gold in zip(cases, expected):
                    try:
                        answer = (live_answer(db, case["question"], objects if semantic else None, verify)
                                  if live else offline_answer(db, case["question"], objects, semantic, verify))
                        correct = answer["status"] == "ok" and equivalent(answer["value"], gold)
                    except Exception as exc:
                        answer, correct = dict(status="error", error=type(exc).__name__), False
                    rows.append(dict(id=case["id"], expected=gold, correct=correct, **answer))
                correct = sum(row["correct"] for row in rows)
                returned = sum(row["status"] == "ok" for row in rows)
                results[key] = dict(correct=correct, total=len(rows), accuracy=correct / len(rows),
                                    returned=returned, wrong_returned=returned-correct, cases=rows)
    return dict(mode="live" if live else "offline", results=results)


def compare(current, previous):
    if current["mode"] != previous["mode"]:
        raise ValueError("Compare runs from the same mode")
    return {name: sorted({row["id"] for row in previous["results"][name]["cases"] if row["correct"]}
                         - {row["id"] for row in report["cases"] if row["correct"]})
            for name, report in current["results"].items()}
