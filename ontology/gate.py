import json
from contextlib import ExitStack, contextmanager
from hashlib import sha256
from pathlib import Path
from time import perf_counter

import duckdb

from ontology.data import open_db
from ontology.semantic import equivalent, execute_plan, scoped, source_revision, validate, visible


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_suite(path):
    cases = json.loads(Path(path).read_text())
    if not isinstance(cases, list) or not cases:
        raise ValueError("A gate suite must contain saved plans")
    ids = set()
    for case in cases:
        if case["id"] in ids or case["role"] not in {"analyst", "admin"}:
            raise ValueError("Gate case IDs must be unique and roles must be recognized")
        plan = case["plan"]
        where = plan.get("where")
        if not isinstance(plan.get("metric"), str) or set(plan) - {"metric", "where"}:
            raise ValueError("Gate case must contain a metric and an optional equality filter")
        if where is not None and (not isinstance(where, dict) or set(where) != {"column", "value"}
                                  or not isinstance(where["column"], str)):
            raise ValueError("Invalid gate filter")
        ids.add(case["id"])
    return cases


@contextmanager
def pinned_source(path):
    path = Path(path).resolve()
    with open_db(path, True):
        revision = source_revision(path)
        if revision is None:
            raise ValueError("Checkpoint the source database before running or publishing the gate")

        def check():
            if source_revision(path) != revision:
                raise ValueError("Source changed during the gate operation. Run it again.")
        with path.open("rb") as stream:
            fingerprint = sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                fingerprint.update(block)
        check()
        yield fingerprint.hexdigest(), check


def execution_signature(objects, plan):
    metric = next((obj for obj in objects if obj["kind"] == "metric"
                   and obj["name"] == plan["metric"]), None)
    if metric is None:
        return None
    where = plan.get("where")
    if where is not None and not any(
        obj["kind"] == "property" and obj["table"] == metric["table"]
        and obj["column"] == where["column"] for obj in objects
    ):
        return None
    # Only these fields reach the compiler. Role visibility and every physical
    # dependency are resolved on both snapshots before comparing signatures.
    return {key: metric[key] for key in ("table", "op", "column", "joins", "where") if key in metric}


def replay(path, before, after, cases, *, full=False):
    started = perf_counter()
    roles = {case["role"] for case in cases}
    views = [{role: visible(snapshot, role) for role in roles} for snapshot in (before, after)]
    fallback = any(case.get("dependencies") != "structured_v1" for case in cases)
    selected = list(cases) if full or fallback else [
        case for case in cases if execution_signature(views[0][case["role"]], case["plan"])
        != execution_signature(views[1][case["role"]], case["plan"])
    ]
    old_objects = {(obj["kind"], obj["name"]): obj for obj in before}
    invariants = [dict(kind=obj["kind"], name=obj["name"]) for obj in after
                  if obj != old_objects.get((obj["kind"], obj["name"])) and not validate(path, obj)["ok"]]
    findings = []
    with ExitStack() as stack:
        sessions = {}

        def execute(index, case):
            role, plan = case["role"], case["plan"]
            objects = views[index][role]
            if execution_signature(objects, plan) is None:
                return "denied", None
            try:
                if (index, role) not in sessions:
                    sessions[index, role] = stack.enter_context(scoped(path, objects))
                result = execute_plan(sessions[index, role], objects, plan)
                return result["status"], result.get("value")
            except (KeyError, ValueError, TypeError, duckdb.Error):
                return "error", None

        for case in selected:
            old, new = (execute(index, case) for index in (0, 1))
            evidence = dict(id=case["id"], role=case["role"])
            if "error" in (old[0], new[0]):
                findings.append(dict(evidence, status="execution_error"))
            elif old[0] != new[0]:
                # Both directions are review blockers. Never attach values, SQL,
                # filters or column names to access findings.
                findings.append(dict(evidence, status="access_changed",
                                     before=old[0], after=new[0]))
            elif old[0] == "ok" and not equivalent(old[1], new[1]):
                findings.append(dict(evidence, status="answer_changed", metric=case["plan"]["metric"],
                                     before=old[1], after=new[1]))
    return dict(mode="full" if full else ("fallback" if fallback else "targeted"),
                cases=len(cases), checked=len(selected), elapsed_ms=round((perf_counter()-started)*1000, 2),
                blocked=bool(invariants) or any(row["status"] != "answer_changed" for row in findings),
                failed_invariants=invariants, findings=findings)


def compare_replays(path, before, after, cases):
    targeted = replay(path, before, after, cases)
    full = replay(path, before, after, cases, full=True)
    target_findings = {row["id"]: row for row in targeted["findings"]}
    missed = []
    for row in full["findings"]:
        observed = target_findings.get(row["id"])
        if observed == row:
            continue
        if (observed and row["status"] == observed["status"] == "answer_changed"
                and row["role"] == observed["role"] and row["metric"] == observed["metric"]
                and all(equivalent(row[key], observed[key]) for key in ("before", "after"))):
            continue
        missed.append(row["id"])
    return dict(targeted=targeted, full=full, missed=missed)
