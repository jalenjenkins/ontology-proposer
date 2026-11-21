import argparse
import json
from pathlib import Path

from ontology.benchmark import compare, evaluate, holdout_gold
from ontology.data import demo, load_csv, profile
from ontology.proposer import fixture_proposal, propose, synthea_proposal
from ontology.query import live_answer
from ontology.semantic import execute_plan, scoped, visible
from ontology.store import Store


def main():
    parser = argparse.ArgumentParser(description="Review and query a versioned ontology")
    parser.add_argument("--db", default="demo.duckdb")
    parser.add_argument("--store", default="review.sqlite")
    parser.add_argument("--gate-cases", type=Path, help="Saved plans required for updates to published ontologies")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("fixture", "profile", "review", "publish", "serve", "demo"):
        sub = commands.add_parser(name)
        if name in {"review", "publish"}:
            sub.add_argument("batch", type=int)
        if name == "profile":
            sub.add_argument("--exhaustive", action="store_true")
        if name == "serve":
            sub.add_argument("--port", type=int, default=8765)
    commands.add_parser("load").add_argument("csv_dir")
    proposal = commands.add_parser("propose").add_mutually_exclusive_group()
    proposal.add_argument("--offline", action="store_true", help="Use fixture reference proposal")
    proposal.add_argument("--synthea-reference", action="store_true", help="Use Synthea reference proposal")
    decision = commands.add_parser("decide")
    decision.add_argument("change", type=int)
    decision.add_argument("decision", choices=("approve", "reject"))
    query = commands.add_parser("query")
    query.add_argument("request", help="Structured plan JSON or natural language with --live")
    query.add_argument("--role", choices=("admin", "analyst"), default="analyst")
    query.add_argument("--verify", action="store_true")
    query.add_argument("--live", action="store_true")
    query.add_argument("--version", type=int)
    query.add_argument("--cache-dir", type=Path, default=Path(".ontology-cache"))
    benchmark = commands.add_parser("eval")
    benchmark.add_argument("--live", action="store_true")
    dataset = benchmark.add_mutually_exclusive_group()
    dataset.add_argument("--synthea", action="store_true")
    dataset.add_argument("--holdout", action="store_true", help="Use the frozen Synthea holdout")
    benchmark.add_argument("--check-gold", action="store_true", help="Check holdout against source and analyst data")
    benchmark.add_argument("--output", type=Path)
    benchmark.add_argument("--baseline", type=Path)
    gate = commands.add_parser("gate")
    gate.add_argument("batch", type=int)
    action = gate.add_mutually_exclusive_group()
    action.add_argument("--full", action="store_true")
    action.add_argument("--acknowledge", metavar="EVIDENCE_KEY")
    gate_demo = commands.add_parser("gate-demo")
    gate_demo.add_argument("scenario", choices=("cost", "permissions"))
    commands.add_parser("gate-compare")
    args = parser.parse_args()
    cmd = args.command
    if cmd == "fixture":
        return demo(args.db)
    if cmd == "load":
        return load_csv(args.csv_dir, args.db)
    if cmd == "profile":
        return profile(args.db, shortlist=not args.exhaustive)
    store = Store(args.store)
    if cmd == "propose":
        adapter = fixture_proposal if args.offline else synthea_proposal if args.synthea_reference else propose
        objects = adapter(args.db)
        return dict(batch=store.stage(args.db, objects))
    if cmd == "review":
        return store.changes(args.batch)
    if cmd == "decide":
        store.decide(args.change, args.decision)
        return dict(decision=args.decision)
    if cmd == "publish":
        return dict(version=store.publish(args.batch, args.db, args.gate_cases))
    if cmd == "gate":
        if args.acknowledge:
            store.acknowledge_gate(args.batch, args.acknowledge)
            return dict(acknowledged=True)
        return store.check_gate(args.batch, args.db, args.gate_cases, args.full)
    if cmd in {"gate-demo", "gate-compare"}:
        from ontology.gate_demo import compare_demo, stage_demo
        if cmd == "gate-compare":
            return compare_demo(args.db)
        return stage_demo(args.db, store, args.scenario)
    if cmd == "query":
        version, snapshot = store.versioned_snapshot(args.version)
        objects = visible(snapshot, args.role)
        with scoped(args.db, objects, cache_dir=args.cache_dir, version=version, role=args.role) as db:
            if args.live:
                return live_answer(db, args.request, objects, args.verify)
            return execute_plan(db, objects, json.loads(args.request), args.verify)
    if cmd == "eval":
        version, snapshot = store.versioned_snapshot()
        if args.check_gold:
            if not args.holdout or args.live:
                raise ValueError("--check-gold requires --holdout without --live")
            cases = holdout_gold(args.db, visible(snapshot, "analyst"))
            return dict(dataset="synthea_holdout", gold_cases_verified=len(cases), ontology_version=version)
        results = evaluate(args.db, snapshot, args.live, args.synthea, args.holdout)
        if args.holdout:
            results["ontology_version"] = version
        if args.baseline:
            results["regressions"] = compare(results, json.loads(args.baseline.read_text()))
        if args.output:
            args.output.write_text(json.dumps(results, indent=2, default=str) + "\n")
        if args.holdout:
            return {key: value for key, value in results.items() if key != "results"} | {
                "results": {name: {k: v for k, v in summary.items() if k != "cases"}
                            for name, summary in results["results"].items()}
            }
        return {name: {k: v for k, v in report.items() if k != "cases"}
                for name, report in results["results"].items()} | {"regressions": results.get("regressions", {})}
    if cmd == "serve":
        from ontology.server import serve
        return serve(store, args.port, args.db, args.gate_cases)
    if cmd == "demo":
        demo(args.db)
        batch = store.stage(args.db, fixture_proposal(args.db))
        return dict(batch=batch, changes=len(store.changes(batch)),
                    review=f"http://127.0.0.1:8765/?batch={batch}")


if __name__ == "__main__":
    try:
        result = main()
        if result is not None:
            print(json.dumps(result, indent=2, default=str))
    except (KeyError, ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from exc
