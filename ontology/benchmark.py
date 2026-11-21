import json
import os
import sys
from urllib.error import URLError
import re
from pathlib import Path

from ontology.data import open_db
from ontology.query import live_answer
from ontology.semantic import equivalent, execute_plan, scoped, visible
from ontology.sql import read_sql


QUESTIONS = Path(__file__).resolve().parent.parent / "eval" / "questions.json"


def offline_answer(db, question, objects, semantic, verify, synthea=False):
    match = re.fullmatch(
        r"(How many patients|How many encounters|How many claims|What is the total claim cost|"
        r"What is the average claim cost|What is the total encounter claim cost|What is the average encounter claim cost)(?: with (city|gender|encounterclass|status1|status) ([\w-]+))?\?",
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
        "What is the total encounter claim cost": ("encounter_claim_cost", "sum(total_claim_cost)", "encounters"),
        "What is the average encounter claim cost": ("average_encounter_claim_cost", "avg(total_claim_cost)", "encounters"),
    }[intent]
    if synthea and column:
        column = column.upper()
    if semantic:
        plan = {"metric": metric}
        if column:
            plan["where"] = dict(column=column, value=value)
        return execute_plan(db, objects, plan, verify)

    # A reproducible schema-only heuristic takes an unnecessary claim-line join
    # when totaling cost. Verification evaluates the aggregate directly at claim grain.
    joined = " join claim_lines on claims.id = claim_lines.claim" if metric == "claim_cost" else ""
    if metric == "encounter_claim_cost":
        joined = " join claims_transactions on encounters.id = claims_transactions.appointmentid"
    predicate = f" where {column}='{value}'" if column else ""
    sql = f"select {aggregate} from {table}{joined}{predicate}"
    primary = read_sql(db, sql)[0][0]
    if verify:
        secondary = read_sql(db, f"select {aggregate} from {table}{predicate}")[0][0]
        if not equivalent(primary, secondary):
            return dict(status="disagreement", sql=sql)
    return dict(status="ok", value=primary, sql=sql)


def evaluate(path, objects, live=False, synthea=False):
    if live and not all(os.environ.get(name) for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL")):
        raise ValueError("Set ANTHROPIC_API_KEY and ANTHROPIC_MODEL before running a live eval")
    if live and not objects:
        raise ValueError("Publish an ontology before running a live eval")
    questions = QUESTIONS.with_name("synthea-questions.json") if synthea else QUESTIONS
    cases = json.loads(questions.read_text())
    with open_db(path, True) as raw:
        expected = [read_sql(raw, case["gold_sql"])[0][0] for case in cases]
    objects = visible(objects, "analyst")
    results = {}
    with scoped(path, objects) as db:
        for semantic in (False, True):
            for verify in (False, True):
                key = f"{'ontology' if semantic else 'schema'}_{'verified' if verify else 'single'}"
                if live:
                    print(f"Running {key} ({len(cases)} questions)", file=sys.stderr, flush=True)
                rows = []
                for case, gold in zip(cases, expected):
                    try:
                        answer = (live_answer(db, case["question"], objects if semantic else None, verify)
                                  if live else offline_answer(db, case["question"], objects, semantic, verify, synthea))
                        correct = answer["status"] == "ok" and equivalent(answer["value"], gold)
                    except (URLError, TimeoutError):
                        raise
                    except Exception as exc:
                        answer, correct = dict(status="error", error=type(exc).__name__), False
                    rows.append(dict(id=case["id"], expected=gold, correct=correct, **answer))
                correct = sum(row["correct"] for row in rows)
                returned = sum(row["status"] == "ok" for row in rows)
                results[key] = dict(correct=correct, total=len(rows), accuracy=correct / len(rows),
                                    returned=returned, wrong_returned=returned-correct, cases=rows)
    report = dict(mode="live" if live else "offline", dataset="synthea" if synthea else "fixture", results=results)
    if live:
        report["model"] = os.environ["ANTHROPIC_MODEL"]
    return report


def compare(current, previous):
    if any(current.get(key) != previous.get(key) for key in ("mode", "dataset")):
        raise ValueError("Compare runs from the same dataset and mode")
    return {name: sorted({row["id"] for row in previous["results"][name]["cases"] if row["correct"]}
                         - {row["id"] for row in report["cases"] if row["correct"]})
            for name, report in current["results"].items()}
